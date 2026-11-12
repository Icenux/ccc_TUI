"""The Claude Code pane next to ccc: an interactive conversation's real
CLI, shown in a tmux pane split off ccc's own window (ccc itself runs in
tmux — the ccc-tui service). Nothing is emulated: the pane is a nested
tmux client attached to the conversation's session on the ccc-sessions
server (see attach_cli), so every Claude Code feature works.

Only one such pane exists; it's marked with the @ccc-cli pane option
holding the conversation (queue) id it shows. In the same column as the
Claude Code pane, under it, runs the bar (cli_bar: preset prompts and keys;
@ccc-bar). On
ccc's own tmux server the keys that switch between ccc and Claude and run
ccc's presets are bound while the pane is open — see key_bindings()."""
from __future__ import annotations

import os
import shlex
import sqlite3
import subprocess
import sys

from ccc import instance, keymap, tmux
from ccc.storage import Storage
from ccc.tui import theme
from ccc.i18n import _

PANE_OPTION = "@ccc-cli"
BAR_OPTION = "@ccc-bar"
# ccc's key list as a 1-row pane under everything (footer_strip).
STRIP_OPTION = "@ccc-strip"
TITLE_OPTION = "@ccc-title"
# Window option: the conversations that need you (ccc.attention), shown in
# the Claude Code pane's title bar.
WAITING_OPTION = "@ccc-waiting"
# Rows of the bar under Claude Code: the preset prompts.
BAR_ROWS = 1
# Keys bound on ccc's server while the pane is open (root table: they
# work in every pane, before the pane's program sees them) — keymap's
# `tmux` actions, on the user's keys. keymap.check() keeps them off
# Claude Code's keys (by default Alt+0, Alt+1 … Alt+9, Ctrl+P, Alt+S).
# The keys bound last are kept in BOUND_OPTION (server), so they're the
# ones unbound — also after the shortcuts changed.
BOUND_OPTION = "@ccc-bound-keys"
QUIT_OPTION = "@ccc-quit-key"
# On ccc's own server: a title bar on each pane, and the pane you type in
# marked — otherwise nothing shows whether keys go to ccc or to Claude.
# Only while Claude Code is open (#{P:} looks through the window's panes
# for @ccc-cli): with ccc alone the keys can't go anywhere else.
CLI_OPEN_FORMAT = f"#{{P:#{{?#{{{PANE_OPTION}}},1,}}}}"
# Every line of the frame (the borders of all panes) is the theme's grey
# line, in ccc and in Claude Code alike; "typing here" (in the accent
# green) marks where the keys go.
BORDER_COLOR = theme.TMUX_FRAME
BORDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("pane-border-status", "top"),
    ("pane-border-format",
     f"#{{?#{{&&:#{{pane_active}},{CLI_OPEN_FORMAT}}},"
     f"#[fg=black#,bg={theme.TMUX_ACCENT}#,bold]"
     f" {_("⌨ typing here")} #[default],}}"
     f"#{{?#{{{TITLE_OPTION}}}, #{{{TITLE_OPTION}}} ,}}"
     f"#{{?#{{&&:#{{{PANE_OPTION}}},#{{{WAITING_OPTION}}}}},#[fg=black#,bg=yellow#,bold] #{{{WAITING_OPTION}}} #[default] ,}}"),
    ("pane-active-border-style", f"fg={BORDER_COLOR},bold"),
    ("pane-border-style", f"fg={BORDER_COLOR}"),
)
# ccc keeps exactly its sidebar (layout.SHARED_CSS #sidebar width) while
# the pane is open, so the menu doesn't move or change size.
SIDEBAR_COLUMNS = 32
# Narrower windows (phone): the pane takes the whole window when focused.
NARROW_COLUMNS = 100
# ccc's own tmux server (bin/ccc-tui-supervise.sh) — only there do we
# change session options such as the mouse.
CCC_SOCKET = instance.TMUX_SOCKET


