"""ccc's own tmux, kept apart from the user's (#280).

ccc runs two tmux servers of its own: "ccc" (the always-on TUI, see
instance) and "ccc-sessions" (the conversations' interactive `claude`, see
managed_sessions). Nothing about them is shared with a tmux the user runs:

- the binary: CCC_TMUX_BIN (default `tmux` on PATH), so ccc can stay on a
  tmux of its own while the system one is upgraded or replaced;
- the sockets: in ccc's own directory (CCC_TMUX_DIR, default
  /tmp/ccc-tmux-<uid>), not in tmux's /tmp/tmux-<uid> next to the user's
  servers, and independent of TMUX_TMPDIR;
- the configuration: `-f /dev/null`, so the user's tmux.conf (and its
  plugins, e.g. tmux-continuum restoring sessions) is never loaded.

Servers ccc started before this (`tmux -L <name>`, in /tmp/tmux-<uid>) keep
being used while they're alive, so an upgrade doesn't cut off running
conversations; the next start of such a server is in ccc's directory.

The user's own tmux (live delivery into `claude` the user runs in it, see
session_discovery / live_delivery) is not ccc's: that is always plain `tmux`."""
from __future__ import annotations

import os
import socket
import stat


def binary() -> str:
    """The tmux ccc runs its servers with."""
    return os.environ.get("CCC_TMUX_BIN") or "tmux"


def socket_dir() -> str:
    return os.environ.get("CCC_TMUX_DIR") or f"/tmp/ccc-tmux-{os.getuid()}"


def _private_dir(path: str) -> bool:
    """Create `path` (0700) if needed; True when it is a directory of ours
    that nobody else can use (like tmux's own check of /tmp/tmux-<uid>)."""
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return False
        if info.st_mode & 0o077:
            os.chmod(path, 0o700)
        return True
    except OSError:
        return False


def socket_path(name: str) -> str:
    return os.path.join(socket_dir(), name)


def legacy_socket_path(name: str) -> str:
    """Where `tmux -L name` puts the socket (before #280)."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}", name)


def _alive(path: str) -> bool:
    """A tmux server is listening on the socket."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(1)
        client.connect(path)
        return True
    except OSError:
        return False
    finally:
        client.close()


def server(name: str) -> list[str]:
    """The arguments selecting ccc's server `name`."""
    path = socket_path(name)
    if not _alive(path) and _alive(legacy_socket_path(name)):
        return ["-L", name]  # started before #280 and still running
    if not _private_dir(socket_dir()):
        return ["-L", name]  # the directory isn't usable: tmux's own place
    return ["-S", path]


def command(name: str, *args: str) -> list[str]:
    """A tmux command on ccc's server `name` (never the user's tmux.conf)."""
    return [binary(), *server(name), "-f", "/dev/null", *args]


def own(*args: str) -> list[str]:
    """A tmux command on the server the calling pane runs in ($TMUX) — for
    code running inside ccc's own server, with ccc's tmux binary."""
    return [binary(), *args]
