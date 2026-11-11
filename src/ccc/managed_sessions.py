"""Interactive Claude Code processes that ccc itself runs for
remote-control conversations: one tmux session per conversation on a
dedicated tmux server, so the conversation can also be driven from the
Claude mobile app / claude.ai while ccc types queued prompts in.

Headless `claude -p` can't do this — it silently ignores --remote-control
(verified), because remote control needs a long-lived process.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass

from ccc import mcp_servers, plugins, profiles
from ccc.live_delivery import DeliveryResult
from ccc.models import DeliveryMode, Queue

# Separate from the user's default tmux server and from the TUI's own one.
# Overridable (CCC_SESSIONS_SOCKET) so test runs never share it with the
# real conversations: server-wide options and key bindings live here.
TMUX_SOCKET = os.environ.get("CCC_SESSIONS_SOCKET", "ccc-sessions")

_REMOTE_URL_RE = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
_SESSION_SELECTING_FLAGS = ("--resume", "--from-pr")

# kill-session only sends SIGHUP; Claude shuts down gracefully but takes a
# few seconds. Wait this long before SIGTERM, then this long before SIGKILL.
STOP_GRACE_SECONDS = 10.0
STOP_TERM_SECONDS = 3.0


def _run(args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=10)


def _tmux(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    # -f /dev/null: the server must not load the user's tmux.conf — plugins
    # like tmux-continuum would restore the user's own sessions into it.
    return _run(["tmux", "-L", TMUX_SOCKET, "-f", "/dev/null", *args], input_text=input_text)


def target(queue_id: int) -> str:
    """The tmux session's name (for creating it)."""
    return f"q{queue_id}"


def exact(queue_id: int) -> str:
    """The session as a `-t` target. A plain name is a prefix match in tmux
    (`q1` would also hit `q12` while q1 is not running), so one conversation's
    keys could land in another; `=name:` is an exact match (#259)."""
    return f"={target(queue_id)}:"


def build_interactive_command(queue: Queue, has_transcript: bool, extra: list[str] | None = None) -> list[str]:
    agent = profiles.get(queue.profile)
    if agent is not None and agent.is_command:  # not Claude Code (#226): its flags do not apply
        from ccc import backends

        return backends.interactive_command(agent, queue.session_id, queue.project_path)
    first = list(queue.first_run_flags or [])
    if any(flag in first for flag in _SESSION_SELECTING_FLAGS):
        session = first  # fork / from-PR: they pick the session themselves
    elif has_transcript:
        session = ["--resume", queue.session_id]
    else:
        session = ["--session-id", queue.session_id]
    remote = ["--remote-control", queue.name.lstrip("- \t") or "conversation"] if queue.delivery_mode == DeliveryMode.REMOTE_CONTROL else []
    launch = plugins.resolve_flags(mcp_servers.resolve_flags(queue.launch_flags, queue.project_path, queue.profile), queue.profile) or []
    return ["claude", *session, *remote, *launch, *(extra or [])]


def is_running(queue_id: int) -> bool:
    return _tmux("has-session", "-t", exact(queue_id)).returncode == 0


# The server is shown inside ccc's own tmux window (tui.cli_pane) through a
# nested client, so it must be invisible: no prefix (every key goes to
# Claude; Ctrl+B is the outer tmux's), no status bar, mouse wheel scrolls
# Claude's history, and Ctrl+] (keymap's conversations.close_claude) leaves
# (detaches) — Claude keeps running.
# set-clipboard is pinned to "external" (only tmux itself may set the
# clipboard): "on" would let any text shown in the session (tool output,
# file contents) write the user's clipboard through an OSC 52 escape. Set
# explicitly, not left to the default, so a server started with another
# value is put back on every start.
DETACH_KEY = "C-]"
# The detach key bound now (server option), so a changed one replaces it.
DETACH_OPTION = "@ccc-detach-key"
TRANSPARENT_OPTIONS: tuple[tuple[str, ...], ...] = (
    ("set-option", "-g", "prefix", "None"),
    ("set-option", "-g", "prefix2", "None"),
    ("set-option", "-g", "status", "off"),
    ("set-option", "-g", "mouse", "on"),
    ("set-option", "-g", "escape-time", "0"),
    # Claude Code asks for it (focus tracking).
    ("set-option", "-g", "focus-events", "on"),
    ("set-option", "-g", "set-clipboard", "external"),
    ("set-option", "-g", "default-terminal", "tmux-256color"),
    ("set-option", "-ga", "terminal-features", ",*:RGB"),
    # tmux copy mode (mouse, Ctrl+Shift+C): copy into a paste buffer and
    # leave copy mode. clipboard_bridge takes new buffers — these and
    # Claude Code's own copies — to your device's clipboard.
    *[("bind-key", "-T", table, key, "send-keys", "-X", "copy-pipe-and-cancel")
      for table in ("copy-mode", "copy-mode-vi") for key in ("MouseDragEnd1Pane", "C-S-c")],
)


