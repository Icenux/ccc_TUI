"""Start the daemon when `ccc` / `ccc tui` finds it not running (a fresh install enables nothing:
the package's postinst only prints a hint), so the queue works right after `apt install ccc`."""
from __future__ import annotations

import getpass
import subprocess

from ccc.heartbeat import daemon_alive
from ccc.storage import Storage

UNIT = "ccc.service"


def _run(*command: str) -> bool:
    try:
        return subprocess.run(list(command), capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_daemon_running(storage: Storage) -> bool | None:
    """None: nothing to do (the heartbeat is fresh). Otherwise whether `systemctl --user enable --now
    ccc.service` went through (False: no systemd user manager or no such unit — silently ignored).
    Enables lingering too, so the daemon survives logout (best effort; may need no root)."""
    if daemon_alive(storage):
        return None
    started = _run("systemctl", "--user", "enable", "--now", UNIT)
    if started:
        _run("loginctl", "enable-linger", getpass.getuser())
    return started
