"""Map agent stream events to push messages and publish them to ntfy."""

from __future__ import annotations

import httpx

from .models import PushMessage
from .sse import SseEvent


def to_push(
    event: SseEvent, *, session_id: str | None, title: str = "Hermes"
) -> PushMessage | None:
    """Convert a stream event into a background push, or None to skip it.

    Token/reasoning deltas are intentionally dropped so the lock-screen update
    isn't spammed; only meaningful status/tool/terminal transitions are pushed.
    """
    base = dict(sessionId=session_id, title=title)
    if event.type == "tool":
        return PushMessage(**base, status=f"Using {event.name}".strip())
    if event.type == "status":
        return PushMessage(**base, status=event.text)
    if event.type == "approval":
        return PushMessage(**base, status="Approval needed")
    if event.type == "completed":
        return PushMessage(**base, status="Complete", done=True)
    if event.type == "error":
        return PushMessage(**base, status=f"Error: {event.text}"[:120], done=True)
    if event.type == "interrupted":
        return PushMessage(**base, status="Stopped", done=True)
    return None


class NtfyPublisher:
    """Publishes :class:`PushMessage` JSON to an ntfy topic (UnifiedPush endpoint)."""

    def __init__(self, base_url: str, topic: str, client: httpx.AsyncClient):
        self._url = f"{base_url.rstrip('/')}/{topic}"
        self._enabled = bool(topic)
        self._client = client

    async def publish(self, message: PushMessage) -> None:
        if not self._enabled:
            return
        try:
            await self._client.post(
                self._url,
                content=message.model_dump_json(),
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        except httpx.HTTPError:
            # Push is best-effort; never let a delivery failure break a run.
            pass