# Claude Code's fullscreen mode (alternate screen): the prompt and status
# line stay at the bottom of the pane also right after /clear (#274); inline
# it would draw them under the header, at the top.
FULLSCREEN_ENV = "CLAUDE_CODE_NO_FLICKER=1"


def start(queue: Queue, command: list[str]) -> None:
    agent = profiles.get(queue.profile)
    fullscreen = [] if agent is not None and agent.is_command else ["env", FULLSCREEN_ENV]
    _tmux(
        "new-session", "-d", "-s", target(queue.id), "-x", "200", "-y", "50",
        "-c", queue.project_path,
        shlex.join([*profiles.env_assignments(queue.profile), *fullscreen, *command]),
    )
    make_transparent()


def make_transparent(detach_key: str | None = None) -> None:
    """Server-wide options (idempotent; the server may have been started
    before ccc set them). The detach key: `detach_key`, else the one bound
    already, else Ctrl+]."""
    for args in TRANSPARENT_OPTIONS:
        _tmux(*args)
    set_detach_key(detach_key)


def set_detach_key(key: str | None = None) -> None:
    """Bind `key` (tmux name) to leave the session; the previous one is
    unbound. None keeps the current one (Ctrl+] when there's none)."""
    result = _tmux("show-options", "-gv", DETACH_OPTION)
    old = result.stdout.strip() if result.returncode == 0 else ""
    key = key or old or DETACH_KEY
    if old and old != key:
        _tmux("unbind-key", "-n", old)
    _tmux("bind-key", "-n", key, "detach-client")
    _tmux("set-option", "-g", DETACH_OPTION, key)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _wait_gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(min(0.2, seconds))
    return not _pid_alive(pid)


def stop(queue_id: int) -> None:
    """End the conversation's interactive process — and make sure it's
    really gone, not left running without its tmux pane."""
    pid = pane_pid(queue_id)
    _tmux("kill-session", "-t", exact(queue_id))
    if pid is None or _wait_gone(pid, STOP_GRACE_SECONDS):
        return
    _signal(pid, signal.SIGTERM)
    if not _wait_gone(pid, STOP_TERM_SECONDS):
        _signal(pid, signal.SIGKILL)


def capture(queue_id: int) -> str:
    result = _tmux("capture-pane", "-p", "-t", exact(queue_id))
    return result.stdout if result.returncode == 0 else ""


# Keys the phone's terminal may press (tmux key names): Claude Code's
# menus, history, interrupt and completion — nothing else.
KEYS = frozenset({"Enter", "Escape", "Tab", "BTab", "Up", "Down", "Left", "Right", "Home", "End", "PageUp",
                  "PageDown", "BSpace", "Space", "C-c", "C-d", "C-l", "C-o", "C-r", "C-t", "C-u"})
TYPE_MAX_CHARS = 200


@dataclass(frozen=True)
class Screen:
    text: str  # with its colours (ANSI escapes)
    width: int
    height: int
    cursor_x: int
    cursor_y: int


def screen(queue_id: int) -> Screen | None:
    """What the conversation's Claude Code shows now, colours included
    (None: it isn't running)."""
    shown = _tmux("capture-pane", "-p", "-e", "-t", exact(queue_id))
    size = _tmux("display-message", "-p", "-t", exact(queue_id),
                 "#{pane_width} #{pane_height} #{cursor_x} #{cursor_y}")
    if shown.returncode != 0 or size.returncode != 0:
        return None
    try:
        width, height, x, y = (int(n) for n in size.stdout.split())
    except ValueError:
        return None
    return Screen(shown.stdout, width, height, x, y)


def press(queue_id: int, keys: list[str]) -> DeliveryResult:
    """Press KEYS (tmux names) in the conversation's Claude Code."""
    unknown = [k for k in keys if k not in KEYS]
    if unknown or not keys:
        return DeliveryResult(delivered=False, error=f"not an allowed key: {', '.join(unknown) or '(none)'}")
    result = _tmux("send-keys", "-t", exact(queue_id), *keys)
    if result.returncode != 0:
        return DeliveryResult(delivered=False, error=result.stderr.strip() or "tmux send-keys failed")
    return DeliveryResult(delivered=True)


