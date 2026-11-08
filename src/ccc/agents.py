"""Claude Code's subagents as files (#220): `<name>.md` in a project's
`.claude/agents` or in the user's `~/.claude/agents`. A file is YAML
frontmatter (`name` and `description` are required; `tools`, `model` … are
optional) and the agent's system prompt as the Markdown body
(code.claude.com/docs/en/sub-agents).

This module reads them so ccc can show what Claude Code would load. It does
not need a YAML library: only the few fields ccc shows are read, everything
else in the frontmatter is left alone. Claude Code skips a broken agent
without a word; here such a file is listed with the reason (`problem`).

A project's agent wins over a user's agent of the same name, and in nested
`.claude/agents` folders the one closest to the working directory wins."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ccc import profiles
from ccc.i18n import N_

logger = logging.getLogger(__name__)

SCOPE_PROJECT = "project"
SCOPE_USER = "user"
MAX_FILE_BYTES = 256 * 1024  # an agent's prompt is a page or two; a bigger file is not one
MAX_NAME = 256


@dataclass(frozen=True)
class Agent:
    name: str
    description: str
    scope: str  # SCOPE_PROJECT / SCOPE_USER
    path: Path
    tools: tuple[str, ...] | None = None  # None: inherits every tool
    model: str = ""  # "" : Claude Code picks (the session's model)
    prompt: str = ""
    problem: str = ""  # why Claude Code would skip the file ("" when it loads)
    overridden: bool = False  # an agent of the same name closer to the project wins


def user_dir(profile: str = "") -> Path:
    return profiles.config_dir(profile) / "agents"


def project_dirs(project_path: str) -> list[Path]:
    """The `.claude/agents` folders of a project, closest first: the project
    folder and its parents up to the repository root (or the home folder)."""
    if not project_path:
        return []
    start = Path(project_path).expanduser()
    home = Path.home()
    found: list[Path] = []
    for folder in (start, *start.parents):
        if folder == home or folder == Path(folder.anchor):
            break  # the home folder's own .claude/agents is the user's scope
        found.append(folder / ".claude" / "agents")
        if (folder / ".git").exists():
            break
    return found


def valid_name(name: str) -> str:
    """Empty when Claude Code accepts `name`, else why not (English, marked for translation)."""
    if not name:
        return N_("no name")
    if len(name) > MAX_NAME:
        return N_("the name is longer than 256 characters")
    if name.startswith("-"):
        return N_("the name starts with '-'")
    if ":" in name:
        return N_("the name contains ':' (reserved for plugins)")
    return ""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split_frontmatter(text: str) -> tuple[list[str], str] | None:
    """(frontmatter lines, body) of a file; None when it has no frontmatter
    (its first line is not `---`, or it never closes)."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:]).strip("\n")
    return None


def parse_fields(lines: list[str]) -> dict[str, str | list[str]]:
    """The top-level `key: value` pairs of a frontmatter; a value is text or,
    for a YAML list (`key:` followed by `- item` lines), a list of items. A
    block text (`key: >` / `|`) is joined into one line. Nested blocks
    (hooks, mcpServers …) are not read — they stay as they are."""
    fields: dict[str, str | list[str]] = {}
    key = None
    block: list[str] | None = None  # the lines of a `>` / `|` text being collected
    for line in lines:
        if block is not None and (line[:1] in " \t" or not line.strip()):
            block.append(line.strip())
            continue
        if block is not None and key is not None:
            fields[key] = " ".join(t for t in block if t)
            block = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t-" and ":" in line:
            key, _sep, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if re.fullmatch(r"[>|][+-]?", value):
                block = []
                fields[key] = ""
            else:
                fields[key] = _unquote(value.split(" #")[0]) if value else []
        elif key is not None and line.lstrip().startswith("- ") and isinstance(fields.get(key), list):
            fields[key].append(_unquote(line.lstrip()[2:]))  # type: ignore[union-attr]
    if block is not None and key is not None:
        fields[key] = " ".join(t for t in block if t)
    return fields


def _tools(value) -> tuple[str, ...] | None:
    if value is None or value == "" or value == []:
        return None
    items = value if isinstance(value, list) else value.split(",")
    return tuple(t.strip() for t in items if t.strip()) or None


def read_file(path: Path, scope: str) -> Agent | None:
    """The agent in `path`; None for a file Claude Code treats as documentation
    (no frontmatter or no name) — and for a file that cannot be read."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parts = split_frontmatter(text)
    if parts is None:
        return None
    fields = parse_fields(parts[0])
    name = fields.get("name")
    if not isinstance(name, str) or not name:
        return None
    description = fields.get("description")
    description = description if isinstance(description, str) else ""
    problem = valid_name(name)
    if not problem and not description:
        problem = N_("no description")
    model = fields.get("model")
    return Agent(name=name, description=description, scope=scope, path=path, tools=_tools(fields.get("tools")),
                 model=model if isinstance(model, str) else "", prompt=parts[1], problem=problem)


def _read_dir(folder: Path, scope: str) -> list[Agent]:
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix == ".md" and p.is_file())
    except OSError:
        return []
    return [a for a in (read_file(p, scope) for p in files) if a is not None]


def list_agents(project_path: str = "", profile: str = "") -> list[Agent]:
    """Every agent Claude Code would find for a project: the project's, then
    the user's. A name defined twice is listed twice; the weaker is
    `overridden`. Sorted by scope (project first), then name."""
    found: list[Agent] = []
    taken: set[str] = set()
    for folder in project_dirs(project_path):  # closest first
        for agent in _read_dir(folder, SCOPE_PROJECT):
            found.append(_mark(agent, taken))
    for agent in _read_dir(user_dir(profile), SCOPE_USER):
        found.append(_mark(agent, taken))
    return sorted(found, key=lambda a: (a.scope != SCOPE_PROJECT, a.name.lower(), a.overridden))


def _mark(agent: Agent, taken: set[str]) -> Agent:
    if agent.problem:
        return agent  # Claude Code skips it; it takes no name
    if agent.name in taken:
        return replace(agent, overridden=True)
    taken.add(agent.name)
    return agent


def find(name: str, project_path: str = "", profile: str = "") -> Agent | None:
    """The agent Claude Code would use for `name` (a loadable, not overridden one)."""
    return next((a for a in list_agents(project_path, profile) if a.name == name and not a.problem
                 and not a.overridden), None)


def as_dict(agent: Agent) -> dict:
    """What the API shows of an agent (the prompt stays in the file)."""
    return {"name": agent.name, "description": agent.description, "scope": agent.scope,
            "tools": list(agent.tools) if agent.tools is not None else None, "model": agent.model,
            "problem": agent.problem, "overridden": agent.overridden, "file": agent.path.name}


def display_path(agent: Agent) -> str:
    home = str(Path.home())
    text = os.fspath(agent.path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text
