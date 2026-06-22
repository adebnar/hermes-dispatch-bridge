"""Map hermes-agent dashboard WebSocket events to the bridge's normalized
:class:`SseEvent`, and back to app-facing SSE frames.

Dashboard events arrive as JSON-RPC notifications:
``{"method":"event","params":{"type":<t>,"session_id":<sid>,"payload":{…}}}``.
Event vocabulary verified live against hermes-agent v0.16.0 — see
docs/PHASE0-FINDINGS.md. Keeping this pure makes the protocol mapping fully
unit-testable without a live socket.
"""

from __future__ import annotations

import json

from .sse import SseEvent


def map_event(etype: str, payload: dict | None) -> SseEvent:
    """Normalize one dashboard event into an :class:`SseEvent`."""
    p = payload or {}
    t = (etype or "").strip().lower()

    if t == "message.delta":
        return SseEvent("token", text=str(p.get("text", "")))
    if t in ("reasoning.delta", "reasoning.available", "thinking.delta"):
        return SseEvent("reasoning", text=str(p.get("text", "")))
    if t in ("tool.complete", "tool.completed", "tool.call_end"):
        # Completion of a single tool is not worth a push line on its own.
        return SseEvent("status", text="")
    if t.startswith("tool."):
        # tool.start/started/call/generating/progress/… → surface the tool.
        return SseEvent(
            "tool",
            name=str(p.get("name") or p.get("tool") or "tool"),
            text=str(p.get("preview") or p.get("context") or ""),
        )
    if t == "session.info":
        return SseEvent("status", text="")  # info only; dropped from push
    if t == "message.start":
        return SseEvent("status", text="")
    if t in ("message.complete", "session.complete", "turn.complete"):
        return SseEvent("completed", raw=json.dumps(p))
    if t in ("session.interrupt", "session.interrupted", "cancelled"):
        return SseEvent("interrupted")
    if t in ("approval.request", "approval"):
        # command in .text, description in .name (see to_app_frame).
        return SseEvent("approval", text=str(p.get("command") or ""),
                        name=str(p.get("description") or ""))
    if t in ("clarify.request", "clarify"):
        return SseEvent("clarify", text=str(p.get("question") or p.get("prompt") or ""))
    if t.endswith(".error") or t == "error":
        return SseEvent("error", text=str(p.get("message") or p.get("text") or "error"))
    return SseEvent("unknown", text=t)


def to_app_frame(ev: SseEvent) -> tuple[str, str]:
    """Serialize a normalized event into an (event_name, json_data) pair for the
    app-facing SSE stream — matching the Android `SseParser` vocabulary."""
    if ev.type == "token":
        return "token", json.dumps({"text": ev.text})
    if ev.type == "tool":
        return "tool", json.dumps({"name": ev.name, "preview": ev.text})
    if ev.type == "reasoning":
        return "reasoning", json.dumps({"text": ev.text})
    if ev.type == "status":
        return "status", json.dumps({"text": ev.text})
    if ev.type == "completed":
        return "completed", ev.raw or "{}"
    if ev.type == "error":
        return "error", json.dumps({"message": ev.text})
    if ev.type == "interrupted":
        return "interrupted", "{}"
    if ev.type == "approval":
        return "approval", json.dumps({"command": ev.text, "description": ev.name})
    if ev.type == "clarify":
        return "clarify", json.dumps({"question": ev.text})
    return "unknown", json.dumps({"type": ev.text})
