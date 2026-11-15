"""Daemon liveness: the daemon stamps a setting every few seconds, the TUI
warns when the stamp gets stale (otherwise a dead daemon just looks like
an idle queue)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ccc.storage import Storage

HEARTBEAT_KEY = "daemon_heartbeat"
VERSION_KEY = "daemon_version"  # the version of the code the daemon runs (ccc.updates.restart_needed)
HEARTBEAT_SECONDS = 10.0
# Longer than one SQLite lock wait (30 s): a single slow commit on a busy disk
# must not look like a dead daemon.
STALE_AFTER = timedelta(seconds=90)


def beat(storage: Storage, now: datetime | None = None) -> None:
    from ccc import __version__

    storage.set_setting(HEARTBEAT_KEY, (now or datetime.now()).isoformat())
    storage.set_setting(VERSION_KEY, __version__)


def daemon_alive(storage: Storage, now: datetime | None = None) -> bool:
    raw = storage.get_setting(HEARTBEAT_KEY)
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (now or datetime.now()) - last <= STALE_AFTER