def embedded() -> bool:
    """Claude Code is shown by ccc itself (tui.claude_pane), in a frame ccc
    draws, instead of a tmux pane next to ccc. Opt-in: CCC_EMBEDDED_TERMINAL=1."""
    return os.environ.get("CCC_EMBEDDED_TERMINAL", "").lower() in ("1", "true", "yes", "on")


def available() -> bool:
    """True when ccc runs inside tmux, so it can split its window (and
    isn't showing Claude Code itself, see embedded())."""
    return not embedded() and bool(os.environ.get("TMUX") and os.environ.get("TMUX_PANE"))


def hide_chrome() -> None:
    """Embedded, on ccc's own tmux server: tmux is only the window ccc lives
    in (so it survives and can be attached from anywhere) — no status line,
    no pane titles; every line on screen is ccc's."""
    if not (embedded() and os.environ.get("TMUX") and _on_ccc_server()):
        return
    _tmux("set-option", "-g", "status", "off")
    _tmux("set-option", "-w", "pane-border-status", "off")
    _tmux("set-option", "-w", "pane-border-lines", "single")
    _tmux("set-option", "-t", os.environ.get("TMUX_PANE", ""), "mouse", "on")
    for command in CLIPBOARD_COMMANDS:
        _tmux(*command)
    ensure_terminal_feature()


def _own_pane() -> str:
    return os.environ["TMUX_PANE"]


def _tmux(*args: str) -> subprocess.CompletedProcess:
    # $TMUX points it at the server ccc runs in; ccc's tmux binary.
    return subprocess.run(tmux.own(*args), capture_output=True, text=True, timeout=5)


def attach_command_list(queue_id: int, keys: dict[str, str] | None = None) -> list[str]:
    detach = keymap.tmux_key((keys or keymap.defaults())["conversations.close_claude"])
    return [sys.executable, "-m", "ccc.attach_cli", str(queue_id), detach]


def attach_command(queue_id: int, keys: dict[str, str] | None = None) -> str:
    return shlex.join(attach_command_list(queue_id, keys))


def _keys(db_path: str | None) -> dict[str, str]:
    """The user's shortcuts (the defaults without a readable database)."""
    if db_path is None:
        return keymap.defaults()
    try:
        return keymap.current(Storage(db_path))
    except sqlite3.Error:
        return keymap.defaults()


def bar_command(db_path: str) -> str:
    return shlex.join([sys.executable, "-m", "ccc.tui.cli_bar", str(db_path)])


def key_bindings(main: str, cli: str, queue_id: int, db_path: str,
                 keys: dict[str, str] | None = None) -> list[tuple[str, ...]]:
    """tmux commands binding ccc's keys for the open Claude Code pane
    (defaults in brackets; `keys` = the user's keymap):
    - switch (Alt+0) between ccc (`main`) and Claude (`cli`; on narrow
      windows Claude then takes the whole window),
    - presets (Alt+1 … Alt+9) type a preset into Claude (`ccc preset`);
      in ccc's own pane they go to ccc as before,
    - Settings (Ctrl+P) opens ccc's Settings from anywhere,
    - screenshot (Alt+S) takes ccc's from anywhere (the keyboard stays),
    - next waiting conversation (Alt+N) opens it here (ccc.attention)."""
    keys = keys or keymap.defaults()

    def key(action_id: str) -> str:
        return keymap.tmux_key(keys[action_id])

    in_ccc = f"#{{==:#{{pane_id}},{main}}}"
    # e|<| compares numbers; plain #{<:} compares strings ("80" > "100").
    to_claude = f"select-pane -t {cli} ; if-shell -F '#{{e|<|:#{{window_width}},{NARROW_COLUMNS}}}' 'resize-pane -Z -t {cli}'"
    switch = key("conversations.focus_claude")
    bindings = [("bind-key", "-n", switch, "if-shell", "-F", in_ccc, to_claude, f"select-pane -t {main}")]
    for number in range(1, 10):
        preset_key = key(f"conversations.preset{number}")
        preset = shlex.join([sys.executable, "-m", "ccc.cli", "preset", str(number),
                             "--queue", str(queue_id), "--db", str(db_path)])
        # Errors are shown on the client that pressed the key, on this server.
        bindings.append((
            "bind-key", "-n", preset_key, "if-shell", "-F", in_ccc, f"send-keys {preset_key}",
            f"run-shell -b \"{preset} --client '#{{client_tty}}' --socket '#{{socket_path}}'\"",
        ))
    settings = key("app.settings")
    bindings.append(("bind-key", "-n", settings, "if-shell", "-F", in_ccc, f"send-keys {settings}",
                     f"select-pane -t {main} ; send-keys -t {main} {settings}"))
    screenshot = key("app.screenshot")
    bindings.append(("bind-key", "-n", screenshot, "if-shell", "-F", in_ccc, f"send-keys {screenshot}",
                     f"send-keys -t {main} {screenshot}"))
    # ccc opens the conversation in this pane and puts the keyboard there.
    jump = key("app.jump_waiting")
    bindings.append(("bind-key", "-n", jump, "if-shell", "-F", in_ccc, f"send-keys {jump}",
                     f"send-keys -t {main} {jump}"))
    return bindings


