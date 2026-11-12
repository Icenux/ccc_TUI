"""What happened lately, across all conversations — the Dashboard's ACTIVITY
(#250): finished tasks, errors, usage limits, reviews, tool calls waiting
for your approval and queued tasks, newest first."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ccc.i18n import _
from ccc.models import RunStatus

# The kinds, in the words the Dashboard shows (fixed, untranslated like the
# design's: they're short labels, not sentences).
TASK, QUEUE, LIMIT, APPROVE, ERROR, REVIEW = "task", "queue", "limit", "approve", "error", "review"
MESSAGE_CHARS = 90


@dataclass(frozen=True)
class Event:
    when: datetime
    kind: str
    project: str
    message: str


def _first_line(text: str | None) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= MESSAGE_CHARS else line[:MESSAGE_CHARS - 1] + "…"


def _run_event(run, name: str) -> Event:
    when = run.finished_at or run.started_at
    if run.review_id is not None:
        return Event(when, REVIEW, name, _first_line(run.result_text) or _("review finished"))
    if run.status == RunStatus.ERROR:
        return Event(when, ERROR, name, _first_line(run.error_text) or _first_line(run.prompt_text))
    if run.status == RunStatus.RATE_LIMITED:
        return Event(when, LIMIT, name, _("usage limit — retry after the reset"))
    if run.finished_at is None:
        return Event(when, TASK, name, _("working: {task}").format(task=_first_line(run.prompt_text)))
    message = _first_line(run.prompt_text)
    if run.git_commit:
        message = _("{task} — committed {sha}").format(task=message, sha=run.git_commit[:7])
    return Event(when, TASK, name, message)


def recent(storage, limit: int = 12) -> list[Event]:
    """The latest events, newest first."""
    names = {q.id: q.name for q in storage.list_queues()}
    events = [_run_event(run, names[run.queue_id]) for run in storage.recent_runs(limit) if run.queue_id in names]
    for request in storage.pending_permission_requests_all():
        if request.queue_id in names:
            events.append(Event(request.created_at, APPROVE, names[request.queue_id],
                                _("waits for approval: {tool}").format(tool=request.tool_name)))
    for queue in storage.list_queues():
        if queue.archived:
            continue
        pending = storage.list_pending_items(queue.id)
        if pending:
            newest = max(item.added_at for item in pending)
            events.append(Event(newest, QUEUE, queue.name,
                                _("{n} tasks waiting").format(n=len(pending)) if len(pending) != 1
                                else _("1 task waiting")))
    events.sort(key=lambda e: e.when, reverse=True)
    return events[:limit]
