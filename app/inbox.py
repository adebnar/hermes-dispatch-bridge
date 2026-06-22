"""Local cron "Inbox": read deliverables that cron jobs save to disk.

hermes-agent always saves each cron run's output to
``{hermes_root}/cron/output/{job_id}/{YYYY-MM-DD_HH-MM-SS}.md`` (default
profile) or ``{hermes_root}/profiles/{profile}/cron/output/{job_id}/*.md``
(named profiles) — regardless of whether the job also delivers to a chat
platform. Jobs whose ``deliver`` is ``local`` only ever land here, so this is
where "deliver to this desktop" results show up.

This module enumerates those files, parses their predictable header, reads one
by id, and (via :class:`InboxWatcher`) pushes an alert when a new deliverable
appears for a job the user has subscribed to.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("hermes-dispatch-bridge.inbox")

# Header line written by cron/jobs.py: "# Cron Job: <name>" (may end " (FAILED)").
_NAME_RE = re.compile(r"^#\s*Cron Job:\s*(.+?)\s*$", re.MULTILINE)
_RUNTIME_RE = re.compile(r"^\*\*Run Time:\*\*\s*(.+?)\s*$", re.MULTILINE)
# Output filenames are timestamps: 2026-06-22_09-00-25.md
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


@dataclass
class InboxItem:
    item_id: str  # "{profile}/{job_id}/{filename}" — opaque id for the read API
    profile: str
    job_id: str
    job_name: str
    status: str  # "ok" | "failed" | "silent"
    created_at: float | None  # epoch seconds, parsed from the filename
    size: int
    snippet: str


def _epoch_from_name(filename: str) -> float | None:
    m = _TS_RE.search(filename)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups())).timestamp()
    except ValueError:
        return None


def _profile_roots(hermes_root: str) -> list[tuple[str, Path]]:
    """(profile_name, output_dir) for every profile that has a cron output tree."""
    root = Path(hermes_root)
    out: list[tuple[str, Path]] = []
    default = root / "cron" / "output"
    if default.is_dir():
        out.append(("default", default))
    profiles = root / "profiles"
    if profiles.is_dir():
        for p in sorted(profiles.iterdir()):
            d = p / "cron" / "output"
            if d.is_dir():
                out.append((p.name, d))
    return out


def _parse_header(text: str) -> tuple[str, str]:
    """Return (job_name, status) from a deliverable's markdown header."""
    name_m = _NAME_RE.search(text)
    name = name_m.group(1) if name_m else "Cron job"
    status = "ok"
    if name.endswith("(FAILED)"):
        status = "failed"
        name = name[: -len("(FAILED)")].strip()
    body = text.split("\n---\n", 1)[-1].strip()
    if body == "[SILENT]" or "## Error" in text:
        status = "failed" if "## Error" in text else "silent"
    return name, status


def _snippet(text: str, limit: int = 240) -> str:
    """First meaningful line(s) of the body, after the metadata header."""
    body = text.split("\n---\n", 1)[-1].strip() if "\n---\n" in text else text
    # Drop a leading "## Error"/"## Prompt" heading from the snippet.
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    snippet = " ".join(lines)
    return snippet[:limit]


class InboxService:
    def __init__(self, hermes_root: str):
        self._root = hermes_root

    def _iter_files(self, profile: str | None) -> list[tuple[str, str, Path]]:
        """(profile, job_id, md_path) across all profiles (or one)."""
        items: list[tuple[str, str, Path]] = []
        for prof, out_dir in _profile_roots(self._root):
            if profile and prof != profile:
                continue
            for job_dir in out_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                for md in job_dir.glob("*.md"):
                    items.append((prof, job_dir.name, md))
        return items

    def list_items(self, profile: str | None = None, limit: int = 100) -> list[InboxItem]:
        rows: list[InboxItem] = []
        for prof, job_id, md in self._iter_files(profile):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
                stat = md.stat()
            except OSError:
                continue
            name, status = _parse_header(text)
            rows.append(
                InboxItem(
                    item_id=f"{prof}/{job_id}/{md.name}",
                    profile=prof,
                    job_id=job_id,
                    job_name=name,
                    status=status,
                    created_at=_epoch_from_name(md.name) or stat.st_mtime,
                    size=stat.st_size,
                    snippet=_snippet(text),
                )
            )
        rows.sort(key=lambda r: r.created_at or 0, reverse=True)
        return rows[:limit]

    def read_item(self, item_id: str) -> tuple[InboxItem, str] | None:
        path = self._resolve(item_id)
        if path is None:
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError:
            return None
        prof, job_id, fname = item_id.split("/", 2)
        name, status = _parse_header(text)
        item = InboxItem(
            item_id=item_id,
            profile=prof,
            job_id=job_id,
            job_name=name,
            status=status,
            created_at=_epoch_from_name(fname) or stat.st_mtime,
            size=stat.st_size,
            snippet=_snippet(text),
        )
        return item, text

    def _resolve(self, item_id: str) -> Path | None:
        """Map an opaque item id back to a file path, rejecting path escapes."""
        parts = item_id.split("/")
        if len(parts) != 3:
            return None
        profile, job_id, fname = parts
        # No traversal / separators in any component.
        if any(seg in ("", ".", "..") or "/" in seg or "\\" in seg for seg in parts):
            return None
        if not fname.endswith(".md"):
            return None
        for prof, out_dir in _profile_roots(self._root):
            if prof != profile:
                continue
            candidate = (out_dir / job_id / fname).resolve()
            try:
                candidate.relative_to(out_dir.resolve())
            except ValueError:
                return None
            return candidate if candidate.is_file() else None
        return None


class InboxWatcher:
    """Polls for new local deliverables and pushes an alert for subscribed jobs."""

    def __init__(self, service: InboxService, store, pusher, poll_seconds: float):
        self._service = service
        self._store = store  # StateStore; alert job-ids under "inbox_alerts"
        self._pusher = pusher
        self._poll = poll_seconds
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None

    # --- alert subscriptions (persisted) ---
    def alerts(self) -> list[str]:
        return list(self._store.get("inbox_alerts", []))

    def set_alerts(self, job_ids: list[str]) -> list[str]:
        cleaned = sorted({j.strip() for j in job_ids if j and j.strip()})
        self._store.set("inbox_alerts", cleaned)
        return cleaned

    def start(self) -> None:
        # Seed "seen" with everything that already exists so a restart doesn't
        # re-alert for old deliverables; only files appearing later fire.
        self._seen = {i.item_id for i in self._service.list_items(limit=10000)}
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        from .models import PushMessage

        while True:
            try:
                await asyncio.sleep(self._poll)
                subs = set(self.alerts())
                if not subs:
                    # Still track new files so enabling alerts later doesn't
                    # retroactively fire for files seen while disabled.
                    self._seen |= {i.item_id for i in self._service.list_items(limit=10000)}
                    continue
                for item in self._service.list_items(limit=10000):
                    if item.item_id in self._seen:
                        continue
                    self._seen.add(item.item_id)
                    if item.job_id in subs:
                        await self._pusher.publish(
                            PushMessage(
                                sessionId=None,
                                title=f"📥 {item.job_name}",
                                status=item.snippet[:120] or "New result in your Inbox",
                                done=True,
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - watcher must never die
                log.exception("inbox watcher poll failed")