def bind_keys(main: str, cli: str, queue_id: int, db_path: str, keys: dict[str, str] | None = None) -> None:
    """Bind key_bindings() (after unbinding what was bound before)."""
    unbind_keys()
    bindings = key_bindings(main, cli, queue_id, db_path, keys)
    for binding in bindings:
        _tmux(*binding)
    _tmux("set-option", "-s", BOUND_OPTION, " ".join(b[2] for b in bindings))


def fit_command(main: str, bar: str | None, strip: str | None = None, sidebar: bool = True) -> str:
    """tmux command putting the layout back: ccc at its sidebar width (with
    Claude Code open), the bar and the key strip at their rows. tmux
    rescales panes proportionally when the window changes size (e.g.
    attaching from a phone, then a desktop), so this runs from the
    window-resized hook too."""
    commands = [f"resize-pane -t {main} -x {SIDEBAR_COLUMNS}"] if sidebar else []
    if bar is not None:
        commands.append(f"resize-pane -t {bar} -y {BAR_ROWS}")
    if strip is not None:
        commands.append(f"resize-pane -t {strip} -y 1")
    return " ; ".join(commands)


def strip_command(main: str) -> str:
    return shlex.join([sys.executable, "-m", "ccc.tui.footer_strip", main])


def _find_flag(option: str) -> str | None:
    result = _tmux("list-panes", "-t", _own_pane(), "-F", f"#{{pane_id}} #{{{option}}}")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        pane, _sep, flag = line.partition(" ")
        if flag.strip() == "1":
            return pane
    return None


def find_strip() -> str | None:
    return _find_flag(STRIP_OPTION)


def ensure_strip() -> str | None:
    """The key strip under the window (created once; it survives TUI
    restarts in the same window)."""
    strip = find_strip()
    if strip is None:
        result = _tmux("split-window", "-v", "-f", "-d", "-l", "1", "-t", _own_pane(),
                       "-P", "-F", "#{pane_id}", strip_command(_own_pane()))
        if result.returncode != 0:
            return None
        strip = result.stdout.strip()
        _tmux("set-option", "-p", "-t", strip, STRIP_OPTION, "1")
    # No title text on the strip (one set by an older ccc goes too).
    _tmux("set-option", "-pu", "-t", strip, TITLE_OPTION)
    _tmux("resize-pane", "-t", strip, "-y", "1")
    if find() is None:
        _tmux("set-hook", "-w", "-t", _own_pane(), "window-resized",
              fit_command(_own_pane(), None, strip, sidebar=False))
    return strip


