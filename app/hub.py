"""Server-held run hub over the hermes-agent dashboard WebSocket (JSON-RPC).

One WebSocket per run: the hub does ``session.create`` (or reuses a session id)
then ``prompt.submit``, consumes ``event`` notifications, maps them to the
bridge's normalized frames, and fans them out to mobile SSE subscribers + push.
The run keeps going (and pushing) even if the phone disconnects. ``cancel`` and
``steer`` are sent on the same live connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import websockets

from .dashboard_events import map_event, to_app_frame
from .hermes_client import HermesClient
from .push import Pusher, to_push
from .sse import format_frame, is_terminal

_SENTINEL = object()
_START_TIMEOUT = 25.0


def _rpc(rid: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})


class StreamHub:
    def __init__(self, client: HermesClient, publisher: Pusher):
        self._client = client
        self._publisher = publisher
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._conns: dict[str, object] = {}  # sid -> websocket

    async def start_run(self, session_id: str | None, message: str) -> str:
        """Open a run and return its session id (used as the app's stream id)."""
        sid_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        task = asyncio.create_task(self._run(session_id, message, sid_future))
        try:
            sid = await asyncio.wait_for(asyncio.shield(sid_future), timeout=_START_TIMEOUT)
        except TimeoutError:
            task.cancel()
            raise RuntimeError("timed out starting run") from None
        self._subs.setdefault(sid, set())
        self._tasks[sid] = task
        return sid

    async def _run(
        self, requested_sid: str | None, message: str, sid_future: asyncio.Future
    ) -> None:
        sid = requested_sid
        try:
            async with websockets.connect(self._client.ws_url(), max_size=None) as ws:
                if requested_sid is None:
                    await ws.send(
                        _rpc(1, "session.create", {"source": "hermes-dispatch",
                                                   "title": message[:60]})
                    )
                async for raw in ws:
                    msg = json.loads(raw)

                    # Resolve the session id, then submit the prompt.
                    if not sid_future.done():
                        if requested_sid is None:
                            if msg.get("id") == 1:
                                sid = (msg.get("result") or {}).get("session_id")
                                if not sid:
                                    raise RuntimeError("session.create returned no session_id")
                            else:
                                continue  # wait for the create result
                        else:
                            sid = requested_sid
                        self._conns[sid] = ws
                        sid_future.set_result(sid)
                        await ws.send(_rpc(2, "prompt.submit",
                                           {"session_id": sid, "text": message}))
                        if requested_sid is None:
                            continue  # the id==1 message carried no event

                    if msg.get("method") != "event":
                        continue
                    params = msg.get("params") or {}
                    ev = map_event(params.get("type"), params.get("payload"))
                    name, data = to_app_frame(ev)
                    self._broadcast(sid, (name, data))
                    push = to_push(ev, session_id=sid)
                    if push is not None:
                        await self._publisher.publish(push)
                    if is_terminal(ev):
                        break
        except Exception:  # noqa: BLE001 - end the stream cleanly; never crash the hub
            pass
        finally:
            if not sid_future.done():
                sid_future.set_exception(RuntimeError("run failed to start"))
            if sid:
                self._broadcast(sid, _SENTINEL)
                self._conns.pop(sid, None)
                self._tasks.pop(sid, None)
                self._subs.pop(sid, None)

    def _broadcast(self, sid: str | None, item: object) -> None:
        if sid is None:
            return
        for q in list(self._subs.get(sid, ())):
            q.put_nowait(item)

    async def subscribe(self, stream_id: str) -> AsyncIterator[str]:
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

    async def cancel(self, stream_id: str) -> None:
        ws = self._conns.get(stream_id)
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(_rpc(90, "session.interrupt", {"session_id": stream_id}))

    async def steer(self, stream_id: str, message: str) -> None:
        ws = self._conns.get(stream_id)
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(_rpc(91, "session.steer",
                                   {"session_id": stream_id, "text": message}))

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
