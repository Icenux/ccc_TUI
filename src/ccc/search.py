"""Search across all conversations' transcripts, and Markdown export."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ccc.notify import known_secrets
from ccc.redact import redact
from ccc.storage import Storage
from ccc.transcript import TranscriptEvent, find_transcript_path, parse_transcript_events

MAX_HITS = 200
SNIPPET_CHARS = 120


@dataclass
class SearchHit:
    queue_id: int
    queue_name: str
    timestamp: datetime
    kind: str  # "human" | "assistant_text" | "tool_call" | "tool_result"
    snippet: str
    archived: bool = False


# Where to look (#304): the active conversations, the archived ones, or both.
ACTIVE, ARCHIVED, EVERYWHERE = "active", "archived", "all"
# What is searched: your messages, Claude's replies, and the tools it ran and
# what they gave back (a command like `bash` is often only there — #304).
MESSAGE_KINDS = ("human", "assistant_text")
ALL_KINDS = (*MESSAGE_KINDS, "tool_call", "tool_result")


def _events(storage: Storage, queue) -> list[TranscriptEvent]:
    path = find_transcript_path(queue.session_id)
    if path is None:
        return []
    events, _ = parse_transcript_events(path)
    return events


def _snippet(text: str, start: int, length: int) -> str:
    flat = " ".join(text.split())
    # Map the match position onto the whitespace-flattened text.
    pos = len(" ".join(text[:start].split()))
    half = (SNIPPET_CHARS - length) // 2
    lo = max(0, pos - half)
    hi = min(len(flat), lo + SNIPPET_CHARS)
    lo = max(0, hi - SNIPPET_CHARS)
    return ("…" if lo > 0 else "") + flat[lo:hi] + ("…" if hi < len(flat) else "")


def search_conversations(storage: Storage, query: str, limit: int = MAX_HITS, where: str = EVERYWHERE,
                         kinds: tuple[str, ...] = MESSAGE_KINDS) -> list[SearchHit]:
    """Case-insensitive substring search in the conversations `where` says
    (ACTIVE, ARCHIVED or EVERYWHERE), in the events of `kinds`, newest first."""
    query = query.strip()
    if not query:
        return []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    hits: list[SearchHit] = []
    for queue in storage.list_queues():
        if (where == ACTIVE and queue.archived) or (where == ARCHIVED and not queue.archived):
            continue
        for event in _events(storage, queue):
            if event.kind not in kinds:
                continue
            match = pattern.search(event.text)
            if match:
                hits.append(
                    SearchHit(queue.id, queue.name, event.timestamp, event.kind,
                              _snippet(event.text, match.start(), len(query)), bool(queue.archived))
                )
    hits.sort(key=lambda h: h.timestamp, reverse=True)
    return hits[:limit]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "conversation"


def markdown_export(storage: Storage, queue_id: int) -> tuple[str, str]:
    """(file name, Markdown) of a conversation, secrets masked — an
    export is something you share; see ccc.redact."""
    queue = storage.get_queue(queue_id)
    lines = [f"# {queue.name}", "", f"`{queue.project_path}` · session `{queue.session_id}`", ""]
    for event in _events(storage, queue):
        when = event.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        if event.kind == "human":
            lines += [f"## **You** · {when}", "", event.text, ""]
        elif event.kind == "assistant_text":
            lines += [f"## **Claude** · {when}", "", event.text, ""]
        elif event.kind == "tool_call":
            lines += [f"- ⚙ {event.text}"]
    name = f"{_safe_name(queue.name)}-{datetime.now():%Y%m%d-%H%M%S}.md"
    return name, redact("\n".join(lines) + "\n", known_secrets(storage))


def export_markdown(storage: Storage, queue_id: int, out_dir: Path) -> Path:
    name, text = markdown_export(storage, queue_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(text, encoding="utf-8")
    return path