def unbind_keys() -> None:
    """Unbind the keys bind_keys() bound (the defaults when it's unknown —
    bound by a ccc from before shortcuts could change)."""
    result = _tmux("show-options", "-sv", BOUND_OPTION)
    bound = result.stdout.split() if result.returncode == 0 and result.stdout.strip() else \
        [b[2] for b in key_bindings("%0", "%0", 0, "")]
    for key in bound:
        _tmux("unbind-key", "-n", key)
    _tmux("set-option", "-su", BOUND_OPTION)


def bind_quit_key(keys: dict[str, str] | None = None) -> None:
    """The close-ccc key detaches the terminal that pressed it (see
    CLIPBOARD_COMMANDS); the previous one is unbound."""
    key = keymap.tmux_key((keys or keymap.defaults())["app.quit"])
    result = _tmux("show-options", "-sv", QUIT_OPTION)
    old = result.stdout.strip() if result.returncode == 0 else ""
    if old and old != key:
        _tmux("unbind-key", "-n", old)
    _tmux("bind-key", "-n", key, "detach-client")
    _tmux("set-option", "-s", QUIT_OPTION, key)


def apply_keys(keys: dict[str, str], db_path: str) -> None:
    """The shortcuts changed: move ccc's tmux keys (here and, for closing
    Claude Code, on the sessions' server) to the new ones."""
    from ccc import managed_sessions

    if not (available() and _on_ccc_server()):
        return  # no Claude Code pane here; attach_cli gets the key when one opens
    managed_sessions.set_detach_key(keymap.tmux_key(keys["conversations.close_claude"]))
    bind_quit_key(keys)
    current = find()
    if current is not None:
        bind_keys(_own_pane(), current[0], current[1], db_path, keys)


def find_bar() -> str | None:
    return _find_flag(BAR_OPTION)


def find() -> tuple[str, int] | None:
    """(pane id, queue id) of the open Claude Code pane, if any."""
    result = _tmux("list-panes", "-t", _own_pane(), "-F", f"#{{pane_id}} #{{{PANE_OPTION}}}")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        pane, _sep, queue = line.partition(" ")
        if queue.strip().isdigit():
            return pane, int(queue)
    return None


def _window_width() -> int:
    result = _tmux("display-message", "-p", "-t", _own_pane(), "#{window_width}")
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def on_ccc_server() -> bool:
    return _on_ccc_server()


def _on_ccc_server() -> bool:
    return os.path.basename(os.environ.get("TMUX", "").split(",")[0]) == CCC_SOCKET


# Copy & paste on ccc's server, the same in ccc and in Claude Code:
# - selecting with the mouse copies to your device's clipboard (OSC 52,
#   over SSH too — Termux and Linux terminals support it) when you let
#   go; the pane leaves copy mode, so typing and pasting go to it again;
# - Ctrl+Shift+C copies the selection (tmux's in copy mode, the embedded
#   frame's own otherwise); without one it does nothing (it must never
#   reach Claude as Ctrl+C = interrupt);
# - Ctrl+Shift+V pastes the last copy (where the terminal doesn't paste
#   by itself — Linux terminals do, from the clipboard).
# set-clipboard stays "external" (tmux's default): only tmux's own copies
# reach the clipboard, never an escape sequence printed into a pane (a
# traceback quoting a conversation name, say). Copies made in Claude Code
# come in as a command instead (clipboard_bridge: load-buffer -w).
# Ctrl+Shift+C in ccc's own pane (not in copy mode): tmux would swallow it, so
# it's handed on as the unambiguous CSI-u sequence Textual reads as
# "ctrl+shift+c" (the embedded Claude Code frame copies its selection).
# In any other pane it does nothing — never Ctrl+C (interrupt) to Claude.
COPY_KEY_TO_CCC = 'if-shell -F "#{==:#{@ccc-title},ccc}" "send-keys -l \'\\033[99;6u\'"'
CLIPBOARD_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("set-option", "-s", "set-clipboard", "external"),
    ("set-option", "-s", "extended-keys", "on"),
    *[("bind-key", "-T", table, "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel")
      for table in ("copy-mode", "copy-mode-vi")],
    *[("bind-key", "-T", table, "C-S-c", "send-keys", "-X", "copy-pipe-and-cancel")
      for table in ("copy-mode", "copy-mode-vi")],
    ("bind-key", "-n", "C-S-c", "if-shell", "-F", "#{pane_in_mode}", "send-keys -X copy-pipe-and-cancel",
     COPY_KEY_TO_CCC),
    ("bind-key", "-n", "C-S-v", "paste-buffer", "-p"),
    # Ctrl+Q (keymap's app.quit) closes ccc for the terminal that pressed
    # it: detach that client (ccc keeps running; `ccc` opens it again).
    # Quitting the TUI would only restart it. Bound by bind_quit_key().
)


