"""A conversation at a glance: what Claude is doing now, what changed in its
folder, how many tokens it used over the last days and the latest failures.

One place builds it for the queue detail in the TUI and for the API
(GET /conversations/{id}/overview), so both show the same. The result is
plain JSON-ready data (times as ISO strings, texts not yet translated except
the state label)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ccc import gitops, transcript
from ccc.i18n import N_, _, retranslate
from ccc.models import Queue, QueueStatus

USAGE_DAYS = 14
ERRORS_SHOWN = 5
ERRORS_DAYS = 7
EVENT_TEXT_MAX = 300
MODELS_SHOWN = 4

STATE_LABELS = {
    "working": N_("Working"),
    "waiting_approval": N_("Waiting for your approval"),
    "waiting_limit": N_("Waiting for the usage limit to reset"),
    "paused_limit": N_("Paused at the usage limit"),
    "blocked": N_("Blocked by an error"),
    "paused": N_("Paused"),
    "idle": N_("Idle"),
}


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class TranscriptTail:
    """The newest line of a conversation's transcript, read incrementally
    (only what was appended since the last look)."""

    def __init__(self) -> None:
        self._session: str | None = None
        self._path: Path | None = None
        self._offset = 0
        self._last: transcript.TranscriptEvent | None = None

    def last(self, session_id: str) -> transcript.TranscriptEvent | None:
        if session_id != self._session:
            self._session, self._path, self._offset, self._last = session_id, None, 0, None
        if self._path is None:
            self._path = transcript.find_transcript_path(session_id)
            if self._path is None:
                return None
        try:
            events, self._offset = transcript.parse_transcript_events(self._path, since_byte_offset=self._offset)
        except OSError:
            return self._last
        if events:
            self._last = events[-1]
        return self._last


def state_of(queue: Queue, needs_approval: bool) -> str:
    if needs_approval:
        return "waiting_approval"
    if queue.status == QueueStatus.RUNNING or queue.live_busy:
        return "working"
    if queue.status == QueueStatus.WAITING_RETRY:
        return "waiting_limit"
    if queue.status == QueueStatus.PAUSED_LIMIT:
        return "paused_limit"
    if queue.status == QueueStatus.BLOCKED_ERROR:
        return "blocked"
    if queue.paused:
        return "paused"
    return "idle"


def _event(event: transcript.TranscriptEvent | None) -> dict | None:
    if event is None:
        return None
    text = " ".join(event.text.split())
    if len(text) > EVENT_TEXT_MAX:
        text = text[:EVENT_TEXT_MAX] + "…"
    return {"kind": event.kind, "text": text, "time": iso(event.timestamp)}


def now_of(storage, queue: Queue, last_event: transcript.TranscriptEvent | None) -> dict:
    state = state_of(queue, queue.id in storage.queues_waiting_for_approval())
    return {
        "state": state,
        "state_label": _(STATE_LABELS[state]),
        "delivery_mode": queue.delivery_mode.value,
        "pending": storage.count_pending_items(queue.id),
        "next_retry_at": iso(queue.next_retry_at),
        "last_event": _event(last_event),
        "context": (
            {"percent": queue.context_percent, "tokens": queue.context_tokens, "window": queue.context_window}
            if queue.context_percent is not None else None
        ),
    }


def changes_of(queue: Queue) -> dict | None:
    """The folder's branch and changed files — only where Claude may run
    commands anyway (gitops); null elsewhere and outside git."""
    if not gitops.claude_can_run_commands(queue):
        return None
    status = gitops.work_status(queue.project_path)
    files = gitops.changed_files(queue.project_path)
    if status is None or files is None:
        return None
    return {
        "branch": status.branch, "base": status.base, "added": status.added, "removed": status.removed,
        "ahead": status.ahead, "behind": status.behind,
        "files": [{"path": f.path, "status": f.status, "added": f.added, "removed": f.removed} for f in files],
        "truncated": len(files) >= gitops.CHANGED_FILES_MAX,
    }


def usage_of(storage, queue: Queue, today: date | None = None) -> dict:
    """Tokens of the last USAGE_DAYS days (headless runs and the replies of
    interactive sessions), per day and per model."""
    today = today or date.today()
    first = today - timedelta(days=USAGE_DAYS - 1)
    per_day = {(first + timedelta(days=i)).isoformat(): 0 for i in range(USAGE_DAYS)}
    per_model: dict[str, int] = {}
    for day, queue_id, model, tokens in storage.token_entries(first.isoformat(), (today + timedelta(days=1)).isoformat()):
        if queue_id != queue.id:
            continue
        per_day[day] = per_day.get(day, 0) + tokens
        per_model[model] = per_model.get(model, 0) + tokens
    models = sorted(per_model.items(), key=lambda kv: -kv[1])
    return {
        "total": storage.queue_token_total(queue.id),
        "today": per_day.get(today.isoformat(), 0),
        "days": [{"day": day, "tokens": tokens} for day, tokens in sorted(per_day.items())],
        "models": [{"model": model, "tokens": tokens} for model, tokens in models[:MODELS_SHOWN]],
    }


def errors_of(storage, queue: Queue, now: datetime | None = None) -> dict:
    """The latest runs that failed (headless runs, and a message that could
    not be delivered to a terminal), and what blocks the conversation now."""
    now = now or datetime.now()
    runs = storage.failed_runs_for_queue(queue.id, ERRORS_SHOWN)
    return {
        "current": queue.last_error,
        "last_days": ERRORS_DAYS,
        "count": storage.failed_run_count(queue.id, (now - timedelta(days=ERRORS_DAYS)).isoformat()),
        "recent": [
            {"id": r.id, "time": iso(r.started_at), "status": r.status.value,
             "text": retranslate(r.error_text or r.result_text or "")[:EVENT_TEXT_MAX]}
            for r in runs
        ],
    }


@dataclass
class Overview:
    now: dict
    changes: dict | None
    usage: dict
    errors: dict

    def as_dict(self) -> dict:
        return {"now": self.now, "changes": self.changes, "usage": self.usage, "errors": self.errors}


def build(storage, queue: Queue, last_event: transcript.TranscriptEvent | None) -> Overview:
    return Overview(now_of(storage, queue, last_event), changes_of(queue), usage_of(storage, queue),
                    errors_of(storage, queue))
