"""Client for the hermes-agent dashboard: REST (httpx) + WS URL/token helpers.

Auth is the dashboard session token sent as `X-Hermes-Session-Token` on REST and
as `?token=` on the WebSocket. See docs/PHASE0-FINDINGS.md.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

# Dashboard cron actions → endpoint suffixes.
CRON_ACTIONS = {"pause": "pause", "resume": "resume", "run": "trigger"}

_TOKEN_RE = re.compile(r'__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')


def extract_session_token(html: str) -> str | None:
    """Pull ``window.__HERMES_SESSION_TOKEN__`` out of the dashboard HTML.

    In loopback mode the dashboard injects its session token into the page, so a
    co-located bridge can read it directly instead of hard-coding one that
    rotates on restart.
    """
    m = _TOKEN_RE.search(html or "")
    return m.group(1) if m else None


class HermesClient:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._token = token  # configured override; empty → auto-fetch from HTML
        self._client = client

    @property
    def base_url(self) -> str:
        return self._base

    async def _ensure_token(self) -> None:
        if self._token:
            return
        resp = await self._client.get(f"{self._base}/")
        resp.raise_for_status()
        token = extract_session_token(resp.text)
        if not token:
            raise RuntimeError(
                "no HERMES_TOKEN set and the dashboard did not expose a session "
                "token (not in loopback mode?)"
            )
        self._token = token

    async def ws_url(self) -> str:
        await self._ensure_token()
        scheme = "wss" if self._base.startswith("https") else "ws"
        host = self._base.split("://", 1)[-1]
        return f"{scheme}://{host}/api/ws?token={self._token}"

    def _headers(self) -> dict[str, str]:
        return {"X-Hermes-Session-Token": self._token} if self._token else {}

    async def _get(self, path: str) -> Any:
        await self._ensure_token()
        resp = await self._client.get(f"{self._base}{path}", headers=self._headers())
        if resp.status_code in (401, 403):  # token rotated → refetch once and retry
            self._token = ""
            await self._ensure_token()
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
        await self._ensure_token()
        resp = await self._client.post(
            f"{self._base}/api/cron/jobs",
            headers=self._headers(),
            json={"name": name, "prompt": prompt, "schedule": schedule},
        )
        resp.raise_for_status()
        return resp.json()

    async def cron_action(self, action: str, cron_id: str) -> None:
        await self._ensure_token()
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
        await self._ensure_token()
        resp = await self._client.post(
            f"{self._base}/api/audio/transcribe",
            headers=self._headers(),
            files={"file": (filename, audio)},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("text", "") if isinstance(data, dict) else str(data)