# Added to tmux's terminal-features once (see ensure_terminal_feature).
TERMINAL_FEATURE = "*:clipboard:extkeys"


def ensure_terminal_feature(feature: str = TERMINAL_FEATURE) -> None:
    """`feature` in the server's terminal-features exactly once. Appending
    it on every ccc start (set-option -as) grew the list by one copy per
    restart; copies already there are removed."""
    result = _tmux("show-options", "-s", "terminal-features")
    found = []
    for line in result.stdout.splitlines():
        name, _sep, value = line.partition(" ")
        if value.strip().strip('"') == feature and name.endswith("]"):
            found.append(int(name[name.index("[") + 1:-1]))
    for index in sorted(found)[1:][::-1]:  # from the end: indexes stay valid
        _tmux("set-option", "-su", f"terminal-features[{index}]")
    if not found:
        _tmux("set-option", "-as", "terminal-features", f",{feature}")


def style_window(keys: dict[str, str] | None = None) -> None:
    """On ccc's own server: pane title bars (and the mouse) from the start,
    not only once Claude Code opens — a title row appearing and staying
    would shift all of ccc down by a row when switching views."""
    if not (available() and _on_ccc_server()):
        return
    # Click a pane to switch to it; the wheel scrolls Claude.
    _tmux("set-option", "-t", _own_pane(), "mouse", "on")
    for name, value in BORDER_OPTIONS:
        _tmux("set-option", "-w", "-t", _own_pane(), name, value)
    # No title over ccc (#250: no bar at the top) — its menu starts right
    # under the frame line; one set by an older ccc goes too.
    _tmux("set-option", "-pu", "-t", _own_pane(), TITLE_OPTION)
    for command in CLIPBOARD_COMMANDS:
        _tmux(*command)
    bind_quit_key(keys)
    ensure_terminal_feature()
    # No zoom (Ctrl+B z): ccc's layout stays as it is.
    _tmux("unbind-key", "-T", "prefix", "z")
    ensure_strip()
    if find() is None and find_bar() is not None:
        # Left by a Claude Code pane that ended without ccc closing it
        # (e.g. ccc restarted meanwhile): it would take Claude's column.
        close()


