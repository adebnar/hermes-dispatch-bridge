"""Hermes Dispatch bridge — FastAPI app exposing a mobile-friendly /v1 API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .auth import require_auth
from .classify import classify_schedule
from .config import settings
from .hermes_client import HermesClient
from .hub import StreamHub
from .models import (
    McpServer,
    Schedule,
    StartTaskRequest,
    StartTaskResponse,
    Task,
)
from .push import NtfyPublisher

log = logging.getLogger("hermes-dispatch-bridge")

_CRON_ACTIONS = {"pause", "resume", "run", "delete"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.auth_enabled:
        log.warning("BRIDGE_TOKEN is not set — the mobile API is UNAUTHENTICATED (dev only).")
    http = httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True)
    hermes = HermesClient(settings.hermes_webui_url, settings.hermes_webui_password, http)
    publisher = NtfyPublisher(settings.ntfy_base_url, settings.ntfy_topic, http)
    hub = StreamHub(hermes, publisher)
    app.state.http = http
    app.state.hermes = hermes
    app.state.hub = hub
    try:
        yield
    finally:
        await hub.shutdown()
        await http.aclose()


app = FastAPI(title="Hermes Dispatch Bridge", version="0.1.0", lifespan=lifespan)


def get_hermes(request: Request) -> HermesClient:
    return request.app.state.hermes


def get_hub(request: Request) -> StreamHub:
    return request.app.state.hub


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "push": settings.push_enabled, "auth": settings.auth_enabled}


# All /v1 routes require the bridge token (when configured).
router = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])


@router.get("/auth/check")
async def auth_check() -> dict:
    return {"ok": True}


@router.get("/tasks", response_model=list[Task])
async def list_tasks(hermes: HermesClient = Depends(get_hermes)) -> list[Task]:
    rows = await hermes.sessions()
    return [
        Task(
            id=r.get("session_id", ""),
            title=r.get("title") or "Untitled task",
            status=r.get("status") or "",
            model=r.get("model"),
            updated_at=float(r.get("updated_at") or 0),
        )
        for r in rows
    ]


@router.post("/tasks", response_model=StartTaskResponse)
async def start_task(
    body: StartTaskRequest,
    hermes: HermesClient = Depends(get_hermes),
    hub: StreamHub = Depends(get_hub),
) -> StartTaskResponse:
    # Decide one-shot vs recurring up front (the "it became a cron job" behavior).
    schedule = classify_schedule(body.message)
    if schedule.is_cron and schedule.cron:
        await hermes.create_cron(name=body.message[:80], schedule=schedule.cron)
        return StartTaskResponse(kind="cron", cron=schedule.cron)

    session_id = body.session_id or await hermes.create_session()
    stream_id = await hermes.start_chat(session_id, body.message, body.model)
    hub.ensure_consumer(stream_id, session_id)  # server holds the run + pushes
    return StartTaskResponse(kind="oneshot", session_id=session_id, stream_id=stream_id)


@router.get("/tasks/{stream_id}/events")
async def task_events(
    stream_id: str,
    hub: StreamHub = Depends(get_hub),
) -> StreamingResponse:
    hub.ensure_consumer(stream_id, None)
    return StreamingResponse(hub.subscribe(stream_id), media_type="text/event-stream")


@router.get("/schedules", response_model=list[Schedule])
async def list_schedules(hermes: HermesClient = Depends(get_hermes)) -> list[Schedule]:
    rows = await hermes.crons()
    return [
        Schedule(
            id=r.get("id", ""),
            name=r.get("name") or "Scheduled task",
            cron=r.get("schedule") or "",
            paused=not r.get("enabled", True),
            next_run=r.get("next_run_at"),
            last_run=r.get("last_run_at"),
        )
        for r in rows
    ]


@router.post("/schedules/{schedule_id}/{action}")
async def schedule_action(
    schedule_id: str,
    action: str,
    hermes: HermesClient = Depends(get_hermes),
) -> dict:
    if action not in _CRON_ACTIONS:
        raise HTTPException(400, f"Unknown action '{action}'")
    await hermes.cron_action(action, schedule_id)
    return {"ok": True}


@router.get("/mcp", response_model=list[McpServer])
async def list_mcp(hermes: HermesClient = Depends(get_hermes)) -> list[McpServer]:
    rows = await hermes.mcp_servers()
    return [
        McpServer(
            name=r.get("name", ""),
            status=r.get("status"),
            tools_count=r.get("tools_count"),
        )
        for r in rows
    ]


app.include_router(router)
