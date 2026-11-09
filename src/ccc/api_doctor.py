"""`ccc api doctor` — a security check-up of the API and what it sits on (#160).
Everything that would make a stolen token or a visitor on the network worse
is looked at once, and each finding says what to do:

* the API listens on this computer only (the tailnet reaches it through
  `tailscale serve`), and is not on the public internet (`tailscale funnel`);
* the database (tokens' fingerprints, Telegram token, history) is private;
* devices: how many may do everything, which have not been seen for long;
* the limits (api_scope) and whether alarms can reach you (api_audit)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ccc import api, api_scope
from ccc.i18n import _
from ccc.notify import Notifier

OK, WARN, FAIL = "ok", "warn", "fail"
STALE_DAYS = 90
LOOPBACK = ("127.0.0.1", "[::1]", "::1")


@dataclass(frozen=True)
class Check:
    level: str
    text: str


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def listeners(ss_output: str, port: int = api.DEFAULT_PORT) -> list[str]:
    """The addresses something listens on at `port`, from `ss -ltnH`."""
    found = []
    for line in ss_output.splitlines():
        columns = line.split()
        if len(columns) >= 4 and columns[3].rsplit(":", 1)[-1] == str(port):
            found.append(columns[3].rsplit(":", 1)[0])
    return found


def _network(ss_output: str, tailscale_json: str) -> list[Check]:
    checks = []
    open_to = [a for a in listeners(ss_output) if a not in LOOPBACK]
    if open_to:
        checks.append(Check(FAIL, _("The API listens on {addresses}, not only on this computer. Run it with --host 127.0.0.1 "
                                    "and put it on the tailnet with tailscale serve.").format(addresses=", ".join(open_to))))
    elif listeners(ss_output):
        checks.append(Check(OK, _("The API listens on this computer only (127.0.0.1).")))
    else:
        checks.append(Check(WARN, _("The API isn't running, so its address could not be checked.")))
    try:
        status = json.loads(tailscale_json or "{}")
    except ValueError:
        status = {}
    funnel = [name for name, on in (status.get("AllowFunnel") or {}).items() if on]
    if funnel:
        checks.append(Check(FAIL, _("{names} is on the public internet (tailscale funnel). Turn it off: tailscale funnel reset").format(
            names=", ".join(funnel))))
    elif status:
        checks.append(Check(OK, _("Nothing is published on the public internet (no tailscale funnel).")))
    return checks


def _files(db_path: str | Path) -> list[Check]:
    checks = []
    for path, what in ((Path(db_path), _("the database")), (Path(db_path).parent, _("its folder"))):
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            continue
        if mode & 0o077:
            checks.append(Check(FAIL, _("{what} ({path}) can be read by other users of this computer (mode {mode}). "
                                        "Run: chmod {fix} {path}").format(what=what, path=path, mode=oct(mode)[2:],
                                                                         fix="700" if path.is_dir() else "600")))
        else:
            checks.append(Check(OK, _("{what} is private (mode {mode}).").format(what=what, mode=oct(mode)[2:])))
    return checks


def _devices(storage, now: datetime) -> list[Check]:
    checks = []
    devices = storage.list_devices()
    stale = [d.name for d in devices if (d.last_seen_at or d.created_at) < now - timedelta(days=STALE_DAYS)]
    if stale:
        checks.append(Check(WARN, _("Not used for {days} days, still signed in: {names}. Sign out: ccc api revoke ID").format(
            days=STALE_DAYS, names=", ".join(stale))))
    return checks


def _limits(storage) -> list[Check]:
    checks = []
    roots = api_scope.roots(storage)
    if any(r == Path(os.path.realpath("/")) or r == Path.home() for r in roots):
        checks.append(Check(WARN, _("An allowed folder is / or your home folder — that is the whole account. "
                                    "Allow project folders instead: ccc api roots")))
    elif roots:
        checks.append(Check(OK, _("The app may start conversations only in: {folders}.").format(
            folders=", ".join(str(r) for r in roots))))
    else:
        checks.append(Check(WARN, _("No folder is set (ccc api roots --add): the app may use the folders ccc already knows.")))
    if api_scope.skip_allowed(storage):
        checks.append(Check(WARN, _("The app may start conversations that skip every permission check. "
                                    "Turn it off: ccc api skip-permissions off")))
    else:
        checks.append(Check(OK, _("The app may not start conversations that skip permission checks.")))
    if Notifier(storage).configured():
        checks.append(Check(OK, _("Alarms reach you through Telegram / Discord.")))
    else:
        checks.append(Check(WARN, _("No Telegram / Discord is set, so a new device or a wrong token goes unnoticed "
                                    "(it is still in `ccc api log`).")))
    return checks


def run_checks(storage, db_path: str | Path, ss_output: str | None = None, tailscale_json: str | None = None,
               now: datetime | None = None) -> list[Check]:
    ss_output = _run(["ss", "-ltnH"]) if ss_output is None else ss_output
    tailscale_json = _run(["tailscale", "serve", "status", "--json"]) if tailscale_json is None else tailscale_json
    return (_network(ss_output, tailscale_json) + _files(db_path) + _devices(storage, now or datetime.now())
            + _limits(storage))