def open(queue_id: int, title: str = "", db_path: str | None = None) -> str | None:
    """Show the conversation in the pane (create it, or switch what it
    shows) and, with db_path, the bar under it. Returns the pane id, None
    if tmux refused."""
    keys = _keys(db_path)
    command = attach_command(queue_id, keys)
    current = find()
    if current is not None:
        pane, shown = current
        if shown == queue_id:
            return pane
        if _tmux("respawn-pane", "-k", "-t", pane, command).returncode != 0:
            return None
    else:
        width = max(20, _window_width() - SIDEBAR_COLUMNS - 1)
        result = _tmux("split-window", "-h", "-d", "-l", str(width), "-t", _own_pane(),
                       "-P", "-F", "#{pane_id}", command)
        if result.returncode != 0:
            return None
        pane = result.stdout.strip()
        style_window(keys)
    _tmux("set-option", "-p", "-t", pane, PANE_OPTION, str(queue_id))
    _tmux("set-option", "-p", "-t", pane, TITLE_OPTION, f"Claude Code · {title}" if title else "Claude Code")
    if db_path is not None and find_bar() is None:
        # Under Claude Code, in its column — ccc's column stays as is.
        result = _tmux("split-window", "-v", "-d", "-l", str(BAR_ROWS), "-t", pane,
                       "-P", "-F", "#{pane_id}", bar_command(db_path))
        if result.returncode == 0:
            bar = result.stdout.strip()
            _tmux("set-option", "-p", "-t", bar, BAR_OPTION, "1")
    bar = find_bar()
    if bar is not None:
        _tmux("set-option", "-pu", "-t", bar, TITLE_OPTION)  # no title text on the bar either
    if _on_ccc_server() and db_path is not None:
        bind_keys(_own_pane(), pane, queue_id, db_path, keys)
    bar = find_bar()
    _tmux("set-hook", "-w", "-t", _own_pane(), "window-resized", fit_command(_own_pane(), bar, find_strip()))
    # And now: split-window -l counts the border row too, the bar got 1 row.
    _tmux("resize-pane", "-t", _own_pane(), "-x", str(SIDEBAR_COLUMNS))
    if bar is not None:
        _tmux("resize-pane", "-t", bar, "-y", str(BAR_ROWS))
    return pane


def waiting_room() -> int | None:
    """Columns left for the waiting conversations in the Claude Code pane's
    title bar; None when there's no pane."""
    current = find()
    if current is None:
        return None
    result = _tmux("display-message", "-p", "-t", current[0], f"#{{pane_width}} #{{{TITLE_OPTION}}}")
    width, _sep, title = result.stdout.rstrip("\n").partition(" ")
    try:
        # The "typing here" marker, the title and the spaces around.
        return max(0, int(width) - len(title) - len(_("⌨ typing here")) - 8)
    except ValueError:
        return None


def publish_waiting(text: str) -> None:
    """Show `text` in the Claude Code pane's title bar ("" hides it). Names
    are the user's: '#' is doubled so tmux never reads them as a style."""
    _tmux("set-option", "-w", "-t", _own_pane(), WAITING_OPTION, text.replace("#", "##"))


def focus() -> None:
    """Put the keyboard into the Claude Code pane (whole window when
    narrow)."""
    current = find()
    if current is None:
        return
    pane = current[0]
    _tmux("select-pane", "-t", pane)
    if 0 < _window_width() < NARROW_COLUMNS:
        _tmux("resize-pane", "-Z", "-t", pane)


def _zoomed() -> bool:
    result = _tmux("display-message", "-p", "-t", _own_pane(), "#{window_zoomed_flag}")
    return result.stdout.strip() == "1"


def zoom_ccc() -> bool:
    """Give ccc the whole window (the other panes are hidden, not closed),
    for a dialog that needs more than the sidebar's columns. True when it
    was zoomed now — pass that to unzoom_ccc()."""
    if not (available() and find() is not None) or _zoomed():
        return False
    return _tmux("resize-pane", "-Z", "-t", _own_pane()).returncode == 0


def unzoom_ccc(zoomed: bool) -> None:
    """Undo zoom_ccc() (only when it zoomed)."""
    if zoomed and _zoomed():
        _tmux("resize-pane", "-Z", "-t", _own_pane())


def close() -> None:
    """Close the pane and its bar (Claude keeps running in its session);
    the keys go back to ccc."""
    current = find()
    if current is not None:
        _tmux("kill-pane", "-t", current[0])
    bar = find_bar()
    if bar is not None:
        _tmux("kill-pane", "-t", bar)
    if current is not None or bar is not None:
        strip = find_strip()
        if strip is not None:
            _tmux("set-hook", "-w", "-t", _own_pane(), "window-resized",
                  fit_command(_own_pane(), None, strip, sidebar=False))
        else:
            _tmux("set-hook", "-uw", "-t", _own_pane(), "window-resized")
        if _on_ccc_server():
            unbind_keys()
