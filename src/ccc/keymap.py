"""ccc's keyboard shortcuts — every one of them can be changed (Settings →
Keyboard shortcuts, or `ccc keys`), but never into a collision.

ACTIONS lists each shortcut once: its id (the Textual Binding id, so
App.set_keymap() moves it), its default key and where it works
(contexts). The TUI builds its bindings from here (binding()), the help
lists them, ccc's tmux binds the `tmux` ones so they also work from the
Claude Code pane. Changes are kept as overrides of the defaults in the
`keymap` setting (JSON {id: key}) and can be exported to / imported from
a JSON file in any folder.

check() says why a key can't be used for an action; a keymap with any
problem is never saved or imported. A problem is:
- another action on the same key where both work at once (a context in
  common),
- a key the terminal can't tell apart from another one (Ctrl+I is Tab…)
  or that ccc's tmux takes (Ctrl+B, Ctrl+Shift+C/V),
- a key the lists and text fields need (arrows, Enter, Tab, editing
  keys) — unless it's the action's own default,
- for keys bound in tmux: a key Claude Code uses, or one without Ctrl /
  Alt (tmux would take it from everything typed into Claude Code)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ccc.i18n import N_, _

SETTING_KEY = "keymap"
EXPORT_NAME = "ccc-keys.json"
FORMAT = "ccc-keys"

# Where a key works. The sidebar views; the windows on top of them (a
# window's keys work only while it's open); "claude": the Claude Code
# pane beside ccc (only the keys ccc binds in tmux reach ccc from there).
VIEWS = ("dashboard", "conversations", "archive", "sessions", "analytics", "queue_detail")
WINDOWS = ("help", "confirm", "approval", "tasks", "task_edit", "preset_edit", "settings", "keys",
           "new_conversation", "preset_name", "add_prompt", "search", "attach", "routines", "routine_edit", "agents", "agent_new", "agent_edit",
           "review", "goal", "workflow", "group", "rename", "update")
CLAUDE = "claude"
EVERYWHERE = (*VIEWS, *WINDOWS, CLAUDE)
_CONVERSATIONS = ("conversations", "archive")
_BACK = ("conversations", "archive", "sessions", "analytics", "queue_detail")
_EDITORS = ("task_edit", "preset_edit", "settings", "keys", "routine_edit", "review", "goal", "workflow", "agent_new", "agent_edit",
            "new_conversation")


@dataclass(frozen=True)
class KeyAction:
    id: str
    default: str  # Textual key name
    label: str  # N_() text, translated where shown
    contexts: tuple[str, ...]
    tmux: bool = False  # also bound in ccc's tmux: works from the Claude Code pane


ACTIONS: tuple[KeyAction, ...] = (
    KeyAction("views.dashboard", "d", N_("Dashboard"), VIEWS),
    KeyAction("views.conversations", "c", N_("Conversations"), VIEWS),
    KeyAction("views.archive", "h", N_("Archive (archived conversations)"), VIEWS),
    KeyAction("views.sessions", "s", N_("Live sessions"), VIEWS),
    KeyAction("views.analytics", "a", N_("Analytics"), VIEWS),
    KeyAction("views.new_conversation", "n", N_("New conversation"), VIEWS),
    KeyAction("views.help", "question_mark", N_("This help"), (*VIEWS, "help")),
    KeyAction("views.routines", "o", N_("Routines (prompts on a schedule)"), VIEWS),
    KeyAction("views.agents", "A", N_("Agents (helpers Claude Code can call)"), VIEWS),
    # In Conversations and Archive t is conversations.tasks (same window).
    KeyAction("views.tasks", "t", N_("Tasks of the selected conversation"),
              tuple(v for v in VIEWS if v not in _CONVERSATIONS)),
    KeyAction("views.update", "U", N_("Update ccc (when there is a new version)"), VIEWS),
    KeyAction("app.settings", "ctrl+p", N_("Settings"), EVERYWHERE, tmux=True),
    KeyAction("app.screenshot", "alt+s", N_("Screenshot of ccc"), EVERYWHERE, tmux=True),
    KeyAction("app.quit", "ctrl+q", N_("Close ccc"), EVERYWHERE, tmux=True),
    KeyAction("app.jump_waiting", "alt+n", N_("Open the next conversation that needs you"), EVERYWHERE, tmux=True),
    KeyAction("app.external_editor", "ctrl+e", N_("Write the text in an external editor (in a text box)"),
              (*VIEWS, *WINDOWS)),
    KeyAction("screen.back", "escape", N_("Back"), _BACK),
    KeyAction("window.cancel", "escape", N_("Close the window / cancel"), WINDOWS),
    KeyAction("window.save", "ctrl+s", N_("Save"), _EDITORS),
    KeyAction("dashboard.quit", "q", N_("Close ccc (on the Dashboard)"), ("dashboard",)),
    KeyAction("dashboard.archive", "x", N_("Archive the conversation (Dashboard)"), ("dashboard",)),
    KeyAction("dashboard.delete", "delete", N_("Delete the conversation (Dashboard)"), ("dashboard",)),
    KeyAction("dashboard.filter", "slash", N_("Filter the Dashboard (all, running, pending, blocked)"), ("dashboard",)),
    KeyAction("screen.retry", "r", N_("Retry a blocked queue / resume one paused at a limit"),
              (*_CONVERSATIONS, "queue_detail")),
    KeyAction("screen.add_prompt", "plus", N_("Add prompt"), ("sessions", "queue_detail")),
    KeyAction("sessions.refresh", "r", N_("Reload the session list"), ("sessions",)),
    KeyAction("screen.review", "v", N_("Review the conversation's changes"), (*_CONVERSATIONS, "queue_detail")),
    KeyAction("screen.goal", "g", N_("Goal loop: work in rounds until a reviewer says it's met"),
              (*_CONVERSATIONS, "queue_detail")),
    KeyAction("screen.workflow", "w", N_("Workflow: a chain of agents (plan, build, reviews, arbiter)"),
              (*_CONVERSATIONS, "queue_detail")),
    KeyAction("conversations.tasks", "t", N_("Tasks editor"), _CONVERSATIONS),
    KeyAction("conversations.archive", "x", N_("Archive / restore the conversation"), _CONVERSATIONS),
    KeyAction("conversations.approve", "p", N_("Approve a tool call"), _CONVERSATIONS),
    KeyAction("conversations.search", "slash", N_("Search: Conversations, Archive or everywhere"), _CONVERSATIONS),
    KeyAction("conversations.filter", "f", N_("Filter the list (name, folder, state)"), _CONVERSATIONS),
    KeyAction("conversations.rename", "f2", N_("Rename the conversation"), _CONVERSATIONS),
    KeyAction("conversations.group", "m", N_("Put the conversation into a group / rename the group"), _CONVERSATIONS),
    KeyAction("conversations.next_group", "ctrl+down", N_("Next group in the list"), _CONVERSATIONS),
    KeyAction("conversations.prev_group", "ctrl+up", N_("Previous group in the list"), _CONVERSATIONS),
    KeyAction("conversations.export", "e", N_("Export the conversation as Markdown"), _CONVERSATIONS),
    KeyAction("conversations.focus_claude", "alt+0", N_("Switch between ccc and Claude Code"),
              (*_CONVERSATIONS, CLAUDE), tmux=True),
    KeyAction("conversations.close_claude", "ctrl+right_square_bracket", N_("Close Claude Code beside ccc"),
              (*_CONVERSATIONS, CLAUDE), tmux=True),
    *[KeyAction(f"conversations.preset{n}", f"alt+{n}", N_("Preset prompt {n}"), (*_CONVERSATIONS, CLAUDE), tmux=True)
      for n in range(1, 10)],
    KeyAction("archive.delete", "delete", N_("Delete the conversation (Archive)"), ("archive",)),
    KeyAction("tasks.add", "a", N_("Add a task"), ("tasks",)),
    KeyAction("tasks.edit", "e", N_("Edit the task"), ("tasks",)),
    KeyAction("tasks.remove", "delete", N_("Remove the task"), ("tasks",)),
    KeyAction("tasks.up", "ctrl+up", N_("Move up"), ("tasks",)),
    KeyAction("tasks.down", "ctrl+down", N_("Move down"), ("tasks",)),
    KeyAction("tasks.pause", "p", N_("Pause / resume the queue"), ("tasks",)),
    KeyAction("tasks.auto", "o", N_("Toggle auto-continue"), ("tasks",)),
    KeyAction("routines.add", "a", N_("Add a routine"), ("routines",)),
    KeyAction("routines.edit", "e", N_("Edit the routine"), ("routines",)),
    KeyAction("routines.toggle", "space", N_("Turn the routine on / off"), ("routines",)),
    KeyAction("routines.run", "r", N_("Run the routine now"), ("routines",)),
    KeyAction("routines.delete", "delete", N_("Delete the routine"), ("routines",)),
    KeyAction("agents.add", "a", N_("Add an agent from a template"), ("agents",)),
    KeyAction("agents.edit", "e", N_("Edit the agent"), ("agents",)),
    KeyAction("agents.delete", "delete", N_("Delete the agent"), ("agents",)),
    KeyAction("answer.yes", "y", N_("Yes / allow"), ("approval", "confirm")),
    KeyAction("approval.always", "a", N_("Always allow this tool in this conversation"), ("approval",)),
    KeyAction("answer.no", "n", N_("No / deny"), ("approval", "confirm")),
    KeyAction("confirm.enter", "enter", N_("Yes (confirmation)"), ("confirm",)),
    KeyAction("analytics.prev", "left", N_("Previous calendar month"), ("analytics",)),
    KeyAction("analytics.next", "right", N_("Next calendar month"), ("analytics",)),
)
BY_ID = {a.id: a for a in ACTIONS}

# Keys the terminal can't tell apart from another key, or that ccc's tmux
# takes before ccc sees them.
RESERVED: dict[str, str] = {
    "ctrl+c": N_("copies / interrupts everywhere"),
    "ctrl+i": N_("the terminal sends it as Tab"),
    "ctrl+m": N_("the terminal sends it as Enter"),
    "ctrl+left_square_bracket": N_("the terminal sends it as Esc"),
    "ctrl+h": N_("the terminal sends it as Backspace"),
    "ctrl+b": N_("tmux's prefix key"),
    "ctrl+shift+c": N_("copies the selection (ccc's tmux)"),
    "ctrl+shift+v": N_("pastes (ccc's tmux)"),
}
# Keys lists and text fields use; only allowed as the action's own default.
WIDGET_KEYS = {
    "enter", "tab", "shift+tab", "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
    "backspace", "space", "ctrl+j", "ctrl+a", "ctrl+e", "ctrl+k", "ctrl+u", "ctrl+w", "ctrl+x",
    "ctrl+v", "ctrl+z", "ctrl+y", "ctrl+f",
}
# Claude Code's own keys (code.claude.com/docs/en/keybindings: Global,
# Chat, Agents, reserved shortcuts) — a key ccc binds in tmux would take
# them from Claude Code.
CLAUDE_KEYS = {
    "ctrl+c", "ctrl+d", "ctrl+t", "ctrl+o", "ctrl+l", "ctrl+g", "ctrl+s", "ctrl+v", "ctrl+r", "ctrl+j",
    "ctrl+x", "ctrl+underscore", "ctrl+z", "ctrl+a", "ctrl+e", "ctrl+k", "ctrl+u", "ctrl+w", "ctrl+y",
    "ctrl+f", "ctrl+n", "ctrl+enter", "ctrl+up", "ctrl+down", "alt+p", "alt+o", "alt+t", "alt+v", "alt+m",
    "alt+up", "alt+down", "tab", "shift+tab", "escape", "enter", "up", "down", "space",
}

_MODIFIERS = ("ctrl", "alt", "shift")
_NAMED = {
    "esc": "escape", "escape": "escape", "enter": "enter", "return": "enter", "tab": "tab", "space": "space",
    "backspace": "backspace", "del": "delete", "delete": "delete", "insert": "insert", "ins": "insert",
    "up": "up", "down": "down", "left": "left", "right": "right", "↑": "up", "↓": "down", "←": "left",
    "→": "right", "home": "home", "end": "end", "pageup": "pageup", "pgup": "pageup", "pagedown": "pagedown",
    "pgdn": "pagedown", "plus": "plus", "minus": "minus",
    **{f"f{n}": f"f{n}" for n in range(1, 25)},
}
_PRETTY = {
    "question_mark": "?", "escape": "Esc", "enter": "Enter", "slash": "/", "delete": "Del", "plus": "+",
    "minus": "-", "space": "Space", "tab": "Tab", "backspace": "Backspace", "left": "←", "right": "→",
    "up": "↑", "down": "↓", "home": "Home", "end": "End", "pageup": "PgUp", "pagedown": "PgDn",
    "insert": "Ins", "right_square_bracket": "]", "left_square_bracket": "[", "underscore": "_",
}
_TMUX = {
    "question_mark": "?", "slash": "/", "escape": "Escape", "enter": "Enter", "delete": "DC", "insert": "IC",
    "space": "Space", "tab": "Tab", "backspace": "BSpace", "left": "Left", "right": "Right", "up": "Up",
    "down": "Down", "home": "Home", "end": "End", "pageup": "PPage", "pagedown": "NPage", "plus": "+",
    "minus": "-", "right_square_bracket": "]", "left_square_bracket": "[", "underscore": "_",
}


def _split(key: str) -> tuple[list[str], str]:
    parts = key.split("+")
    return parts[:-1], parts[-1]


def parse(text: str) -> str | None:
    """A key as the user writes it ("Ctrl+P", "alt+s", "+", "?", "F5",
    "Esc") → Textual's key name ("ctrl+p", "plus", "question_mark");
    None if it isn't a key."""
    from textual.keys import _character_to_key

    text = text.strip()
    if not text:
        return None
    if text.endswith("++"):  # "Ctrl++"
        mods, base = text[:-2].split("+") if text[:-2] else [], "+"
        mods = [m for m in mods if m]
    elif text == "+":
        mods, base = [], "+"
    else:
        *mods, base = text.split("+")
    mods = [m.strip().lower().replace("control", "ctrl").replace("meta", "alt").replace("option", "alt")
            for m in mods]
    if any(m not in _MODIFIERS for m in mods) or len(set(mods)) != len(mods) or not base:
        return None
    lower = base.lower()
    if lower in _NAMED:
        name = _NAMED[lower]
    elif lower in _PRETTY.values() and len(base) == 1:
        name = _character_to_key(base)
    elif len(base) == 1 and base.isprintable() and base != " ":
        # "A" is Shift+a as the terminal reports it; with Ctrl/Alt it's "a".
        name = _character_to_key(base.lower() if mods else base)
    else:
        name = lower.replace(" ", "_")
        if name not in _known_names():
            return None
    ordered = [m for m in _MODIFIERS if m in mods]
    return "+".join([*ordered, name])


