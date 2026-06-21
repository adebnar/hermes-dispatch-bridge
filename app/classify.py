"""Heuristic classification of a task prompt as one-shot vs recurring (cron).

This is intentionally a small, dependency-free heuristic so it is fast and fully
unit-testable. The plan's longer-term path is to delegate this to a Hermes
*skill* (`classify_schedule`); this module is the local fallback and the shape
that skill should return.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WEEKDAYS = {
    "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
    "thursday": 4, "friday": 5, "saturday": 6,
}

_RECURRENCE = re.compile(
    r"\b(every|each|daily|hourly|weekly|monthly|"
    r"every day|each day|every morning|every week|every month)\b",
    re.IGNORECASE,
)
_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScheduleResult:
    is_cron: bool
    cron: str | None = None


def _parse_time(text: str) -> tuple[int, int]:
    """Return (minute, hour) in 24h; default 09:00."""
    m = _TIME.search(text)
    if not m:
        return 0, 9
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return minute, hour


def classify_schedule(text: str) -> ScheduleResult:
    """Decide whether *text* describes a recurring task and, if so, a cron expr."""
    if not text or not _RECURRENCE.search(text):
        return ScheduleResult(is_cron=False)

    lowered = text.lower()
    minute, hour = _parse_time(lowered)

    if "hour" in lowered:  # hourly / every hour
        return ScheduleResult(True, f"{minute} * * * *")

    for name, dow in _WEEKDAYS.items():
        if name in lowered:
            return ScheduleResult(True, f"{minute} {hour} * * {dow}")

    if "week" in lowered:  # weekly, default Monday
        return ScheduleResult(True, f"{minute} {hour} * * 1")
    if "month" in lowered:  # monthly, default 1st
        return ScheduleResult(True, f"{minute} {hour} 1 * *")

    # daily / each day / every morning / every day
    return ScheduleResult(True, f"{minute} {hour} * * *")
