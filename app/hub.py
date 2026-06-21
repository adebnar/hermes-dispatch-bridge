"""Server-held run hub: one upstream SSE consumer per run, fanned out to any
number of mobile subscribers AND to background push.

This is what makes the bridge more than a proxy: the run keeps progressing and
pushing even when the phone is asleep or disconnected.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from .hermes_client import HermesClient
from .push import NtfyPublisher, to_push
from .sse import format_frame, is_terminal, parse

_SENTINEL = object()


class StreamHub:
    def __init__(self, client: HermesClient, publisher: NtfyPublisher):
        self._client = client
        self._publisher = publisher
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def ensure_consumer(self, stream_id: str, session_id: str | None) -> None:
        """Start the single upstream consumer for a run if not already running."""
        if stream_id in self._tasks:
            return
        self._subs.setdefault(stream_id, set())
        self._tasks[stream_id] = asyncio.create_task(self._consume(stream_id, session_id))

    async def _consume(self, stream_id: str, session_id: str | None) -> None:
        try:
            async for event_name, data in self._client.stream_chat(stream_id):
                self._broadcast(stream_id, (event_name, data))
                event = parse(event_name, data)
                push = to_push(event, session_id=session_id)
                if push is not None:
                    await self._publisher.publish(push)
                if is_terminal(event):
                    break
        except Exception:  # noqa: BLE001 - surface as stream end; never crash the hub
            pass
        finally:
            self._broadcast(stream_id, _SENTINEL)
            self._tasks.pop(stream_id, None)
            self._subs.pop(stream_id, None)

    def _broadcast(self, stream_id: str, item: object) -> None:
        for q in list(self._subs.get(stream_id, ())):
            q.put_nowait(item)

    async def subscribe(self, stream_id: str) -> AsyncIterator[str]:
        """Yield re-serialized SSE frames for a foreground subscriber."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(stream_id, set()).add(queue)
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                event_name, data = item
                yield format_frame(event_name, data)
        finally:
            self._subs.get(stream_id, set()).discard(queue)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