def _known_names() -> set[str]:
    from textual.keys import Keys

    names = {k.value for k in Keys if "+" not in k.value}
    return names | set(_NAMED.values()) | set(_PRETTY)


def pretty(key: str) -> str:
    """ctrl+p → Ctrl+P, question_mark → ?, alt+s → Alt+S."""
    from textual.keys import key_to_character

    mods, base = _split(key) if key not in ("plus",) else ([], key)
    if base in _PRETTY:
        shown = _PRETTY[base]
    elif base.startswith("f") and base[1:].isdigit():
        shown = base.upper()
    else:
        shown = key_to_character(base) or base
        if mods and len(shown) == 1:
            shown = shown.upper()
    return "+".join([m.capitalize() for m in mods] + [shown])


def tmux_key(key: str) -> str | None:
    """Textual key → tmux's name for it (ctrl+q → C-q, alt+s → M-s);
    None for keys tmux can't name."""
    from textual.keys import key_to_character

    mods, base = _split(key) if key != "plus" else ([], key)
    if base in _TMUX:
        name = _TMUX[base]
    elif base.startswith("f") and base[1:].isdigit():
        name = base.upper()
    else:
        name = key_to_character(base) or ""
        if len(name) != 1:
            return None
    prefix = {"ctrl": "C-", "alt": "M-", "shift": "S-"}
    return "".join(prefix[m] for m in mods) + name


