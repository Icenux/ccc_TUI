"""Remembered choices for the New conversation dialog: the last one used
(prefilled next time) and named presets. Stored as JSON in settings."""
from __future__ import annotations

import json
from dataclasses import dataclass

from ccc.launch_options import DEFAULT_SELECTION, FORK, GROUPS, SESSION_NEW, group_of, is_agent, is_mcp_server, is_plugin

LAST_KEY = "last_launch_selection"
PRESETS_KEY = "launch_presets"

_KNOWN_KEYS = {option.key for group in GROUPS for option in group.options}


@dataclass(frozen=True)
class LaunchChoice:
    selection: frozenset[str]
    auto_continue: bool = True
    remote_control: bool = True


def _normalise(keys) -> frozenset[str]:
    """Keep known keys, start a new session (which session to resume is a
    per-launch decision, never remembered), fill required groups."""
    sel = {k for k in keys if (k in _KNOWN_KEYS or is_mcp_server(k) or is_plugin(k) or is_agent(k)) and group_of(k).key not in ("session", "session_extra")}
    sel.add(SESSION_NEW)
    sel.discard(FORK)
    for group in GROUPS:
        if group.required and not any(o.key in sel for o in group.options):
            sel |= {o.key for o in group.options if o.key in DEFAULT_SELECTION}
    return frozenset(sel)


def _to_dict(choice: LaunchChoice) -> dict:
    return {
        "selection": sorted(choice.selection),
        "auto_continue": choice.auto_continue,
        "remote_control": choice.remote_control,
    }


def _from_dict(data) -> LaunchChoice | None:
    if not isinstance(data, dict) or not isinstance(data.get("selection"), list):
        return None
    return LaunchChoice(
        _normalise(data["selection"]),
        bool(data.get("auto_continue", True)),
        bool(data.get("remote_control", True)),
    )


def _load_json(storage, key):
    raw = storage.get_setting(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def load_last(storage) -> LaunchChoice | None:
    return _from_dict(_load_json(storage, LAST_KEY))


def save_last(storage, choice: LaunchChoice) -> None:
    storage.set_setting(LAST_KEY, json.dumps(_to_dict(choice)))


def list_presets(storage) -> dict[str, LaunchChoice]:
    data = _load_json(storage, PRESETS_KEY)
    if not isinstance(data, dict):
        return {}
    presets = {name: _from_dict(value) for name, value in data.items()}
    return {name: choice for name, choice in presets.items() if choice is not None}


def save_preset(storage, name: str, choice: LaunchChoice) -> None:
    presets = {n: _to_dict(c) for n, c in list_presets(storage).items()}
    presets[name] = _to_dict(choice)
    storage.set_setting(PRESETS_KEY, json.dumps(presets, ensure_ascii=False))


def delete_preset(storage, name: str) -> None:
    presets = {n: _to_dict(c) for n, c in list_presets(storage).items() if n != name}
    storage.set_setting(PRESETS_KEY, json.dumps(presets, ensure_ascii=False))
