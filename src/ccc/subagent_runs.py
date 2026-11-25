"""What the subagents of a Claude Code session did (#286): when each was called, by whom, how long it worked,
what it was told, step by step what it ran, and how it ended. ccc.api_subagent_runs serves it, the TUI and the
Android app show it. (ccc.agents is about subagent *definitions*; this is about their *runs*.)

Everything is read from Claude Code's own transcripts, nothing is stored by ccc:
  <project>/<session>.jsonl                          the main conversation: the `Agent`/`Task` call, its result and,
                                                     for a background agent, a <task-notification> when it ends
  <project>/<session>/subagents/agent-<id>.jsonl     the agent's own transcript (every step it took)
  <project>/<session>/subagents/agent-<id>.meta.json {"agentType", "description", "toolUseId", "finished", ...}
A subagent that starts another one has the `Agent` call in its own transcript, so the runs form a tree
(`parent_id` is "" for a run called by the conversation itself).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ccc.transcript import find_transcript_path

AGENT_TOOLS = ("Agent", "Task")
RUNNING, DONE, ERROR, STOPPED = "running", "done", "error", "stopped"
# A run that never wrote "finished" and has been silent this long was cut off (Claude Code closed, the agent killed).
STALE_SECONDS = 15 * 60
PROMPT_MAX = 20000
RESULT_MAX = 20000
ARG_MAX = 200

_NOTIFICATION = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


def _tag(text: str, name: str) -> str:
    found = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return found.group(1).strip() if found else ""


@dataclass
class Step:
    index: int
    tool: str
    arg: str
    started: datetime | None
    offset_ms: int  # from the start of the run
    duration_ms: int | None  # None while the tool still runs
    error: str = ""  # the first line of the error, "" when the step went well
    agent_id: str = ""  # an `Agent` step: the run it started

    def as_dict(self) -> dict:
        return {"index": self.index, "tool": self.tool, "arg": self.arg,
                "started": self.started.isoformat() if self.started else None, "offset_ms": self.offset_ms,
                "duration_ms": self.duration_ms, "error": self.error, "agent_id": self.agent_id}


@dataclass
class Run:
    id: str  # the agentId; also the name of its transcript
    parent_id: str  # "" = called by the conversation
    kind: str  # subagent_type: general-purpose, Explore, …
    description: str
    status: str = RUNNING
    started: datetime | None = None
    ended: datetime | None = None
    duration_ms: int = 0
    tokens: int = 0
    tool_count: int = 0
    model: str = ""
    prompt: str = ""
    result: str = ""
    tool_use_id: str = ""
    depth: int = 1
    background: bool = False
    steps: list[Step] = field(default_factory=list)

    def as_dict(self, detail: bool = False) -> dict:
        out = {
            "id": self.id, "parent_id": self.parent_id, "kind": self.kind, "description": self.description,
            "status": self.status, "started": self.started.isoformat() if self.started else None,
            "ended": self.ended.isoformat() if self.ended else None, "duration_ms": self.duration_ms,
            "tokens": self.tokens, "tool_count": self.tool_count, "model": self.model, "depth": self.depth,
            "background": self.background,
        }
        if detail:
            out.update({"prompt": self.prompt, "result": self.result, "steps": [s.as_dict() for s in self.steps]})
        return out


def _when(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _lines(path: Path):
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    yield data
    except OSError:
        return


def _blocks(row: dict) -> list[dict]:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _arg_of(tool: str, data) -> str:
    """The one value that tells what a call did: the command, the file, the pattern, the question."""
    if not isinstance(data, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:ARG_MAX]
    return ""


def _tokens_of(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:ARG_MAX]
    return ""


@dataclass
class _Called:
    """An `Agent` call seen in a transcript, before we know which run it started."""
    tool_use_id: str
    owner: str  # "" = the conversation, else the agentId whose transcript holds the call
    kind: str
    description: str
    prompt: str
    at: datetime | None
    background: bool


@dataclass
class _Outcome:
    status: str = ""
    result: str = ""
    duration_ms: int = 0
    tokens: int = 0
    tool_count: int = 0


def _scan(path: Path, owner: str, called: dict[str, _Called], by_agent: dict[str, str],
          outcomes: dict[str, _Outcome]) -> None:
    """Collect the `Agent` calls of one transcript, which agent each call started (the tool result names it)
    and — in the main one — how a background agent ended (<task-notification>)."""
    for row in _lines(path):
        if row.get("type") == "queue-operation":
            _notified(row, outcomes)
            continue
        for block in _blocks(row):
            if block.get("type") == "tool_use" and block.get("name") in AGENT_TOOLS:
                data = block.get("input") if isinstance(block.get("input"), dict) else {}
                called[block.get("id", "")] = _Called(
                    block.get("id", ""), owner, str(data.get("subagent_type") or "general-purpose"),
                    str(data.get("description") or ""), str(data.get("prompt") or ""), _when(row.get("timestamp")),
                    bool(data.get("run_in_background")))
            elif block.get("type") == "tool_result":
                result = row.get("toolUseResult")
                if isinstance(result, dict) and result.get("agentId"):
                    by_agent[str(result["agentId"])] = block.get("tool_use_id", "")
                    if result.get("status") not in ("async_launched", None) or "totalDurationMs" in result:
                        outcomes[str(result["agentId"])] = _Outcome(
                            DONE if result.get("status") in ("completed", None) else ERROR,
                            _text_of(result.get("content")), int(result.get("totalDurationMs") or 0),
                            int(result.get("totalTokens") or 0), int(result.get("totalToolUseCount") or 0))
                elif block.get("is_error"):
                    pass  # a call that never started a run (denied, bad arguments): nothing to show


def _notified(row: dict, outcomes: dict[str, _Outcome]) -> None:
    content = row.get("content")
    if not isinstance(content, str) or "<task-notification>" not in content:
        return
    for body in _NOTIFICATION.findall(content):
        task = _tag(body, "task-id")
        if not task:
            continue
        status = _tag(body, "status")
        digits = lambda name: int(re.sub(r"\D", "", _tag(body, name)) or 0)  # noqa: E731
        outcomes[task] = _Outcome(DONE if status == "completed" else ERROR if status else "",
                                  _tag(body, "result"), digits("duration_ms"), digits("subagent_tokens"),
                                  digits("tool_uses"))


def _read_run(path: Path, meta: dict, now: float) -> tuple[Run, list[_Called]]:
    """One subagent's own transcript: prompt, model, steps (a call and its result), tokens, end."""
    run = Run(id=path.stem.removeprefix("agent-"), parent_id="", kind=str(meta.get("agentType") or "general-purpose"),
              description=str(meta.get("description") or ""), tool_use_id=str(meta.get("toolUseId") or ""),
              depth=int(meta.get("spawnDepth") or 1), background=meta.get("requestShape") == "background")
    pending: dict[str, Step] = {}
    spent: dict[str, int] = {}  # per message: one message is written more than once while it streams
    last_text = ""
    last_row: datetime | None = None
    nested: list[_Called] = []
    for row in _lines(path):
        at = _when(row.get("timestamp"))
        if at:
            run.started = run.started or at
            last_row = at
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if row.get("type") == "user" and not run.prompt:
            run.prompt = _text_of(message.get("content"))[:PROMPT_MAX]
        if row.get("type") != "assistant" and row.get("type") != "user":
            continue
        if row.get("type") == "assistant":
            run.model = run.model or str(message.get("model") or "")
            spent[str(message.get("id") or row.get("uuid"))] = _tokens_of(message.get("usage"))
        for block in _blocks(row):
            kind = block.get("type")
            if kind == "text" and row.get("type") == "assistant":
                last_text = block.get("text", "")
            elif kind == "tool_use":
                step = Step(len(run.steps), str(block.get("name") or "?"), _arg_of(str(block.get("name")), block.get("input")),
                            at, int((at - run.started).total_seconds() * 1000) if at and run.started else 0, None)
                run.steps.append(step)
                pending[block.get("id", "")] = step
                if block.get("name") in AGENT_TOOLS:
                    data = block.get("input") if isinstance(block.get("input"), dict) else {}
                    nested.append(_Called(block.get("id", ""), run.id, str(data.get("subagent_type") or "general-purpose"),
                                          str(data.get("description") or ""), str(data.get("prompt") or ""), at,
                                          bool(data.get("run_in_background"))))
            elif kind == "tool_result":
                step = pending.pop(block.get("tool_use_id", ""), None)
                if step is None:
                    continue
                if at and step.started:
                    step.duration_ms = max(int((at - step.started).total_seconds() * 1000), 0)
                if block.get("is_error"):
                    step.error = _first_line(_text_of(block.get("content"))) or "error"
                inner = row.get("toolUseResult")
                if isinstance(inner, dict) and inner.get("agentId"):
                    step.agent_id = str(inner["agentId"])
    run.ended = last_row
    run.tool_count = len(run.steps)
    run.tokens = sum(spent.values())
    run.result = last_text[:RESULT_MAX]
    run.duration_ms = int((last_row - run.started).total_seconds() * 1000) if last_row and run.started else 0
    if meta.get("finished"):
        run.status = DONE
    else:
        silent = now - path.stat().st_mtime if path.exists() else 0
        run.status = STOPPED if silent > STALE_SECONDS else RUNNING
    return run, nested


