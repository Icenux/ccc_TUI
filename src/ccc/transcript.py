from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ccc import profiles
from ccc.i18n import _

# Overridable in tests via monkeypatch — real default is Claude Code's own
# transcript root, one subdirectory per project (directory name is an
# encoded project path we deliberately do NOT try to reconstruct — see
# find_transcript_path).
PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def project_roots(profile: str = "") -> list[Path]:
    """Where transcripts of a profile live. For the default profile that is
    PROJECTS_ROOT; a named profile has its own folder. `None`-like "all" is
    `every_project_root`."""
    named = profiles.get(profile)
    return [named.config_dir / "projects"] if named else [PROJECTS_ROOT]


def every_project_root() -> list[Path]:
    """The transcript folders of the default profile and of every named one
    (a session id is a UUID, so searching all of them finds the one)."""
    roots = [PROJECTS_ROOT]
    for profile in profiles.list_profiles():
        root = profile.config_dir / "projects"
        if root not in roots:
            roots.append(root)
    return roots


@dataclass
class TranscriptEvent:
    kind: str  # "human" | "assistant_text" | "tool_call" | "tool_result"
    text: str
    timestamp: datetime
    # tool_call / tool_result: pairs a result with its call.
    tool_use_id: str | None = None
    # tool_call: the full input as pretty JSON (text is a short preview).
    detail: str | None = None


def find_transcript_path(session_id: str) -> Path | None:
    """Locate a Claude Code session's JSONL transcript by session_id alone
    (a UUID, effectively globally unique) — robust against Claude Code's
    project-path encoding scheme (replaces every non-alphanumeric
    character with "-", confirmed live on this machine; e.g. both "/" and
    "_" in a real path both became "-"), which would be fragile to
    reconstruct by hand. Returns None if no matching file exists."""
    for root in every_project_root():
        matches = list(root.glob(f"*/{session_id}.jsonl"))
        if matches:
            return matches[0]
    return None


TOOL_PREVIEW_MAX_CHARS = 80
TOOL_RESULT_MAX_CHARS = 4000


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        text = ""
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + "\n" + _("… (truncated, {n} chars total)").format(n=len(text))
    return text


