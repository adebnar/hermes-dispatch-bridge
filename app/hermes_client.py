"""Client for the hermes-agent dashboard: REST (httpx) + WS URL/token helpers.

Auth is the dashboard session token sent as `X-Hermes-Session-Token` on REST and
as `?token=` on the WebSocket. See docs/PHASE0-FINDINGS.md.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

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

    async def sessions(
        self, profile: str | None = None, archived: str = "exclude"
    ) -> list[dict]:
        path = f"/api/sessions?limit=50&offset=0&order=created&archived={archived}"
        if profile:
            path += f"&profile={quote(profile)}"
        data = await self._get(path)
        return data.get("sessions", []) if isinstance(data, dict) else []

    async def search_sessions(
        self, q: str, profile: str | None = None, limit: int = 30
    ) -> list[dict]:
        path = f"/api/sessions/search?q={quote(q)}&limit={limit}"
        if profile:
            path += f"&profile={quote(profile)}"
        data = await self._get(path)
        return data.get("results", []) if isinstance(data, dict) else []

    async def set_session_archived(
        self, session_id: str, archived: bool, profile: str | None = None
    ) -> None:
        await self._ensure_token()
        body: dict = {"archived": archived}
        if profile:
            body["profile"] = profile
        resp = await self._client.patch(
            f"{self._base}/api/sessions/{quote(session_id)}",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()

    async def profiles(self) -> list[dict]:
        data = await self._get("/api/profiles")
        return data.get("profiles", []) if isinstance(data, dict) else data

    async def active_profile(self) -> str | None:
        data = await self._get("/api/profiles/active")
        return data.get("active") if isinstance(data, dict) else None

    async def crons(self) -> list[dict]:
        data = await self._get("/api/cron/jobs")
        return data if isinstance(data, list) else data.get("jobs", [])

    async def mcp_servers(self) -> list[dict]:
        data = await self._get("/api/mcp/servers")
        return data.get("servers", []) if isinstance(data, dict) else data

    async def model_info(self) -> dict:
        return await self._get("/api/model/info")

    async def gateway_version(self) -> str | None:
        """The hermes-agent dashboard version (from /api/system/stats)."""
        data = await self._get("/api/system/stats")
        return data.get("hermes_version") if isinstance(data, dict) else None

    async def model_options(self, profile: str | None = None) -> dict:
        path = "/api/model/options"
        if profile:
            path += f"?profile={quote(profile)}"
        return await self._get(path)

    async def set_profile_model(self, profile: str, provider: str, model: str) -> None:
        await self._ensure_token()
        resp = await self._client.put(
            f"{self._base}/api/profiles/{quote(profile)}/model",
            headers=self._headers(),
            json={"provider": provider, "model": model},
        )
        resp.raise_for_status()

    async def set_global_model(self, provider: str, model: str) -> None:
        await self._ensure_token()
        resp = await self._client.post(
            f"{self._base}/api/model/set",
            headers=self._headers(),
            json={"provider": provider, "model": model},
        )
        resp.raise_for_status()

    async def create_cron(
        self, name: str, prompt: str, schedule: str, profile: str | None = None
    ) -> dict:
        await self._ensure_token()
        # The dashboard reads the target profile from the ?profile= QUERY param
        # (CronJobCreate has no profile field); a body profile is ignored and the
        # job lands in "default". So pass it on the URL.
        url = f"{self._base}/api/cron/jobs"
        if profile:
            url += f"?profile={quote(profile)}"
        resp = await self._client.post(
            url,
            headers=self._headers(),
            json={"name": name, "prompt": prompt, "schedule": schedule},
        )
        resp.raise_for_status()
        return resp.json()

    async def messages(self, session_id: str, profile: str | None = None) -> list[dict]:
        path = f"/api/sessions/{session_id}/messages"
        if profile:
            path += f"?profile={quote(profile)}"
        data = await self._get(path)
        return data.get("messages", []) if isinstance(data, dict) else data

    async def update_cron(
        self, cron_id: str, updates: dict[str, Any], profile: str | None = None
    ) -> dict:
        await self._ensure_token()
        url = f"{self._base}/api/cron/jobs/{cron_id}"
        if profile:
            url += f"?profile={quote(profile)}"
        resp = await self._client.put(
            url, headers=self._headers(), json={"updates": updates}
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
