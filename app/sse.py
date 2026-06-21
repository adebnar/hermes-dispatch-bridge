"""Pure SSE frame parsing + normalization (mirrors the Android SseParser)."""

from __future__ import annotations

import json
from dataclasses import dataclass

# Terminal event types after which a run is finished.
TERMINAL = {"completed", "error", "interrupted"}


@dataclass(frozen=True)
class SseEvent:
    type: str  # token|tool|status|reasoning|approval|clarify|completed|error|interrupted|unknown
    text: str = ""
    name: str = ""  # tool name
    raw: str = ""


def _obj(data: str) -> dict:
    try:
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def parse(event_name: str | None, data: str) -> SseEvent:
    """Map a raw SSE frame to a normalized :class:`SseEvent`. Never raises."""
    obj = _obj(data)
    etype = (event_name or obj.get("type") or "").strip().lower()

    if etype in ("token", "text", "delta", "message"):
        return SseEvent("token", text=str(obj.get("text") or obj.get("content") or data), raw=data)
    if etype in ("tool", "tool_use", "tool_call"):
        return SseEvent(
            "tool",
            name=str(obj.get("name") or obj.get("tool_name") or "tool"),
            text=str(obj.get("preview") or ""),
            raw=data,
        )
    if etype in ("status", "worker_started", "assistant_started"):
        return SseEvent("status", text=str(obj.get("text") or obj.get("label") or etype), raw=data)
    if etype in ("reasoning", "thinking"):
        return SseEvent("reasoning", text=str(obj.get("text") or data), raw=data)
    if etype == "approval":
        return SseEvent("approval", text=str(obj.get("command") or ""), raw=data)
    if etype == "clarify":
        return SseEvent(
            "clarify", text=str(obj.get("question") or obj.get("prompt") or ""), raw=data
        )
    if etype in ("completed", "done", "finished"):
        return SseEvent("completed", raw=data)
    if etype in ("interrupted", "cancelled"):
        return SseEvent("interrupted", raw=data)
    if etype == "error":
        return SseEvent(
            "error", text=str(obj.get("message") or obj.get("error") or "error"), raw=data
        )
    return SseEvent("unknown", text=etype, raw=data)


def is_terminal(event: SseEvent) -> bool:
    return event.type in TERMINAL


def format_frame(event_name: str | None, data: str) -> str:
    """Re-serialize a frame as a single SSE wire chunk."""
    prefix = f"event: {event_name}\n" if event_name else ""
    return f"{prefix}data: {data}\n\n"
