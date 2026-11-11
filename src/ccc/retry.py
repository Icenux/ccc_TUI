from __future__ import annotations

import re
from datetime import datetime, timedelta

_RESET_RE = re.compile(
    r"resets?\s+(?:at\s+)?(?:(\d{1,2}):(\d{2})\s*(am|pm)?|(\d{1,2})\s*(am|pm))",
    re.IGNORECASE,
)

MIN_BACKOFF_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 60 * 60


def parse_reset_time(text: str, now: datetime | None = None) -> datetime | None:
    match = _RESET_RE.search(text)
    if not match:
        return None
    now = now or datetime.now()
    if match.group(1) is not None:
        hour, minute, meridiem = int(match.group(1)), int(match.group(2)), (match.group(3) or "").lower()
    else:  # "resets 8pm" (Claude Code's own limit message)
        hour, minute, meridiem = int(match.group(4)), 0, match.group(5).lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_backoff_seconds(previous_seconds: int | None) -> int:
    if previous_seconds is None or previous_seconds <= 0:
        return MIN_BACKOFF_SECONDS
    return min(previous_seconds * 2, MAX_BACKOFF_SECONDS)
