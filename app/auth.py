"""Bearer-token auth for the mobile API."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Validate `Authorization: Bearer <bridge_token>`.

    When no token is configured the bridge runs open (development only); main.py
    logs a warning at startup in that case.
    """
    if not settings.auth_enabled:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, settings.bridge_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
