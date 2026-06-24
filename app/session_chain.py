"""Reassemble the full transcript of a Hermes conversation across compression.

Hermes context-compression ends a session and forks a child (linked via
``parent_session_id``), splitting a long conversation across several sessions.
The dashboard messages API is no help here: ``resolve_resume_session_id``
redirects EVERY link in a chain to the live tip, so the API only ever returns the
tip's segment and the earlier links are unreachable through it.

So the bridge reads the profile's ``state.db`` READ-ONLY, walks the
``parent_session_id`` lineage, and concatenates each link's user/assistant text in
order. Content decoding mirrors Hermes's own ``_decode_content`` (a ``\\x00json:``
prefix marks JSON-encoded structured content; everything else is a plain string).
On any error this returns ``None`` so the caller falls back to the single-session
API path (never worse than before).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_MAX_CHAIN = 64
_CONTENT_JSON_PREFIX = "\x00json:"  # mirrors hermes_state.SessionDB._CONTENT_JSON_PREFIX


def _db_path(hermes_root: str, profile: str | None) -> Path:
    base = Path(hermes_root)
    if profile:
        return base / "profiles" / profile / "state.db"
    return base / "state.db"


def _decode_text(content) -> str:
    """Extract displayable text from a stored ``content`` value, matching how the
    dashboard maps messages (plain string, or JSON parts joined on their text)."""
    if isinstance(content, str) and content.startswith(_CONTENT_JSON_PREFIX):
        try:
            content = json.loads(content[len(_CONTENT_JSON_PREFIX):])
        except (json.JSONDecodeError, TypeError):
            return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


def chain_transcript(
    hermes_root: str, profile: str | None, session_id: str
) -> list[tuple[str, str]] | None:
    """Full (role, text) transcript across the compression lineage of
    ``session_id``, root → tip. Returns ``None`` on any problem so the caller can
    fall back to the API."""
    if not session_id:
        return None
    db = _db_path(hermes_root, profile)
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row

        # Unknown session in this DB → return None so the caller falls back to the
        # API (handles prefix ids, other profiles, fresh sessions not yet flushed).
        if not conn.execute(
            "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)
        ).fetchone():
            return None

        def parent(sid: str) -> str | None:
            row = conn.execute(
                "SELECT parent_session_id FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            return (row["parent_session_id"] or None) if row else None

        def latest_child(sid: str) -> str | None:
            row = conn.execute(
                "SELECT id FROM sessions WHERE parent_session_id = ? "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (sid,),
            ).fetchone()
            return row["id"] if row else None

        # Walk up to the root, then down the continuation chain to the tip,
        # keeping session_id in the middle.
        ups: list[str] = []
        seen: set[str] = set()
        cur: str | None = session_id
        while cur and cur not in seen and len(ups) < _MAX_CHAIN:
            seen.add(cur)
            ups.append(cur)
            cur = parent(cur)
        ups.reverse()

        downs: list[str] = []
        cur = session_id
        while len(downs) < _MAX_CHAIN:
            child = latest_child(cur)
            if not child or child in seen:
                break
            seen.add(child)
            downs.append(child)
            cur = child

        out: list[tuple[str, str]] = []
        for sid in ups + downs:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = ? AND active = 1 AND role IN ('user','assistant') "
                "ORDER BY id",
                (sid,),
            ).fetchall()
            for row in rows:
                text = _decode_text(row["content"]).strip()
                if text:
                    out.append((row["role"], text))
        return out
    except sqlite3.Error:
        return None
    finally:
        conn.close()
