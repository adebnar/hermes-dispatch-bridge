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
    title: str  # display title — derived from the result's first heading
    status: str  # "ok" | "failed" | "silent"
    created_at: float | None  # epoch seconds, parsed from the filename
    size: int
    snippet: str
    output: str = ""  # the result only (no cron header/prompt)


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


_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")


def _parse_header(text: str) -> tuple[str, str]:
    """Return (job_name, status) from a deliverable's markdown header."""
    name_m = _NAME_RE.search(text)
    name = name_m.group(1) if name_m else "Cron job"
    status = "ok"
    if name.endswith("(FAILED)"):
        status = "failed"
        name = name[: -len("(FAILED)")].strip()
    body = text.split("\n---\n", 1)[-1].strip()
    if "## Error" in text:
        status = "failed"
    elif body == "[SILENT]":
        status = "silent"
    return name, status


def _result(text: str, job_name: str) -> str:
    """The agent's output only — strip the cron header/prompt/metadata.

    Layout written by cron/jobs.py is: header + (## Prompt …) + "---" + output,
    or for failures a "## Error" section with no separator. We take the text
    after the final separator, fall back to the error block, then to the whole
    body, and drop a leading heading that just echoes the job name.
    """
    if "\n---\n" in text:
        out = text.split("\n---\n")[-1].strip()
    elif "## Error" in text:
        out = text.split("## Error", 1)[1].strip()
        out = "## Error\n" + out if out else out
    else:
        out = text.strip()
    # Drop a leading heading that duplicates the job name (echoed title).
    lines = out.splitlines()
    if lines:
        m = _HEADING_RE.match(lines[0].strip())
        if m and m.group(1).strip().lower() == job_name.strip().lower():
            out = "\n".join(lines[1:]).lstrip("\n")
    return out


_GENERIC_HEADINGS = {"error", "prompt", "output", "result", "summary"}


def _derive_title(output: str, job_name: str) -> str:
    """Use the result's first markdown heading as the title, else the job name."""
    for line in output.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _HEADING_RE.match(s)
        if m and m.group(1).strip().lower() not in _GENERIC_HEADINGS:
            return m.group(1).strip()
        return job_name
    return job_name


def _snippet(output: str, limit: int = 240) -> str:
    """First meaningful line(s) of the result, minus a leading heading."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if lines and _HEADING_RE.match(lines[0]):
        lines = lines[1:]
    return " ".join(lines)[:limit]


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
            output = _result(text, name)
            rows.append(
                InboxItem(
                    item_id=f"{prof}/{job_id}/{md.name}",
                    profile=prof,
                    job_id=job_id,
                    job_name=name,
                    title=_derive_title(output, name),
                    status=status,
                    created_at=_epoch_from_name(md.name) or stat.st_mtime,
                    size=stat.st_size,
                    snippet=_snippet(output),
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
        output = _result(text, name)
        item = InboxItem(
            item_id=item_id,
            profile=prof,
            job_id=job_id,
            job_name=name,
            title=_derive_title(output, name),
            status=status,
            created_at=_epoch_from_name(fname) or stat.st_mtime,
            size=stat.st_size,
            snippet=_snippet(output),
            output=output,
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
        # Jobs we've already alerted a failure for, until they next succeed —
        # so a job that fails every run alerts once, not on every poll.
        self._failing: set[str] = set()
        self._task: asyncio.Task | None = None

    # --- alert subscriptions (persisted) ---
    def alerts(self) -> list[str]:
        return list(self._store.get("inbox_alerts", []))

    def alert_on_failures(self) -> bool:
        return bool(self._store.get("inbox_alert_failures", False))

    def set_alerts(self, job_ids: list[str]) -> list[str]:
        cleaned = sorted({j.strip() for j in job_ids if j and j.strip()})
        self._store.set("inbox_alerts", cleaned)
        return cleaned

    def set_alert_on_failures(self, enabled: bool) -> bool:
        self._store.set("inbox_alert_failures", bool(enabled))
        return bool(enabled)

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
                failures_on = self.alert_on_failures()
                for item in self._service.list_items(limit=10000):
                    first_seen = item.item_id not in self._seen
                    self._seen.add(item.item_id)
                    # A success clears a job's failing-streak so its next failure
                    # can alert again.
                    if item.status != "failed":
                        self._failing.discard(item.job_id)
                    if not first_seen:
                        continue

                    subscribed = item.job_id in subs
                    failure_alert = (
                        failures_on
                        and item.status == "failed"
                        and item.job_id not in self._failing
                    )
                    if item.status == "failed":
                        self._failing.add(item.job_id)
                    if not (subscribed or failure_alert):
                        continue

                    failed = item.status == "failed"
                    await self._pusher.publish(
                        PushMessage(
                            sessionId=None,
                            title=("⚠️ " if failed else "📥 ") + (item.title or item.job_name),
                            status=item.snippet[:120]
                            or ("Run failed" if failed else "New result in your Inbox"),
                            done=True,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - watcher must never die
                log.exception("inbox watcher poll failed")
