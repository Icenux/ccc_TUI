"""Which Claude Code plugins a conversation runs with. Plugins are enabled
globally (`enabledPlugins` in ~/.claude/settings.json) and change a
conversation's behaviour: hooks add context, skills steer the model, they
bring MCP servers and take part of the context window.

A conversation stores just the *ids* it allows, as one marker flag (MARKER)
in its launch flags. When `claude` is started the marker becomes `--settings`
with an `enabledPlugins` object that switches every installed plugin on or
off; Claude Code lets it override the user's own settings. Without a marker
nothing changes: the conversation inherits what the user has enabled."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ccc import mcp_servers, profiles

logger = logging.getLogger(__name__)

# Launch-flag marker: "--ccc-plugins=a@m,b@m" runs with only these plugins,
# "--ccc-plugins=" with none.
MARKER = "--ccc-plugins="


def _config_dir(profile: str = "") -> Path:
    return profiles.config_dir(profile)


def valid_id(plugin_id: str) -> bool:
    return bool(plugin_id) and "," not in plugin_id and "=" not in plugin_id and not plugin_id.startswith("-")


def installed(profile: str = "") -> list[str]:
    """Ids (`name@marketplace`) of the installed plugins."""
    plugins = mcp_servers._json_object(_config_dir(profile) / "plugins" / "installed_plugins.json").get("plugins")
    if not isinstance(plugins, dict):
        return []
    return sorted(pid for pid in plugins if isinstance(pid, str) and valid_id(pid))


def enabled_by_default(profile: str = "") -> list[str]:
    """Installed plugins the user's own settings.json has switched on."""
    enabled = mcp_servers._json_object(_config_dir(profile) / "settings.json").get("enabledPlugins")
    if not isinstance(enabled, dict):
        return []
    return sorted(pid for pid in installed(profile) if enabled.get(pid) is True)


def marker(selected) -> str:
    return MARKER + ",".join(sorted(selected))


def selected_ids(flags: list[str] | None) -> list[str] | None:
    """Ids allowed by the marker in `flags`; None when there is none
    (the conversation inherits the user's plugins)."""
    for flag in flags or []:
        if flag.startswith(MARKER):
            return [p for p in flag[len(MARKER):].split(",") if p]
    return None


def resolve_flags(flags: list[str] | None, profile: str = "") -> list[str] | None:
    """`flags` with the marker replaced by `--settings <file>` that enables
    the allowed plugins and disables the other installed ones. Without a
    marker `flags` come back unchanged."""
    if flags is None or not any(f.startswith(MARKER) for f in flags):
        return flags
    wanted = selected_ids(flags) or []
    found = installed(profile)
    for pid in wanted:
        if pid not in found:
            logger.warning("Plugin '%s' is no longer installed; skipped", pid)
    path = mcp_servers.write_config({"enabledPlugins": {pid: pid in wanted for pid in found}})
    resolved: list[str] = []
    for flag in flags:
        resolved += ["--settings", path] if flag.startswith(MARKER) else [flag]
    return resolved