def defaults() -> dict[str, str]:
    return {a.id: a.default for a in ACTIONS}


def effective(overrides: dict[str, str]) -> dict[str, str]:
    """Every action's key with `overrides` applied (unknown ids ignored)."""
    keys = defaults()
    keys.update({i: k for i, k in overrides.items() if i in keys})
    return keys


def overrides_of(keys: dict[str, str]) -> dict[str, str]:
    return {i: k for i, k in keys.items() if i in BY_ID and k != BY_ID[i].default}


def load(storage) -> dict[str, str]:
    """The saved overrides (a broken setting = none)."""
    try:
        data = json.loads(storage.get_setting(SETTING_KEY, "") or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {i: k for i, k in data.items() if i in BY_ID and isinstance(k, str)}


def current(storage) -> dict[str, str]:
    return effective(load(storage))


def key_of(storage, action_id: str) -> str:
    return current(storage)[action_id]


def problems(action_id: str, key: str, keys: dict[str, str]) -> list[str]:
    """Why `key` can't be `action_id`'s key, the other keys being `keys`
    (translated sentences; empty = it's free)."""
    action = BY_ID[action_id]
    found: list[str] = []
    if parse(key) != key:
        return [_("“{key}” isn't a key ccc knows.").format(key=key)]
    if key in RESERVED:
        found.append(_("{key} can't be used: {why}.").format(key=pretty(key), why=_(RESERVED[key])))
    if key in WIDGET_KEYS and key != action.default:
        found.append(_("{key} is used by lists and text fields.").format(key=pretty(key)))
    if action.tmux:
        mods, base = _split(key)
        if not ({"ctrl", "alt"} & set(mods)) and not (base.startswith("f") and base[1:].isdigit()):
            found.append(_("{key} works from the Claude Code pane too, so it needs Ctrl or Alt (or F1–F12) — "
                           "otherwise it couldn't be typed into Claude Code.").format(key=pretty(key)))
        if key in CLAUDE_KEYS:
            found.append(_("{key} is a Claude Code key.").format(key=pretty(key)))
        if tmux_key(key) is None:
            found.append(_("{key} can't be bound in tmux.").format(key=pretty(key)))
    for other in ACTIONS:
        if other.id == action_id or keys.get(other.id) != key:
            continue
        if set(other.contexts) & set(action.contexts):
            found.append(_("{key} is already “{label}”.").format(key=pretty(key), label=_(other.label).format(n=_preset_n(other))))
    return found


def _preset_n(action: KeyAction) -> str:
    return action.id.removeprefix("conversations.preset") if action.id.startswith("conversations.preset") else ""


def label(action: KeyAction) -> str:
    return _(action.label).format(n=_preset_n(action))


def check(keys: dict[str, str]) -> dict[str, list[str]]:
    """Every problem of a whole keymap: {action id: problems}."""
    return {i: p for i in BY_ID if (p := problems(i, keys[i], keys))}


class KeymapError(ValueError):
    """The keymap has collisions; `problems` says which."""

    def __init__(self, problems: dict[str, list[str]]):
        self.problems = problems
        super().__init__("\n".join(f"{label(BY_ID[i])}: {' '.join(p)}" for i, p in problems.items()))


def save(storage, keys: dict[str, str]) -> None:
    """Save a whole keymap — refused (KeymapError) if anything collides."""
    full = effective(keys)
    found = check(full)
    if found:
        raise KeymapError(found)
    storage.set_setting(SETTING_KEY, json.dumps(overrides_of(full), sort_keys=True))


def export(keys: dict[str, str], folder: str | Path) -> Path:
    """Write every action's key to <folder>/ccc-keys.json."""
    path = Path(folder).expanduser() / EXPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"format": FORMAT, "version": 1, "keys": {i: keys[i] for i in BY_ID}}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_export(path: str | Path) -> dict[str, str]:
    """The keymap in an exported file (a folder: its ccc-keys.json).
    ValueError if it isn't one; ids ccc doesn't know are skipped."""
    path = Path(path).expanduser()
    if path.is_dir():
        path = path / EXPORT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(_("Can't read {path}: {error}").format(path=path, error=error.strerror or error)) from error
    except ValueError as error:
        raise ValueError(_("{path} isn't a ccc shortcuts file.").format(path=path)) from error
    keys = data.get("keys") if isinstance(data, dict) and data.get("format") == FORMAT else None
    if not isinstance(keys, dict):
        raise ValueError(_("{path} isn't a ccc shortcuts file.").format(path=path))
    found = {}
    for i, k in keys.items():
        if i in BY_ID and isinstance(k, str):
            found[i] = parse(k) or k
    return effective(found)


def binding(action_id: str, action: str, description: str = "", **kwargs):
    """The Textual Binding for an action, on its default key — App.set_keymap
    moves it to the user's key."""
    from textual.binding import Binding

    return Binding(BY_ID[action_id].default, action, description, id=action_id, **kwargs)
