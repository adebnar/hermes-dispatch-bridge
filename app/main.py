"""Hermes Dispatch bridge — FastAPI app exposing a mobile-friendly /v1 API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from .auth import require_auth
from .classify import classify_schedule
from .config import settings
from .hermes_client import HermesClient
from .hub import StreamHub
from .models import (
    McpServer,
    ProfileInfo,
    ProfilesResponse,
    PushRegisterRequest,
    Schedule,
    StartTaskRequest,
    StartTaskResponse,
    SteerRequest,
    Task,
)
from .push import Pusher
from .registry import EndpointRegistry

log = logging.getLogger("hermes-dispatch-bridge")

_CRON_ACTIONS = {"pause", "resume", "run", "delete"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.auth_enabled:
        log.warning("BRIDGE_TOKEN is not set — the mobile API is UNAUTHENTICATED (dev only).")
    http = httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True)
    hermes = HermesClient(settings.hermes_url, settings.hermes_token, http)
    registry = EndpointRegistry()
    static_endpoint = (
        f"{settings.ntfy_base_url.rstrip('/')}/{settings.ntfy_topic}"
        if settings.ntfy_topic
        else None
    )
    publisher = Pusher(http, registry, static_endpoint)
    hub = StreamHub(hermes, publisher)
    app.state.http = http
    app.state.hermes = hermes
    app.state.registry = registry
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


def get_registry(request: Request) -> EndpointRegistry:
    return request.app.state.registry


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "push": settings.push_enabled, "auth": settings.auth_enabled}


# All /v1 routes require the bridge token (when configured).
router = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])


@router.get("/auth/check")
async def auth_check() -> dict:
    return {"ok": True}


@router.get("/profiles", response_model=ProfilesResponse)
async def list_profiles(hermes: HermesClient = Depends(get_hermes)) -> ProfilesResponse:
    rows = await hermes.profiles()
    active = await hermes.active_profile()
    return ProfilesResponse(
        profiles=[
            ProfileInfo(
                name=r.get("name", ""),
                model=r.get("model"),
                is_default=bool(r.get("is_default")),
            )
            for r in rows
        ],
        active=active,
    )


@router.get("/tasks", response_model=list[Task])
async def list_tasks(
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> list[Task]:
    rows = await hermes.sessions(profile=x_hermes_profile)
    return [
        Task(
            id=r.get("id") or r.get("session_id", ""),
            title=r.get("title") or _first_message(r) or "Task",
            status=r.get("status") or r.get("source") or "",
            model=r.get("model"),
            updated_at=float(r.get("updated_at") or r.get("created_at") or 0),
        )
        for r in rows
    ]


def _first_message(session: dict) -> str:
    msgs = session.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
                return str(m["content"])[:80]
    return ""


@router.post("/tasks", response_model=StartTaskResponse)
async def start_task(
    body: StartTaskRequest,
    hermes: HermesClient = Depends(get_hermes),
    hub: StreamHub = Depends(get_hub),
    x_hermes_profile: str | None = Header(default=None),
) -> StartTaskResponse:
    # Decide one-shot vs recurring up front (the "it became a cron job" behavior).
    schedule = classify_schedule(body.message)
    if schedule.is_cron and schedule.cron:
        await hermes.create_cron(
            name=body.message[:80], prompt=body.message, schedule=schedule.cron
        )
        return StartTaskResponse(kind="cron", cron=schedule.cron)

    # Open a server-held run over the dashboard WS; session id == app stream id.
    sid = await hub.start_run(
        body.session_id, body.message, profile=x_hermes_profile, images=body.images
    )
    return StartTaskResponse(kind="oneshot", session_id=sid, stream_id=sid)


@router.get("/tasks/{stream_id}/events")
async def task_events(
    stream_id: str,
    hub: StreamHub = Depends(get_hub),
) -> StreamingResponse:
    return StreamingResponse(hub.subscribe(stream_id), media_type="text/event-stream")


@router.post("/tasks/{stream_id}/cancel")
async def cancel_task(
    stream_id: str,
    hub: StreamHub = Depends(get_hub),
) -> dict:
    await hub.cancel(stream_id)
    return {"ok": True}


@router.post("/tasks/{stream_id}/steer")
async def steer_task(
    stream_id: str,
    body: SteerRequest,
    hub: StreamHub = Depends(get_hub),
) -> dict:
    await hub.steer(stream_id, body.message)
    return {"ok": True}


def _cron_expr(r: dict) -> str:
    sched = r.get("schedule_display") or r.get("schedule") or r.get("cron")
    if isinstance(sched, dict):
        return str(sched.get("expr") or sched.get("display") or "")
    return str(sched or "")


def _epoch(value) -> float | None:
    """Coerce a dashboard time (ISO string or epoch) to epoch seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


@router.get("/schedules", response_model=list[Schedule])
async def list_schedules(
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> list[Schedule]:
    rows = await hermes.crons()
    if x_hermes_profile:
        rows = [
            r for r in rows
            if (r.get("profile_name") or r.get("profile")) == x_hermes_profile
        ]
    return [
        Schedule(
            id=r.get("id", ""),
            name=r.get("name") or "Scheduled task",
            cron=_cron_expr(r),
            paused=bool(
                r.get("paused_at") or r.get("state") == "paused" or not r.get("enabled", True)
            ),
            next_run=_epoch(r.get("next_run") or r.get("next_run_at")),
            last_run=_epoch(r.get("last_run") or r.get("last_run_at")),
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


@router.post("/push/register")
async def register_push(
    body: PushRegisterRequest,
    registry: EndpointRegistry = Depends(get_registry),
) -> dict:
    registry.add(body.endpoint)
    return {"ok": True, "count": len(registry)}


@router.post("/push/unregister")
async def unregister_push(
    body: PushRegisterRequest,
    registry: EndpointRegistry = Depends(get_registry),
) -> dict:
    registry.remove(body.endpoint)
    return {"ok": True, "count": len(registry)}


@router.get("/mcp", response_model=list[McpServer])
async def list_mcp(hermes: HermesClient = Depends(get_hermes)) -> list[McpServer]:
    rows = await hermes.mcp_servers()

    def _count(r: dict) -> int | None:
        return len(r["tools"]) if isinstance(r.get("tools"), list) else r.get("tools_count")

    return [
        McpServer(
            name=r.get("name", ""),
            status=r.get("status") or ("enabled" if r.get("enabled") else "disabled"),
            tools_count=_count(r),
        )
        for r in rows
    ]


app.include_router(router)
