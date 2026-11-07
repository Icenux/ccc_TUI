"""Token usage of remote-control conversations.

Those run as interactive `claude --remote-control` sessions, which return no
structured result — but Claude Code writes every model reply with its model
and token usage into the session's transcript. The daemon reads new lines
of the transcripts of ccc's remote-control conversations (and of their
subagents) and stores one row per reply in `session_usage`.

One reply spans several transcript lines (one per content block), all with
the same `message.id` and the same usage, so replies are counted once per
message id. Only replies from the moment the conversation was created in
ccc count — a resumed session's older history doesn't."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ccc import transcript
from ccc.models import DeliveryMode

logger = logging.getLogger(__name__)

USAGE_POLL_SECONDS = 30.0
SKIPPED_MODELS = {"<synthetic>"}  # Claude Code's own placeholder replies, no API call


@dataclass(frozen=True)
class UsageRecord:
    message_id: str
    model: str
    at: datetime  # local time, like prompt_runs.started_at
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


def _record(data: dict) -> UsageRecord | None:
    if data.get("type") != "assistant":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    usage, msg_id, model = message.get("usage"), message.get("id"), message.get("model")
    if not isinstance(usage, dict) or not msg_id or not model or model in SKIPPED_MODELS:
        return None
    try:
        at = datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (KeyError, ValueError):
        return None

    def tokens(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    return UsageRecord(
        str(msg_id), str(model), at,
        tokens("input_tokens"), tokens("output_tokens"),
        tokens("cache_read_input_tokens"), tokens("cache_creation_input_tokens"),
    )


def parse_usage(path: Path, offset: int = 0) -> tuple[list[UsageRecord], int]:
    """Replies in the transcript from a byte offset, one per message id,
    plus the offset to continue from. A trailing incomplete line (the file
    is being written) is left for the next call."""
    records: dict[str, UsageRecord] = {}
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n") + 1
    for raw in data[:end].splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        record = _record(line) if isinstance(line, dict) else None
        if record is not None and record.message_id not in records:
            records[record.message_id] = record
    return list(records.values()), offset + end


class UsageCollector:
    """Keeps read offsets per transcript file between calls (in memory: after
    a daemon restart files are read again and duplicates are ignored)."""

    def __init__(self, projects_root: Path | None = None):
        self.projects_root = projects_root
        self._offsets: dict[Path, int] = {}

    def _transcripts(self, session_id: str) -> list[Path]:
        for root in ([self.projects_root] if self.projects_root else transcript.every_project_root()):
            main = list(root.glob(f"*/{session_id}.jsonl"))
            if main:
                return main[:1] + sorted(root.glob(f"*/{session_id}/subagents/*.jsonl"))
        return []

    def collect(self, storage) -> int:
        """Read new replies of every interactive conversation; returns how
        many were stored."""
        stored = 0
        for queue in storage.list_queues():
            if not queue.delivery_mode.managed:
                continue
            for path in self._transcripts(queue.session_id):
                offset = self._offsets.get(path, 0)
                try:
                    if path.stat().st_size < offset:  # file replaced / truncated
                        offset = 0
                    records, self._offsets[path] = parse_usage(path, offset)
                except OSError:
                    continue
                records = [r for r in records if r.at >= queue.created_at]
                if records:
                    stored += storage.insert_session_usage(queue.id, records)
        return stored
