from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from ccc import profiles


@dataclass
class DiscoveredSession:
    session_id: str
    name: str
    cwd: str
    kind: str  # "interactive" | "background"
    status: str | None
    pid: int | None = None
    state: str | None = None  # e.g. "blocked" when hard-blocked on a usage limit


def discover_sessions() -> list[DiscoveredSession]:
    """Live sessions of the default account and of every named profile
    (each account keeps its own, so each is asked), without duplicates."""
    found = _discover("")
    seen = {s.session_id for s in found}
    for profile in profiles.list_profiles():
        for session in _discover(profile.name):
            if session.session_id not in seen:
                seen.add(session.session_id)
                found.append(session)
    return found


def _discover(profile: str) -> list[DiscoveredSession]:
    """Live Claude Code sessions of one account (interactive + background),
    via `claude agents --json`. Returns [] on any failure (claude not on
    PATH, no active sessions, unexpected output) — never raises, since
    this is a nice-to-have discovery aid, not load-bearing for the daemon."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            env=profiles.env_for(profile),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []

    if not isinstance(data, list):
        return []

    sessions = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("sessionId")
        cwd = entry.get("cwd")
        if not session_id or not cwd:
            continue
        sessions.append(
            DiscoveredSession(
                session_id=session_id,
                name=entry.get("name") or session_id[:8],
                cwd=cwd,
                kind=entry.get("kind", "interactive"),
                status=entry.get("status"),
                pid=entry.get("pid"),
                state=entry.get("state"),
            )
        )
    return sessions


def find_tmux_pane_for_pid(pid: int) -> str | None:
    """Find the tmux pane (as a "session:window.pane" target string usable
    with `tmux send-keys -t <target>`) that owns the given process pid.

    A tmux pane's #{pane_pid} is the pane's own foreground process — which
    is the target pid itself when tmux launched `claude` directly as the
    pane's command, or an ANCESTOR of it (typically the shell) when a
    human ran `claude` by hand inside an already-running shell pane. Both
    shapes exist for real on this machine (confirmed live: a pane created
    as `tmux new-session ... claude` has pane_pid == claude's own pid; a
    pane created as `tmux new-session ... bash` with claude then typed in
    by hand has pane_pid == claude's parent). So this checks the pid
    itself first, then walks up its ancestry (bounded, to avoid any
    pathological process tree) until a pane_pid matches or the chain ends.
    Returns None if `tmux`/`ps` are unavailable or no pane matches."""
    try:
        panes_result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index} #{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if panes_result.returncode != 0:
            return None
        pane_by_pid: dict[str, str] = {}
        for line in panes_result.stdout.splitlines():
            parts = line.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            target, pane_pid = parts
            pane_by_pid[pane_pid] = target

        current = str(pid)
        seen: set[str] = set()
        for _ in range(10):
            if current in pane_by_pid:
                return pane_by_pid[current]
            if current in seen:
                return None
            seen.add(current)
            ppid_result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", current],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if ppid_result.returncode != 0:
                return None
            next_pid = ppid_result.stdout.strip()
            if not next_pid or next_pid in ("0", "1"):
                return None
            current = next_pid
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return None


def session_ready_for_input(session: DiscoveredSession) -> bool:
    """True when a discovered session looks ready to receive a fresh
    prompt: not actively generating a response, and not hard-blocked on a
    usage limit. Best-effort — Claude Code's own reported status/state,
    not a guarantee (the human could be mid-typing something else at that
    exact moment; see live_delivery module docstring)."""
    if session.status != "idle":
        return False
    if session.state == "blocked":
        return False
    return True
