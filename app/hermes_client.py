"""Client for the hermes-agent dashboard: REST (httpx) + WS URL/token helpers.

Auth is the dashboard session token sent as `X-Hermes-Session-Token` on REST and
as `?token=` on the WebSocket. See docs/PHASE0-FINDINGS.md.
"""

from __future__ import annotations

from typing import Any

import httpx

# Dashboard cron actions → endpoint suffixes.
CRON_ACTIONS = {"pause": "pause", "resume": "resume", "run": "trigger"}


class HermesClient:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def token(self) -> str:
        return self._token

    def ws_url(self) -> str:
        scheme = "wss" if self._base.startswith("https") else "ws"
        host = self._base.split("://", 1)[-1]
        return f"{scheme}://{host}/api/ws?token={self._token}"

    def _headers(self) -> dict[str, str]:
        return {"X-Hermes-Session-Token": self._token} if self._token else {}

    async def _get(self, path: str) -> Any:
        resp = await self._client.get(f"{self._base}{path}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def sessions(self) -> list[dict]:
        data = await self._get("/api/sessions?limit=50&offset=0&order=created")
        return data.get("sessions", []) if isinstance(data, dict) else []

    async def crons(self) -> list[dict]:
        data = await self._get("/api/cron/jobs")
        return data if isinstance(data, list) else data.get("jobs", [])

    async def mcp_servers(self) -> list[dict]:
        data = await self._get("/api/mcp/servers")
        return data.get("servers", []) if isinstance(data, dict) else data

    async def model_info(self) -> dict:
        return await self._get("/api/model/info")

    async def create_cron(self, name: str, prompt: str, schedule: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/api/cron/jobs",
            headers=self._headers(),
            json={"name": name, "prompt": prompt, "schedule": schedule},
        )
        resp.raise_for_status()
        return resp.json()

    async def cron_action(self, action: str, cron_id: str) -> None:
        if action == "delete":
            resp = await self._client.delete(
                f"{self._base}/api/cron/jobs/{cron_id}", headers=self._headers()
            )
        else:
            suffix = CRON_ACTIONS.get(action)
            if suffix is None:
                raise ValueError(f"unknown cron action: {action}")
            resp = await self._client.post(
                f"{self._base}/api/cron/jobs/{cron_id}/{suffix}", headers=self._headers()
            )
        resp.raise_for_status()

    async def transcribe(self, audio: bytes, filename: str = "audio.m4a") -> str:
        resp = await self._client.post(
            f"{self._base}/api/audio/transcribe",
            headers=self._headers(),
            files={"file": (filename, audio)},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("text", "") if isinstance(data, dict) else str(data)
