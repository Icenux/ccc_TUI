"""Which MCP servers a conversation may use. Claude Code loads every server
it finds (user, project, local, claude.ai connectors) and puts all their
tool definitions into the context of every turn; with `--strict-mcp-config`
it loads only the servers handed over with `--mcp-config`.

A conversation stores just the *names* it allows, as one marker flag
(MARKER) in its launch flags. The server definitions — which can hold
tokens in `env` / `headers` — are looked up when `claude` is started
(resolve_flags) and never stored in the database, shown or logged by ccc.
They reach `claude` through a private file (0600), not the command line,
where every local user could read them.

Servers of a project's `.mcp.json` are offered only once the user has
approved them for that project in Claude Code itself (it asks before
running them): a repository must not get a command run just because a
remembered choice names a server it defines."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from ccc import profiles

logger = logging.getLogger(__name__)

# Launch-flag marker: "--ccc-mcp=a,b" allows servers a and b, "--ccc-mcp="
# none (ccc's own server is attached by the orchestrator where approvals need it).
MARKER = "--ccc-mcp="
OWN_SERVER = "ccc"


def claude_json_path(profile: str = "") -> Path:
    if profile and profiles.get(profile):
        return profiles.config_dir(profile) / ".claude.json"
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home()) / ".claude.json"


def _read_servers(path: Path, project_path: str | None = None) -> dict[str, dict]:
    """The `mcpServers` of a JSON file — top level, or of one project's
    entry (`projects[project_path]`) in ~/.claude.json."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if project_path is not None:
        data = (data.get("projects") or {}).get(project_path) if isinstance(data, dict) else None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {name: cfg for name, cfg in servers.items() if isinstance(cfg, dict) and valid_name(name)}


def valid_name(name: str) -> bool:
    return bool(name) and name != OWN_SERVER and "," not in name and "=" not in name


def _json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def approved_project_servers(project: str, names, profile: str = "") -> set[str]:
    """Which of the `.mcp.json` servers `names` the user approved for
    `project`, as Claude Code records it: in the project's entry of
    ~/.claude.json or in the user's own settings.json (never in the
    project's, which the repository controls)."""
    entry = (_json_object(claude_json_path(profile)).get("projects") or {}).get(project)
    entry = entry if isinstance(entry, dict) else {}
    settings_dir = profiles.config_dir(profile)
    user_settings = _json_object(settings_dir / "settings.json")
    approved: set[str] = set()
    disabled: set[str] = set()
    for source in (entry, user_settings):
        if source.get("enableAllProjectMcpServers") is True:
            approved |= set(names)
        for key, into in (("enabledMcpjsonServers", approved), ("disabledMcpjsonServers", disabled)):
            listed = source.get(key)
            if isinstance(listed, list):
                into |= {n for n in listed if isinstance(n, str)}
    return approved - disabled  # a rejection anywhere wins


def discover(project_path: str, profile: str = "") -> dict[str, dict]:
    """Servers Claude Code would load in `project_path`, by name: user
    config, then the project's approved .mcp.json servers, then local (per
    project) config — the later one wins, as in Claude Code."""
    servers = _read_servers(claude_json_path(profile))
    if not project_path.strip():
        return servers  # no folder yet: only the user's own servers
    project = str(Path(project_path).expanduser())
    from_project = _read_servers(Path(project) / ".mcp.json")
    approved = approved_project_servers(project, from_project, profile)
    servers |= {name: cfg for name, cfg in from_project.items() if name in approved}
    servers |= _read_servers(claude_json_path(profile), project)
    return servers


def names(project_path: str, profile: str = "") -> list[str]:
    return sorted(discover(project_path, profile))


def config_dir() -> Path:
    """Where the private config files for `claude` go (0700)."""
    return Path(os.environ.get("CCC_MCP_DIR") or Path.home() / ".local" / "share" / "ccc" / "mcp")


def write_config(config: dict) -> str:
    """The config as a private file (0600) and its path. Named by its
    content, so a running interactive session keeps its file and the same
    choice reuses it."""
    text = json.dumps(config, sort_keys=True)
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{hashlib.sha256(text.encode()).hexdigest()[:20]}.json"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    return str(path)


def marker(selected) -> str:
    return MARKER + ",".join(sorted(selected))


def selected_names(flags: list[str] | None) -> list[str] | None:
    """Names allowed by the marker in `flags`; None when there is none
    (all servers, as before)."""
    for flag in flags or []:
        if flag.startswith(MARKER):
            return [n for n in flag[len(MARKER):].split(",") if n]
    return None


def resolve_flags(flags: list[str] | None, project_path: str, profile: str = "") -> list[str] | None:
    """`flags` with the marker replaced by what `claude` understands:
    --strict-mcp-config plus --mcp-config for the allowed servers that still
    exist. Without a marker `flags` come back unchanged."""
    if flags is None or not any(f.startswith(MARKER) for f in flags):
        return flags
    wanted = selected_names(flags) or []
    found = discover(project_path, profile)
    for name in wanted:
        if name not in found:
            logger.warning("MCP server '%s' is no longer configured; skipped", name)
    config = {"mcpServers": {name: found[name] for name in wanted if name in found}}
    resolved: list[str] = []
    for flag in flags:
        if flag.startswith(MARKER):
            resolved.append("--strict-mcp-config")
            if config["mcpServers"]:
                resolved += ["--mcp-config", write_config(config)]
        else:
            resolved.append(flag)
    return resolved
