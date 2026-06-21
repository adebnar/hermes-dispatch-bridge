"""Map agent stream events to push messages and publish them to ntfy."""

from __future__ import annotations

import httpx

from .models import PushMessage
from .registry import EndpointRegistry
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


class Pusher:
    """Publishes :class:`PushMessage` JSON to every registered device endpoint
    plus an optional statically-configured ntfy topic."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        registry: EndpointRegistry,
        static_endpoint: str | None = None,
    ):
        self._client = client
        self._registry = registry
        self._static = static_endpoint

    def targets(self) -> list[str]:
        urls = set(self._registry.all())
        if self._static:
            urls.add(self._static)
        return sorted(urls)

    async def publish(self, message: PushMessage) -> None:
        body = message.model_dump_json()
        headers = {"Content-Type": "application/json"}
        for url in self.targets():
            try:
                await self._client.post(url, content=body, headers=headers, timeout=10.0)
            except httpx.HTTPError:
                # Push is best-effort; never let a delivery failure break a run.
                pass
