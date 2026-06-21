"""Async client for the upstream Hermes webui HTTP + SSE contract."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


class HermesClient:
    """Wraps the webui API. Logs in once (cookie jar) and re-logs on 401."""

    def __init__(self, base_url: str, password: str, client: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._password = password
        self._client = client
        self._authed = False

    async def ensure_login(self) -> None:
        if self._authed:
            return
        await self.login()

    async def login(self) -> None:
        resp = await self._client.post(
            f"{self._base}/api/auth/login", json={"password": self._password}
        )
        resp.raise_for_status()
        self._authed = True

    async def _get_json(self, path: str) -> list | dict:
        await self.ensure_login()
        resp = await self._client.get(f"{self._base}{path}")
        if resp.status_code == 401:
            self._authed = False
            await self.ensure_login()
            resp = await self._client.get(f"{self._base}{path}")
        resp.raise_for_status()
        return resp.json()

    async def sessions(self) -> list[dict]:
        return await self._get_json("/api/sessions")

    async def crons(self) -> list[dict]:
        return await self._get_json("/api/crons")

    async def mcp_servers(self) -> list[dict]:
        return await self._get_json("/api/mcp/servers")

    async def create_session(self) -> str:
        await self.ensure_login()
        resp = await self._client.post(f"{self._base}/api/session", json={})
        resp.raise_for_status()
        return resp.json()["session_id"]

    async def start_chat(self, session_id: str, message: str, model: str | None = None) -> str:
        await self.ensure_login()
        body = {"session_id": session_id, "message": message}
        if model:
            body["model"] = model
        resp = await self._client.post(f"{self._base}/api/chat/start", json=body)
        resp.raise_for_status()
        return resp.json()["stream_id"]

    async def cancel_chat(self, stream_id: str) -> None:
        await self.ensure_login()
        resp = await self._client.post(
            f"{self._base}/api/chat/cancel", json={"stream_id": stream_id}
        )
        resp.raise_for_status()

    async def steer_chat(self, stream_id: str, message: str) -> None:
        await self.ensure_login()
        resp = await self._client.post(
            f"{self._base}/api/chat/steer", json={"stream_id": stream_id, "message": message}
        )
        resp.raise_for_status()

    async def cron_action(self, action: str, cron_id: str) -> None:
        await self.ensure_login()
        resp = await self._client.post(f"{self._base}/api/crons/{action}", json={"id": cron_id})
        resp.raise_for_status()

    async def create_cron(self, name: str, schedule: str) -> dict:
        await self.ensure_login()
        resp = await self._client.post(
            f"{self._base}/api/crons/create", json={"name": name, "schedule": schedule}
        )
        resp.raise_for_status()
        return resp.json()

    async def stream_chat(self, stream_id: str) -> AsyncIterator[tuple[str | None, str]]:
        """Yield (event_name, data) SSE frames for a run."""
        await self.ensure_login()
        url = f"{self._base}/api/chat/stream"
        headers = {"Accept": "text/event-stream"}
        async with self._client.stream(
            "GET", url, params={"stream_id": stream_id}, headers=headers, timeout=None
        ) as resp:
            resp.raise_for_status()
            event: str | None = None
            data: list[str] = []
            async for line in resp.aiter_lines():
                if line == "":
                    if data or event is not None:
                        yield event, "\n".join(data)
                    event, data = None, []
                elif line.startswith(":"):
                    continue
                elif line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data.append(line[len("data:"):].lstrip())