def _subagent_dir(transcript: Path) -> Path:
    return transcript.parent / transcript.stem / "subagents"


def read_runs(session_id: str, now: float | None = None) -> list[Run]:
    """Every subagent run of a session — those of the conversation and of other subagents — oldest first, each
    with its steps. [] for a session without any (or without a transcript)."""
    main = find_transcript_path(session_id)
    if main is None:
        return []
    now = time.time() if now is None else now
    called: dict[str, _Called] = {}
    by_agent: dict[str, str] = {}
    outcomes: dict[str, _Outcome] = {}
    _scan(main, "", called, by_agent, outcomes)
    runs: dict[str, Run] = {}
    folder = _subagent_dir(main)
    if folder.is_dir():
        for file in sorted(folder.glob("agent-*.jsonl")):
            try:
                meta = json.loads(file.with_suffix(".meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            run, nested = _read_run(file, meta, now)
            runs[run.id] = run
            for call in nested:
                called.setdefault(call.tool_use_id, call)
            _scan_nested(file, run.id, by_agent, outcomes)
    owner_of: dict[str, str] = {}
    for agent_id, tool_use_id in by_agent.items():
        owner_of[agent_id] = tool_use_id
    for run in runs.values():
        call = called.get(run.tool_use_id or owner_of.get(run.id, ""))
        if call:
            run.parent_id = call.owner
            run.kind = run.kind if run.kind != "general-purpose" or not call.kind else call.kind
            run.description = run.description or call.description
            run.prompt = call.prompt or run.prompt
            run.started = run.started or call.at
            run.background = run.background or call.background
        outcome = outcomes.get(run.id)
        if outcome:
            if outcome.status:
                run.status = outcome.status if run.status != RUNNING or outcome.status != DONE else DONE
            run.duration_ms = run.duration_ms or outcome.duration_ms  # the notification times only the last stretch of a resumed agent
            run.tokens = outcome.tokens or run.tokens
            run.tool_count = outcome.tool_count or run.tool_count
            run.result = (outcome.result or run.result)[:RESULT_MAX]
        elif run.status == DONE and run.steps and run.steps[-1].error:
            run.status = ERROR
        depth = 1
        up = run.parent_id
        while up and up in runs and depth < 20:
            depth += 1
            up = runs[up].parent_id
        run.depth = depth
    return sorted(runs.values(), key=lambda r: (r.started.timestamp() if r.started else 0.0, r.id))


def _scan_nested(path: Path, owner: str, by_agent: dict[str, str], outcomes: dict[str, _Outcome]) -> None:
    called: dict[str, _Called] = {}
    _scan(path, owner, called, by_agent, outcomes)


def find_run(session_id: str, agent_id: str) -> Run | None:
    return next((r for r in read_runs(session_id) if r.id == agent_id), None)
