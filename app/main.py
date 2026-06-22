"""Hermes Dispatch bridge — FastAPI app exposing a mobile-friendly /v1 API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .auth import require_auth
from .classify import classify_schedule
from .config import settings
from .crypto import decode_key
from .hermes_client import HermesClient
from .hub import StreamHub
from .inbox import InboxService, InboxWatcher
from .models import (
    AlertsRequest,
    AlertsResponse,
    ApproveRequest,
    ArchiveRequest,
    ClarifyRequest,
    InboxContent,
    InboxItem,
    McpServer,
    Message,
    ModelOption,
    ModelsResponse,
    ProfileInfo,
    ProfilesResponse,
    PushKeyRequest,
    PushRegisterRequest,
    Schedule,
    ScheduleUpdateRequest,
    SetModelRequest,
    StartTaskRequest,
    StartTaskResponse,
    SteerRequest,
    Task,
    TranscribeResponse,
)
from .push import Pusher
from .registry import EndpointRegistry
from .store import StateStore

log = logging.getLogger("hermes-dispatch-bridge")

_CRON_ACTIONS = {"pause", "resume", "run", "delete"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.auth_enabled:
        log.warning("BRIDGE_TOKEN is not set — the mobile API is UNAUTHENTICATED (dev only).")
    http = httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True)
    hermes = HermesClient(settings.hermes_url, settings.hermes_token, http)
    store = StateStore(settings.state_path)
    registry = EndpointRegistry(store)
    static_endpoint = (
        f"{settings.ntfy_base_url.rstrip('/')}/{settings.ntfy_topic}"
        if settings.ntfy_topic
        else None
    )
    publisher = Pusher(
        http, registry, static_endpoint, get_key=lambda: decode_key(store.get("push_key", ""))
    )
    hub = StreamHub(hermes, publisher)
    inbox = InboxService(settings.hermes_root)
    watcher = InboxWatcher(inbox, store, publisher, settings.inbox_poll_seconds)
    watcher.start()
    app.state.http = http
    app.state.hermes = hermes
    app.state.store = store
    app.state.registry = registry
    app.state.hub = hub
    app.state.inbox = inbox
    app.state.watcher = watcher
    try:
        yield
    finally:
        await watcher.stop()
        await hub.shutdown()
        await http.aclose()


app = FastAPI(title="Hermes Dispatch Bridge", version="0.1.0", lifespan=lifespan)


def get_hermes(request: Request) -> HermesClient:
    return request.app.state.hermes


def get_hub(request: Request) -> StreamHub:
    return request.app.state.hub


def get_registry(request: Request) -> EndpointRegistry:
    return request.app.state.registry


def get_inbox(request: Request) -> InboxService:
    return request.app.state.inbox


def get_watcher(request: Request) -> InboxWatcher:
    return request.app.state.watcher


def get_store(request: Request) -> StateStore:
    return request.app.state.store


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
    archived: str = Query(default="exclude"),  # exclude | only | include
) -> list[Task]:
    rows = await hermes.sessions(profile=x_hermes_profile, archived=archived)
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


def _clean_snippet(text: str) -> str:
    return (text or "").replace(">>>", "").replace("<<<", "").strip()


@router.get("/tasks/search", response_model=list[Task])
async def search_tasks(
    q: str,
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> list[Task]:
    rows = await hermes.search_sessions(q, profile=x_hermes_profile)
    out: list[Task] = []
    for r in rows:
        sid = r.get("session_id") or r.get("id") or ""
        snippet = _clean_snippet(r.get("snippet", ""))
        out.append(
            Task(
                id=sid,
                title=snippet[:80] or "Session",
                status=r.get("source") or "",
                model=r.get("model"),
                updated_at=float(r.get("session_started") or 0),
            )
        )
    return out


@router.post("/tasks/{session_id}/archive")
async def archive_task(
    session_id: str,
    body: ArchiveRequest,
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> dict:
    await hermes.set_session_archived(session_id, body.archived, profile=x_hermes_profile)
    return {"ok": True, "archived": body.archived}


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
            name=body.message[:80], prompt=body.message, schedule=schedule.cron,
            profile=x_hermes_profile,
        )
        return StartTaskResponse(kind="cron", cron=schedule.cron)

    # Open a server-held run over the dashboard WS; session id == app stream id.
    sid = await hub.start_run(
        body.session_id, body.message, profile=x_hermes_profile, images=body.images
    )
    return StartTaskResponse(kind="oneshot", session_id=sid, stream_id=sid)


@router.get("/tasks/{session_id}/messages", response_model=list[Message])
async def task_messages(
    session_id: str,
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> list[Message]:
    out: list[Message] = []
    for m in await hermes.messages(session_id, profile=x_hermes_profile):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            text = "\n".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = str(content or "")
        if text.strip():
            out.append(Message(role=role, text=text))
    return out


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


@router.post("/tasks/{stream_id}/approve")
async def approve_task(
    stream_id: str,
    body: ApproveRequest,
    hub: StreamHub = Depends(get_hub),
) -> dict:
    await hub.approve(stream_id, body.choice, body.all)
    return {"ok": True}


@router.post("/tasks/{stream_id}/clarify")
async def clarify_task(
    stream_id: str,
    body: ClarifyRequest,
    hub: StreamHub = Depends(get_hub),
) -> dict:
    await hub.clarify(stream_id, body.answer)
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
            prompt=str(r.get("prompt") or ""),
            paused=bool(
                r.get("paused_at") or r.get("state") == "paused" or not r.get("enabled", True)
            ),
            next_run=_epoch(r.get("next_run") or r.get("next_run_at")),
            last_run=_epoch(r.get("last_run") or r.get("last_run_at")),
        )
        for r in rows
    ]


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdateRequest,
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> dict:
    updates: dict[str, str] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.prompt is not None:
        updates["prompt"] = body.prompt
    if body.schedule is not None and body.schedule.strip():
        updates["schedule"] = body.schedule.strip()
    if not updates:
        raise HTTPException(400, "No fields to update")
    await hermes.update_cron(schedule_id, updates, profile=x_hermes_profile)
    return {"ok": True}


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


@router.get("/push/info")
async def push_info() -> dict:
    """How to receive lock-screen alerts: subscribe to this ntfy topic."""
    return {
        "configured": settings.push_enabled,
        "base_url": settings.ntfy_base_url,
        "topic": settings.ntfy_topic,
    }


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


@router.post("/push/key")
async def set_push_key(
    body: PushKeyRequest,
    store: StateStore = Depends(get_store),
) -> dict:
    """Register (or clear) the E2EE key used to encrypt push payloads.

    A blank key disables encryption. A non-empty key must be a base64 32-byte
    AES-256 key; the bridge stores it and encrypts device-push bodies with it.
    """
    if body.key and decode_key(body.key) is None:
        raise HTTPException(400, "key must be a base64-encoded 32-byte value")
    store.set("push_key", body.key or "")
    return {"ok": True, "encrypted": bool(body.key)}


@router.get("/inbox", response_model=list[InboxItem])
async def list_inbox(
    inbox: InboxService = Depends(get_inbox),
    x_hermes_profile: str | None = Header(default=None),
) -> list[InboxItem]:
    return [
        InboxItem(
            id=i.item_id,
            profile=i.profile,
            job_id=i.job_id,
            job_name=i.job_name,
            title=i.title,
            status=i.status,
            created_at=i.created_at,
            size=i.size,
            snippet=i.snippet,
        )
        for i in inbox.list_items(profile=x_hermes_profile)
    ]


@router.get("/inbox/item", response_model=InboxContent)
async def read_inbox(
    id: str,
    inbox: InboxService = Depends(get_inbox),
) -> InboxContent:
    found = inbox.read_item(id)
    if found is None:
        raise HTTPException(404, "Inbox item not found")
    i, content = found
    return InboxContent(
        id=i.item_id,
        profile=i.profile,
        job_id=i.job_id,
        job_name=i.job_name,
        title=i.title,
        status=i.status,
        created_at=i.created_at,
        size=i.size,
        snippet=i.snippet,
        output=i.output,
        content=content,
    )


@router.get("/inbox/alerts", response_model=AlertsResponse)
async def get_alerts(watcher: InboxWatcher = Depends(get_watcher)) -> AlertsResponse:
    return AlertsResponse(
        job_ids=watcher.alerts(),
        alert_on_failures=watcher.alert_on_failures(),
    )


@router.put("/inbox/alerts", response_model=AlertsResponse)
async def set_alerts(
    body: AlertsRequest,
    watcher: InboxWatcher = Depends(get_watcher),
) -> AlertsResponse:
    job_ids = watcher.set_alerts(body.job_ids)
    failures = (
        watcher.set_alert_on_failures(body.alert_on_failures)
        if body.alert_on_failures is not None
        else watcher.alert_on_failures()
    )
    return AlertsResponse(job_ids=job_ids, alert_on_failures=failures)


@router.post("/audio/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    hermes: HermesClient = Depends(get_hermes),
) -> TranscribeResponse:
    """Server-side speech-to-text: forward uploaded audio to the dashboard."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "multipart field 'file' (audio) is required")
    data = await upload.read()
    filename = getattr(upload, "filename", None) or "audio.m4a"
    text = await hermes.transcribe(data, filename)
    return TranscribeResponse(text=text)


@router.get("/models", response_model=ModelsResponse)
async def list_models(
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> ModelsResponse:
    opts = await hermes.model_options(profile=x_hermes_profile)
    providers = opts.get("providers", []) if isinstance(opts, dict) else []
    # Offer models from the active provider (and any user-defined ones).
    out: list[ModelOption] = []
    for p in providers:
        if not (p.get("is_current") or p.get("is_user_defined")):
            continue
        slug = p.get("slug", "")
        for m in p.get("models", []):
            out.append(ModelOption(provider=slug, model=m))
    return ModelsResponse(models=out, current=opts.get("model"))


@router.post("/models/set")
async def set_model(
    body: SetModelRequest,
    hermes: HermesClient = Depends(get_hermes),
    x_hermes_profile: str | None = Header(default=None),
) -> dict:
    provider = body.provider or "nous"
    if x_hermes_profile:
        await hermes.set_profile_model(x_hermes_profile, provider, body.model)
    else:
        await hermes.set_global_model(provider, body.model)
    return {"ok": True, "model": body.model}


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
