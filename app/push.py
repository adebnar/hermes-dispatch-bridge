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
        return PushMessage(**base, status=event.text) if event.text.strip() else None
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
    """Publishes :class:`PushMessage` to every registered device endpoint plus an
    optional statically-configured ntfy topic.

    Device endpoints (the app's own UnifiedPush registrations) receive the full
    message as JSON so the client can render title/status/done and deep-link —
    and, when an E2EE key is registered, that JSON is AES-GCM encrypted so the
    relay only sees ciphertext. The static ntfy topic stays plaintext with
    Title/Tags/Priority headers so the ntfy app renders it on the lock screen.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        registry: EndpointRegistry,
        static_endpoint: str | None = None,
        get_key=None,
    ):
        self._client = client
        self._registry = registry
        self._static = static_endpoint
        self._get_key = get_key  # callable -> bytes | None

    def targets(self) -> list[str]:
        urls = set(self._registry.all())
        if self._static:
            urls.add(self._static)
        return sorted(urls)

    async def publish(self, message: PushMessage) -> None:
        # Device endpoints: full JSON body (optionally encrypted).
        from . import crypto

        json_body = message.model_dump_json().encode("utf-8")
        key = self._get_key() if self._get_key else None
        device_body = crypto.encrypt(json_body, key).encode("utf-8") if key else json_body

        for url in self._registry.all():
            await self._post(url, device_body, headers={"Priority": "low"})

        # Static ntfy topic: plaintext for the ntfy app's lock-screen rendering.
        if self._static:
            text = (message.status or ("Complete" if message.done else "")).encode("utf-8")
            await self._post(
                self._static,
                text,
                headers={
                    "Title": message.title or "Hermes",
                    "Tags": "white_check_mark" if message.done else "robot",
                    "Priority": "default" if message.done else "low",
                },
            )

    async def _post(self, url: str, body: bytes, headers: dict) -> None:
        try:
            await self._client.post(url, content=body, headers=headers, timeout=10.0)
        except httpx.HTTPError:
            # Push is best-effort; never let a delivery failure break a run.
            pass