def type_text(queue_id: int, text: str) -> DeliveryResult:
    """Type a few characters as they are (an answer to a menu, a "y") —
    no control characters; longer text goes through paste_and_submit."""
    if not text or len(text) > TYPE_MAX_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return DeliveryResult(delivered=False, error="text to type must be short, without control characters")
    result = _tmux("send-keys", "-t", exact(queue_id), "-l", text)
    if result.returncode != 0:
        return DeliveryResult(delivered=False, error=result.stderr.strip() or "tmux send-keys failed")
    return DeliveryResult(delivered=True)


def pane_pid(queue_id: int) -> int | None:
    result = _tmux("display-message", "-p", "-t", exact(queue_id), "#{pane_pid}")
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def parse_remote_url(pane_text: str) -> str | None:
    match = _REMOTE_URL_RE.search(pane_text)
    return match.group(0) if match else None


# Claude Code's folder-trust dialog: (question, the "yes" choice) for each
# wording seen so far. Newer versions ask "Quick safety check: Is this a
# project you created or one you trust?" with "No, exit" pre-selected.
TRUST_DIALOGS: tuple[tuple[str, str], ...] = (
    ("Do you trust the files in this folder?", "Yes, proceed"),
    ("Is this a project you created or one you trust?", "Yes, I trust this folder"),
)
TRUST_SELECT_ATTEMPTS = 4
TRUST_SELECT_PAUSE_SECONDS = 0.3


def _trust_dialog(pane_text: str) -> tuple[str, str] | None:
    for question, yes in TRUST_DIALOGS:
        if question in pane_text and yes in pane_text:
            return question, yes
    return None


def needs_trust_confirmation(pane_text: str) -> bool:
    """True only while interactive Claude shows its folder-trust dialog
    (headless -p skips it; the user picked the folder in ccc, so the
    daemon accepts it). Deliberately narrow: the question AND its "yes"
    choice must be on screen, so Claude merely quoting the phrase, or any
    other prompt — tool permission questions in particular — never
    matches. Callers additionally limit this to just after startup."""
    return _trust_dialog(pane_text) is not None


def trust_choice_selected(pane_text: str) -> bool | None:
    """Whether the dialog's cursor (❯) is on its "yes" choice; None when
    there's no trust dialog. Only lines from the question down count —
    the scrollback above may hold old "❯ prompt" lines."""
    dialog = _trust_dialog(pane_text)
    if dialog is None:
        return None
    question, yes = dialog
    lines = pane_text.splitlines()
    start = max(i for i, line in enumerate(lines) if question in line)
    selected = next((line for line in lines[start:] if "❯" in line), "")
    return yes in selected


def confirm(queue_id: int, sleep=time.sleep) -> bool:
    """Accept the folder-trust dialog: move the cursor to the "yes" choice
    and press Enter only once the screen shows it selected — Enter on a
    pre-selected "No, exit" would quit Claude. True if confirmed."""
    for _ in range(TRUST_SELECT_ATTEMPTS):
        selected = trust_choice_selected(capture(queue_id))
        if selected is None:
            return False
        if selected:
            _tmux("send-keys", "-t", exact(queue_id), "Enter")
            return True
        _tmux("send-keys", "-t", exact(queue_id), "Down")
        sleep(TRUST_SELECT_PAUSE_SECONDS)
    return False


def paste_and_submit(queue_id: int, text: str, submit: bool = True) -> DeliveryResult:
    """Paste the prompt as ONE bracketed paste (plain send-keys would turn
    each newline into Enter and submit a multi-line prompt line by line),
    then press Enter to send it — or, with submit=False, leave it in
    Claude's input line."""
    buffer = f"ccc-{queue_id}"
    steps = [
        (("load-buffer", "-b", buffer, "-"), text),
        (("paste-buffer", "-p", "-d", "-b", buffer, "-t", exact(queue_id)), None),
    ]
    if submit:
        steps.append((("send-keys", "-t", exact(queue_id), "Enter"), None))
    for args, input_text in steps:
        result = _tmux(*args, input_text=input_text)
        if result.returncode != 0:
            return DeliveryResult(delivered=False, error=result.stderr.strip() or f"tmux {args[0]} failed")
    return DeliveryResult(delivered=True)