def _parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _events_from_line(data: dict) -> list[TranscriptEvent]:
    line_type = data.get("type")
    message = data.get("message") or {}
    raw_timestamp = data.get("timestamp")
    if not raw_timestamp:
        return []
    timestamp = _parse_timestamp(raw_timestamp)

    if line_type == "user":
        content = message.get("content")
        if isinstance(content, str):
            return [TranscriptEvent(kind="human", text=content, timestamp=timestamp)]
        if isinstance(content, list):
            return [
                TranscriptEvent(
                    kind="tool_result",
                    text=_tool_result_text(block.get("content")),
                    timestamp=timestamp,
                    tool_use_id=block.get("tool_use_id"),
                )
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
        return []

    if line_type == "assistant":
        content = message.get("content")
        if not isinstance(content, list):
            return []
        events = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    events.append(TranscriptEvent(kind="assistant_text", text=text, timestamp=timestamp))
            elif block_type == "tool_use":
                name = block.get("name", "?")
                input_preview = json.dumps(block.get("input", {}), ensure_ascii=False)
                if len(input_preview) > TOOL_PREVIEW_MAX_CHARS:
                    input_preview = input_preview[:TOOL_PREVIEW_MAX_CHARS] + "…"
                events.append(
                    TranscriptEvent(
                        kind="tool_call",
                        text=f"{name}: {input_preview}",
                        timestamp=timestamp,
                        tool_use_id=block.get("id"),
                        detail=json.dumps(block.get("input", {}), indent=2, ensure_ascii=False),
                    )
                )
            # "thinking" and any other block type: skipped
        return events

    return []  # system, cost-state, bridge-session, mode, etc.: skipped


def parse_transcript_events(path: Path, since_byte_offset: int = 0) -> tuple[list[TranscriptEvent], int]:
    """Incrementally parse a JSONL transcript from a byte offset. Returns
    new events plus the offset to pass next time. The offset only ever
    advances past complete, successfully-parsed lines — a trailing
    incomplete line (the file may be actively written by a live Claude
    Code process) is left for the next call to pick up once it's whole."""
    events: list[TranscriptEvent] = []
    with open(path, "rb") as f:
        f.seek(since_byte_offset)
        consumed = since_byte_offset
        for raw_line in f:
            if not raw_line.endswith(b"\n"):
                break  # incomplete trailing line — do not consume
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                consumed += len(raw_line)
                continue
            events.extend(_events_from_line(data))
            consumed += len(raw_line)
    return events, consumed


@dataclass
class SessionSummary:
    session_id: str
    modified: datetime
    first_prompt: str
    message_count: int


def encode_project_dir(path: str) -> str:
    """Claude Code's transcript directory name for a cwd: every
    non-alphanumeric character becomes "-" (confirmed on this machine)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def list_sessions_for_directory(path: str, limit: int = 50, profile: str = "") -> list[SessionSummary]:
    """Claude Code sessions recorded for a directory, newest first. Tries
    the path as given AND its realpath — ~/DEV is a symlink, and sessions
    started from either spelling land in different directories."""
    files: dict[str, Path] = {}
    for root in project_roots(profile):
        for candidate in {path.rstrip("/") or "/", os.path.realpath(path)}:
            project_dir = root / encode_project_dir(candidate)
            if project_dir.is_dir():
                for f in project_dir.glob("*.jsonl"):
                    files.setdefault(f.stem, f)
    newest = sorted(files.values(), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    summaries = []
    for f in newest:
        first_prompt, count = _summarize(f)
        if first_prompt is None:
            continue
        summaries.append(SessionSummary(f.stem, datetime.fromtimestamp(f.stat().st_mtime), first_prompt, count))
    return summaries


def session_recorded_in(session_id: str, path: str, profile: str = "") -> bool:
    """Was this session really recorded in `path`? The transcript folder's name is a lossy encoding of the
    path (every / and . becomes "-"), so two folders can share it: the `cwd` the transcript itself records
    must be this folder too (#160)."""
    wanted = os.path.realpath(path)
    candidates = [
        root / encode_project_dir(candidate) / f"{session_id}.jsonl"
        for root in project_roots(profile)
        for candidate in {path.rstrip("/") or "/", wanted}
    ]
    for transcript in candidates:
        if not transcript.is_file():
            continue
        try:
            with open(transcript, "rb") as fh:
                for number, raw in enumerate(fh):
                    if number >= 200:
                        break
                    if b'"cwd"' not in raw:
                        continue
                    try:
                        cwd = json.loads(raw).get("cwd")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if isinstance(cwd, str):
                        return os.path.realpath(cwd) == wanted
        except OSError:
            continue
    return False


_MESSAGE_LINE_RE = re.compile(rb'"type":\s*"(?:user|assistant)"')


def _summarize(path: Path) -> tuple[str | None, int]:
    first_prompt = None
    count = 0
    with open(path, "rb") as fh:
        for raw in fh:
            # Cheap pre-filter: most lines (tool results, hooks, metadata)
            # aren't messages, and transcripts can be many MB.
            if not _MESSAGE_LINE_RE.search(raw):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for event in _events_from_line(data):
                if event.kind not in ("human", "assistant_text"):
                    continue
                count += 1
                # Skip harness-injected lines like <command-name>…</command-name>.
                if first_prompt is None and event.kind == "human" and not event.text.lstrip().startswith("<"):
                    first_prompt = event.text.strip().splitlines()[0][:120]
    return first_prompt, count


def last_human_text(session_id: str) -> str | None:
    """The last human message recorded in a session's transcript — used
    after a usage-limit hit to tell whether the prompt actually reached
    the conversation. None if there's no transcript or no human message."""
    path = find_transcript_path(session_id)
    if path is None:
        return None
    events, _offset = parse_transcript_events(path)
    humans = [e for e in events if e.kind == "human"]
    return humans[-1].text if humans else None
