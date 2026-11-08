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

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ccc import profiles
from ccc.i18n import N_, _

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
    stamp: tuple[int, int] = (0, 0)  # (size, mtime_ns) of the file when it was read: edits refuse a changed file


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


_DOUBLE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*(?:#.*)?')
_SINGLE = re.compile(r"'((?:[^']|'')*)'\s*(?:#.*)?")


def _unquote(value: str) -> str:
    """A scalar's text: quotes removed (a `#` inside them is text, not a comment), a trailing
    ` # comment` of an unquoted value dropped."""
    value = value.strip()
    if (m := _DOUBLE.fullmatch(value)):
        try:
            return json.loads(f'"{m.group(1)}"')  # JSON escapes: what write_agent writes
        except ValueError:
            return m.group(1)
    if (m := _SINGLE.fullmatch(value)):
        return m.group(1).replace("''", "'")
    return value.split(" #")[0].strip()


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
                fields[key] = _unquote(value) if value else []
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
        info = path.stat()
        if info.st_size > MAX_FILE_BYTES:
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
                 model=model if isinstance(model, str) else "", prompt=parts[1], problem=problem,
                 stamp=(info.st_size, info.st_mtime_ns))


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
    return display_path_of(agent.path)


# --- writing (#220) ---------------------------------------------------------------------

NEW_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # what ccc creates: also a safe file name
MODELS = ("", "sonnet", "opus", "haiku", "fable", "inherit")
_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def valid_new_name(name: str) -> str:
    """Empty when `name` may name a new agent, else why not (English, marked for translation)."""
    if not NEW_NAME.fullmatch(name):
        return N_("Use lowercase letters, digits and hyphens (at most 64), starting with a letter or digit.")
    return ""


def render(name: str, description: str, tools: tuple[str, ...] | None, model: str, prompt: str) -> str:
    """The text of an agent file. The description is written as one quoted line so that any
    text (colons, quotes, `#`) reads back the same."""
    problem = valid_new_name(name)
    if problem:
        raise ValueError(_(problem))
    description = " ".join(description.split())
    if not description:
        raise ValueError(_("An agent needs a description: Claude decides by it when to call the agent."))
    if model not in MODELS:
        raise ValueError(_("Unknown model: {model}").format(model=model))
    if tools is not None and not (tools and all(_TOOL.fullmatch(t) for t in tools)):
        raise ValueError(_("Unknown tool list."))
    lines = ["---", f"name: {name}", f"description: {json.dumps(description, ensure_ascii=False)}"]
    if tools is not None:
        lines.append("tools: " + ", ".join(tools))
    if model:
        lines.append(f"model: {model}")
    lines += ["---", "", prompt.strip(), ""]
    return "\n".join(lines)


def folder_exists(project_path: str) -> bool:
    return bool(project_path) and Path(project_path).expanduser().is_dir()


def folder_for(scope: str, project_path: str = "", profile: str = "") -> Path:
    if scope == SCOPE_USER:
        return user_dir(profile)
    if scope == SCOPE_PROJECT and folder_exists(project_path):
        return Path(project_path).expanduser() / ".claude" / "agents"
    raise ValueError(_("Choose where the agent is kept."))


