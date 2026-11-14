"""Fixed catalogue of attributes a new conversation can be started with,
and the pure logic turning a set of ticked keys into `claude` flags."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ccc import mcp_servers, plugins
from ccc.i18n import N_, _

SESSION_NEW = "session_new"
SESSION_CONTINUE = "session_continue"
SESSION_RESUME = "session_resume"
SESSION_FROM_PR = "session_from_pr"
FORK = "fork"
MCP_SELECT = "mcp_select"
# One ticked server of the "MCP servers" group: "mcp:<name>". The names
# come from the user's Claude Code config (ccc.mcp_servers), not from GROUPS.
MCP_PREFIX = "mcp:"
PLUGINS_SELECT = "plugins_select"
# One ticked plugin of the "Plugins" group: "plugin:<id>" (ids come from the
# plugins installed in Claude Code, ccc.plugins).
PLUGIN_PREFIX = "plugin:"
AGENT_SELECT = "agent_select"
# The one ticked agent of the "Agent" group: "agent:<name>" (a Claude Code subagent
# file of the folder or the user, ccc.agents). The whole conversation runs as that
# agent (`claude --agent <name>`).
AGENT_PREFIX = "agent:"
AGENT_FLAG = "--agent"
_AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # what may end up on a command line


@dataclass(frozen=True)
class LaunchOption:
    key: str
    label: str
    flags: tuple[str, ...]
    short: str  # compact label for headers / queue detail
    # label and short are English, marked with N_(): shown through _().


@dataclass(frozen=True)
class OptionGroup:
    key: str
    title: str
    options: tuple[LaunchOption, ...]
    exclusive: bool  # ticking one unticks the rest of the group
    required: bool  # exactly one must stay ticked


GROUPS: tuple[OptionGroup, ...] = (
    OptionGroup("session", N_("Session"), (
        LaunchOption(SESSION_NEW, N_("New session"), (), N_("new")),
        LaunchOption(SESSION_CONTINUE, N_("Continue last in directory"), (), N_("continue")),
        LaunchOption(SESSION_RESUME, N_("Resume selected…"), (), N_("resume")),
        LaunchOption(SESSION_FROM_PR, N_("From pull request…"), (), N_("from PR")),
    ), exclusive=True, required=True),
    OptionGroup("session_extra", "", (
        LaunchOption(FORK, N_("As a copy (fork)"), ("--fork-session",), N_("fork")),
    ), exclusive=False, required=False),
    OptionGroup("permissions", N_("Permissions"), (
        LaunchOption("perm_skip", N_("Bypass permissions"), ("--dangerously-skip-permissions",), N_("bypass permissions")),
        LaunchOption("perm_accept_edits", N_("Accept edits"), ("--permission-mode", "acceptEdits"), N_("accept edits")),
        LaunchOption("perm_manual", N_("Manual"), ("--permission-mode", "manual"), N_("manual")),
        LaunchOption("perm_dont_ask", N_("Don't ask"), ("--permission-mode", "dontAsk"), N_("don't ask")),
        LaunchOption("perm_auto", N_("Auto"), ("--permission-mode", "auto"), N_("auto")),
        LaunchOption("perm_plan", N_("Plan"), ("--permission-mode", "plan"), N_("plan mode")),
    ), exclusive=True, required=True),
    OptionGroup("model", N_("Model"), (
        LaunchOption("model_opus", N_("Opus"), ("--model", "opus"), N_("Opus")),
        LaunchOption("model_sonnet", N_("Sonnet"), ("--model", "sonnet"), N_("Sonnet")),
        LaunchOption("model_haiku", N_("Haiku"), ("--model", "haiku"), N_("Haiku")),
        LaunchOption("model_fable", N_("Fable"), ("--model", "fable"), N_("Fable")),
    ), exclusive=True, required=False),
    OptionGroup("effort", N_("Effort"), (
        LaunchOption("effort_low", N_("low"), ("--effort", "low"), N_("effort low")),
        LaunchOption("effort_medium", N_("medium"), ("--effort", "medium"), N_("effort medium")),
        LaunchOption("effort_high", N_("high"), ("--effort", "high"), N_("effort high")),
        LaunchOption("effort_xhigh", N_("xhigh"), ("--effort", "xhigh"), N_("effort xhigh")),
        LaunchOption("effort_max", N_("max"), ("--effort", "max"), N_("effort max")),
    ), exclusive=True, required=False),
    OptionGroup("mcp", N_("MCP servers"), (
        LaunchOption(MCP_SELECT, N_("Only selected MCP servers (fewer tokens)"), (), N_("MCP")),
    ), exclusive=False, required=False),
    OptionGroup("plugins", N_("Plugins"), (
        LaunchOption(PLUGINS_SELECT, N_("Only selected plugins (predictable behaviour)"), (), N_("plugins")),
    ), exclusive=False, required=False),
    OptionGroup("agent", N_("Agent"), (
        LaunchOption(AGENT_SELECT, N_("Whole conversation as one agent"),
                     (), N_("agent")),
    ), exclusive=False, required=False),
    OptionGroup("other", N_("Other"), (
        LaunchOption("other_fallback_sonnet", N_("Fallback to Sonnet when overloaded"), ("--fallback-model", "sonnet"), N_("fallback Sonnet")),
        LaunchOption("other_chrome", N_("Claude in Chrome"), ("--chrome",), N_("Chrome")),
        LaunchOption("other_safe_mode", N_("Safe mode (no customizations)"), ("--safe-mode",), N_("safe mode")),
    ), exclusive=False, required=False),
)

# Groups whose choice decides WHICH session to talk to rather than adding
# flags to every run — handled by orchestrator.start_conversation().
SESSION_GROUP_KEYS = ("session", "session_extra")

DEFAULT_SELECTION: frozenset[str] = frozenset({SESSION_NEW, "perm_skip"})


def is_mcp_server(key: str) -> bool:
    return key.startswith(MCP_PREFIX) and mcp_servers.valid_name(key[len(MCP_PREFIX):])


def is_plugin(key: str) -> bool:
    return key.startswith(PLUGIN_PREFIX) and plugins.valid_id(key[len(PLUGIN_PREFIX):])


def is_agent(key: str) -> bool:
    return key.startswith(AGENT_PREFIX) and _AGENT_NAME.fullmatch(key[len(AGENT_PREFIX):]) is not None


def agent_names(selection) -> list[str]:
    return sorted(k[len(AGENT_PREFIX):] for k in selection if is_agent(k))


def group_of(key: str) -> OptionGroup:
    if is_agent(key):
        return next(g for g in GROUPS if g.key == "agent")
    if is_mcp_server(key):
        return next(g for g in GROUPS if g.key == "mcp")
    if is_plugin(key):
        return next(g for g in GROUPS if g.key == "plugins")
    for group in GROUPS:
        if any(o.key == key for o in group.options):
            return group
    raise KeyError(key)


def toggle(selection: frozenset[str], key: str, checked: bool) -> frozenset[str]:
    group = group_of(key)
    sel = set(selection)
    if is_mcp_server(key):
        # A server only counts while the mode is on; ticking one turns it on.
        sel.add(key) if checked else sel.discard(key)
        if checked:
            sel.add(MCP_SELECT)
        return frozenset(sel)
    if is_plugin(key):
        # Same as servers: a plugin only counts while the mode is on.
        sel.add(key) if checked else sel.discard(key)
        if checked:
            sel.add(PLUGINS_SELECT)
        return frozenset(sel)
    if is_agent(key):
        # One agent at most, and only while the mode is on; ticking one turns it on.
        sel = {k for k in sel if not is_agent(k)}
        if checked:
            sel |= {key, AGENT_SELECT}
        return frozenset(sel)
    if key == AGENT_SELECT and not checked:
        sel = {k for k in sel if not is_agent(k)}
    if key == MCP_SELECT and not checked:
        sel = {k for k in sel if not is_mcp_server(k)}
    if key == PLUGINS_SELECT and not checked:
        sel = {k for k in sel if not is_plugin(k)}
    if checked:
        if group.exclusive:
            sel -= {o.key for o in group.options}
        sel.add(key)
    elif not group.required:
        sel.discard(key)
    if SESSION_NEW in sel:
        sel.discard(FORK)  # nothing to copy from
    return frozenset(sel)


def session_mode(selection: frozenset[str]) -> str:
    for option in GROUPS[0].options:
        if option.key in selection:
            return option.key
    return SESSION_NEW


def persistent_flags(selection: frozenset[str]) -> list[str]:
    flags: list[str] = []
    for group in GROUPS:
        if group.key in SESSION_GROUP_KEYS:
            continue
        for option in group.options:
            if option.key in selection:
                flags.extend(option.flags)
    if MCP_SELECT in selection:
        flags.append(mcp_servers.marker(k[len(MCP_PREFIX):] for k in selection if is_mcp_server(k)))
    if PLUGINS_SELECT in selection:
        flags.append(plugins.marker(k[len(PLUGIN_PREFIX):] for k in selection if is_plugin(k)))
    if AGENT_SELECT in selection and agent_names(selection):
        flags += [AGENT_FLAG, agent_names(selection)[0]]
    return flags


def describe_flags(flags: list[str] | None) -> str:
    """Compact human summary of a queue's persistent flags."""
    if flags is None:
        return _("legacy (bypass permissions)")
    remaining = [f for f in flags if not f.startswith((mcp_servers.MARKER, plugins.MARKER))]
    parts: list[str] = []
    for group in GROUPS:
        if group.key in SESSION_GROUP_KEYS or group.key in ("mcp", "plugins", "agent"):
            continue
        for option in group.options:
            n = len(option.flags)
            for i in range(len(remaining) - n + 1):
                if tuple(remaining[i:i + n]) == option.flags:
                    parts.append(_(option.short))
                    del remaining[i:i + n]
                    break
    for i in range(len(remaining) - 1):
        if remaining[i] == AGENT_FLAG:
            parts.append(_("Agent: {name}").format(name=remaining[i + 1]))
            del remaining[i:i + 2]
            break
    chosen = mcp_servers.selected_names(flags)
    if chosen is not None:
        parts.append(_("MCP: {servers}").format(servers=", ".join(chosen)) if chosen else _("MCP: none"))
    chosen_plugins = plugins.selected_ids(flags)
    if chosen_plugins is not None:
        parts.append(_("Plugins: {plugins}").format(plugins=", ".join(p.split("@")[0] for p in chosen_plugins))
                     if chosen_plugins else _("Plugins: none"))
    parts.extend(remaining)
    return " · ".join(parts) if parts else _("default")
