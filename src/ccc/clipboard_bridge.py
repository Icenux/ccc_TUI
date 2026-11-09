"""Copies made in Claude Code → your device's clipboard.

Claude Code (with its own mouse selection) and tmux copy mode both put a
copy into a paste buffer of the session server (ccc-sessions). That server
must not hand anything to the clipboard itself (set-clipboard external,
see managed_sessions), and ccc's server doesn't accept OSC 52 from its
panes either (see tui.cli_pane) — so text merely printed in a session can
never reach the clipboard. This bridge listens to the session server as a
read-only tmux control client and loads each new paste buffer into ccc's
server with `load-buffer -w`, which tmux itself sends to the clipboard of
ccc's client (OSC 52 — over SSH, in Termux and Linux terminals).

Programs Claude runs in a session could make such a buffer too — but they
can run `tmux load-buffer -w` on ccc's server directly anyway; what this
keeps out is text that's only printed.

Runs in the bar process (tui.cli_bar), i.e. while a Claude Code pane is
open."""
from __future__ import annotations

import os
import re
import subprocess
import threading

from ccc import instance, managed_sessions

NOTIFICATION = "%paste-buffer-changed "
RETRY_SECONDS = 1.0
# Only copies: tmux names those itself (buffer0, buffer1, …) — copy mode and
# Claude Code's own mouse copies. Never named buffers: ccc pastes queued
# prompts and presets into Claude through "ccc-<id>" buffers, and those
# must not overwrite your clipboard.
COPY_BUFFER = re.compile(r"buffer\d+")


def changed_buffer(line: str) -> str | None:
    """The buffer name of a control-mode `%paste-buffer-changed` line, if
    it's a copy (see COPY_BUFFER)."""
    line = line.rstrip("\n")
    if line.startswith(NOTIFICATION):
        name = line[len(NOTIFICATION):].strip()
        return name if COPY_BUFFER.fullmatch(name) else None
    return None


def read_buffer(buffer_name: str) -> bytes | None:
    """The session server's paste buffer (None if it's empty or gone)."""
    saved = subprocess.run(
        ["tmux", "-L", managed_sessions.TMUX_SOCKET, "save-buffer", "-b", buffer_name, "-"],
        capture_output=True, timeout=5,
    )
    return saved.stdout if saved.returncode == 0 and saved.stdout else None


def forward(buffer_name: str) -> bool:
    """Load the session server's buffer into ccc's server and onto the
    clipboard of its client."""
    saved = subprocess.run(
        ["tmux", "-L", managed_sessions.TMUX_SOCKET, "save-buffer", "-b", buffer_name, "-"],
        capture_output=True, timeout=5,
    )
    if saved.returncode != 0 or not saved.stdout:
        return False
    loaded = subprocess.run(
        ["tmux", "-L", instance.TMUX_SOCKET, "load-buffer", "-w", "-"],
        input=saved.stdout, capture_output=True, timeout=5,
    )
    return loaded.returncode == 0


def control_command(queue_id: int) -> list[str]:
    # -r: read-only, the bridge never types into the session.
    return ["tmux", "-L", managed_sessions.TMUX_SOCKET, "-C", "attach-session", "-r",
            "-t", managed_sessions.exact(queue_id)]


def listen(queue_id: int, stop: threading.Event, on_copy=None) -> None:
    """Forward buffers until `stop` is set; reconnects if the session (or
    its server) goes away and comes back. `on_copy(text)`: ccc shows Claude
    Code itself (tui.claude_pane) and has no tmux server of its own to load
    the buffer into — the text goes to the callback instead."""
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    while not stop.is_set():
        try:
            client = subprocess.Popen(control_command(queue_id), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        except OSError:
            stop.wait(RETRY_SECONDS)
            continue
        try:
            for line in client.stdout:
                if stop.is_set():
                    break
                name = changed_buffer(line)
                if name is not None:
                    if on_copy is None:
                        forward(name)
                    elif (data := read_buffer(name)) is not None:
                        on_copy(data.decode("utf-8", "replace"))
        finally:
            client.kill()
            client.wait()
        stop.wait(RETRY_SECONDS)


def start(queue_id: int, on_copy=None) -> threading.Event:
    """Run the bridge in a daemon thread; set the returned event to stop."""
    stop = threading.Event()
    threading.Thread(target=listen, args=(queue_id, stop, on_copy), daemon=True, name="clipboard-bridge").start()
    return stop