def write_agent(name: str, description: str, tools: tuple[str, ...] | None, model: str, prompt: str,
                scope: str, project_path: str = "", profile: str = "") -> tuple[Path, bool]:
    """Create `<name>.md` in the scope's folder; (its path, whether the folder is new).
    Never overwrites: an existing file is refused. Claude Code watches only the agent
    folders that existed when it started, so a new folder needs a restart there."""
    text = render(name, description, tools, model, prompt)
    folder = folder_for(scope, project_path, profile)
    new_folder = not folder.exists()
    path = folder / f"{name}.md"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        raise ValueError(_("{file} already exists.").format(file=display_path_of(path))) from None
    except OSError as error:
        raise ValueError(_("Could not write the agent: {error}").format(error=error.strerror or error)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path, new_folder


def display_path_of(path: Path) -> str:
    home = str(Path.home())
    text = os.fspath(path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


# --- editing and deleting (#220) ----------------------------------------------------------


def _is_key_line(line: str) -> bool:
    return bool(line) and line[0] not in " \t-#" and ":" in line


def replace_fields(front: list[str], updates: dict[str, str | None]) -> list[str]:
    """`front` (the frontmatter's lines) with the given top-level fields set to the new `key: value`
    line (None removes the field). A field's own lines — a list, a block text — go with it; every
    other line (hooks, mcpServers, comments …) stays exactly as it was. A missing field is added."""
    out: list[str] = []
    seen: set[str] = set()
    skipping = False
    for line in front:
        if _is_key_line(line):
            key = line.partition(":")[0].strip()
            skipping = key in updates
            if skipping:
                seen.add(key)
                if updates[key] is not None:
                    out.append(updates[key])
                continue
        elif skipping:
            continue  # the continuation of a field that was replaced
        out.append(line)
    out += [value for key, value in updates.items() if key not in seen and value is not None]
    return out


def _tools_line(tools: tuple[str, ...] | None) -> str | None:
    if tools is None:
        return None
    if not (tools and all(_TOOL.fullmatch(t) for t in tools)):
        raise ValueError(_("Unknown tool list."))
    return "tools: " + ", ".join(tools)


def update_agent(agent: Agent, description: str, tools: tuple[str, ...] | None, model: str, prompt: str) -> None:
    """Change an agent's description, tools, model and prompt in its file; the name and every other
    field stay as they are. ValueError (a message for the user) when the file is a symbolic link,
    has changed since it was read, or a value is not valid."""
    path = agent.path
    if path.is_symlink():
        raise ValueError(_("{file} is a symbolic link: change its target in an editor.").format(
            file=display_path_of(path)))
    description = " ".join(description.split())
    if not description:
        raise ValueError(_("An agent needs a description: Claude decides by it when to call the agent."))
    if model != agent.model and model not in MODELS:  # a model the file already has is left alone
        raise ValueError(_("Unknown model: {model}").format(model=model))
    updates = {"description": f"description: {json.dumps(description, ensure_ascii=False)}",
               "tools": _tools_line(tools), "model": f"model: {model}" if model else None}
    try:
        info = path.stat()
        if (info.st_size, info.st_mtime_ns) != agent.stamp:
            raise ValueError(_("The file changed on disk since the window was opened; open it again."))
        parts = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(_("Could not read the agent: {error}").format(error=getattr(error, "strerror", error))) from None
    if parts is None:
        raise ValueError(_("The file has no frontmatter any more; open it again."))
    front = replace_fields(parts[0], updates)
    text = "\n".join(["---", *front, "---", "", prompt.strip(), ""])
    # The scratch file is made fresh under an unpredictable name (O_EXCL): the folder may come from a
    # cloned repository, and a symbolic link planted at a guessable name must not be followed.
    scratch = None
    try:
        fd, scratch = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".ccc-tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(scratch, info.st_mode & 0o777)
        os.replace(scratch, path)
    except OSError as error:
        if scratch is not None:
            Path(scratch).unlink(missing_ok=True)
        raise ValueError(_("Could not write the agent: {error}").format(error=error.strerror or error)) from None


def trash_dir() -> Path:
    return Path(os.environ.get("CCC_AGENT_TRASH") or Path.home() / ".local" / "share" / "ccc" / "deleted-agents")


def delete_agent(agent: Agent) -> Path:
    """Take an agent away from Claude Code: its file moves to ccc's own folder (not deleted for good),
    and that path is returned. Only a file ccc found in an agent folder is touched."""
    path = agent.path
    if path.suffix != ".md" or path.parent.name != "agents":
        raise ValueError(_("{file} is not an agent file.").format(file=display_path_of(path)))
    target_dir = trash_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{stamp}-{agent.scope}-{path.name}"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o700)
        shutil.move(os.fspath(path), os.fspath(target))
    except OSError as error:
        raise ValueError(_("Could not remove the agent: {error}").format(error=error.strerror or error)) from None
    return target
