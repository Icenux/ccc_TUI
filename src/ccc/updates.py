"""ccc updating itself: is there a newer release on GitHub, and apply it.

The same rule as the Android app's (ccc.releases): a new version is the
repository's latest release — tag `X.Y.Z` — with the ccc artifact
(`ccc.tar.gz`) attached, and it is newer by number than what runs here.
A ccc that has no version (a checkout or build made by hand, "0.0.0-dev")
takes the newest tag its checkout contains as its version.

Looking is harmless (one request to GitHub, no token); applying runs only
on a yes — in the TUI's Update window or from the phone:
- ccc as a git checkout (bin/install.sh, the usual way): the release's tag
  is fetched and the checkout fast-forwarded to it — only on main, clean,
  with nothing of its own, while no task runs — then the install script
  runs again and the services restart;
- ccc installed from the system package (.deb or the Arch package): the package manager updates
  it (`apt upgrade` with the Icenux APT repository, `pacman -Syu` with the Icenux pacman repository); ccc only tells that there is a
  newer release and never touches /usr itself;
- ccc installed as a package (pip / pipx): the release's `ccc.tar.gz` is
  downloaded and pip-installed into the environment ccc runs from.

The result of the last look is kept in the `update_state` setting (JSON),
so every reader — the TUI, the API, the daemon's notice — sees the same.
The daemon looks every CHECK_SECONDS (check_enabled(): Settings)."""
from __future__ import annotations

import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from ccc import __version__, heartbeat, releases
from ccc.i18n import _
from ccc.models import QueueStatus

STATE_KEY = "update_state"
CHECK_KEY = "update_check"  # "" = on, "off"
BRANCH = "main"
REMOTE = "origin"
CHECK_SECONDS = 6 * 3600.0
FETCH_TIMEOUT = 40
UNITS = ("ccc.service", "ccc-tui.service", "ccc-api.service")
PROJECT_DIR = Path(__file__).resolve().parents[2]
NOTES_LINES = 12


@dataclass
class UpdateState:
    checked_at: str | None = None
    installed: str | None = None  # the version running here
    version: str | None = None  # the newer release, if there is one
    tag: str | None = None  # its git tag as published (`0.2.0` or `v0.2.0`)
    url: str | None = None  # its ccc.tar.gz
    notes: str = ""  # the release's text
    error: str | None = None
    # The version already told to Telegram / Discord, so it's said once.
    notified_version: str | None = None

    @property
    def available(self) -> bool:
        return self.version is not None

    # What the API (and the Android app, which shows them) has always called these.
    @property
    def behind(self) -> int:
        return 1 if self.available else 0

    @property
    def remote_head(self) -> str | None:
        return self.version

    @property
    def commits(self) -> list[str]:
        """The first lines of the release notes, for the app's list."""
        return self.notes_lines()[:NOTES_LINES]

    def notes_lines(self) -> list[str]:
        return [line.rstrip() for line in self.notes.splitlines() if line.strip()]


def load(storage) -> UpdateState:
    try:
        stored = json.loads(storage.get_setting(STATE_KEY) or "{}")
        known = {f.name for f in fields(UpdateState)}
        return UpdateState(**{k: v for k, v in stored.items() if k in known})
    except (ValueError, TypeError, AttributeError):
        return UpdateState()


def save(storage, state: UpdateState) -> None:
    storage.set_setting(STATE_KEY, json.dumps(asdict(state)))


def check_enabled(storage) -> bool:
    return storage.get_setting(CHECK_KEY) != "off"


def _git(repo: Path, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    # Never ask for a password: a fetch that needs one fails.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "LC_ALL": "C"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, env=env)


def is_checkout(repo: Path = PROJECT_DIR) -> bool:
    """ccc runs from a git checkout made by bin/install.sh (not from site-packages)."""
    return (repo / ".git").exists() and (repo / "bin" / "install.sh").is_file()


def installed_by_system() -> str | None:
    """How the system package manager installed ccc ("deb" or "pacman"), None when it did not:
    the package ships /usr/lib/ccc/install-kind next to its virtualenv."""
    try:
        return (Path(sys.prefix).parent / "install-kind").read_text().strip() or None
    except OSError:
        return None


def installed_version(repo: Path = PROJECT_DIR) -> str:
    """The version of the ccc running here: the one stamped into the
    package by the release build, else — a checkout — its newest tag; a
    version that is not a number ("0.0.0-dev") never asks for an update."""
    if releases.parse_version(__version__):
        return __version__
    if is_checkout(repo):
        try:
            tag = _git(repo, "describe", "--tags", "--abbrev=0", "--match", "[0-9]*", "HEAD").stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            tag = ""
        if releases.parse_version(tag):
            return tag
    return __version__


@functools.cache
def running_version() -> str:
    """installed_version() for display, looked up once (the services restart on an update)."""
    return installed_version()


def _latest() -> releases.Release | None:
    """The newest release with the ccc artifact."""
    return releases.parse_release(releases.fetch())


def check(storage, repo: Path = PROJECT_DIR, latest=_latest) -> UpdateState:
    """Ask GitHub for the latest release and compare with this ccc; the
    outcome is saved (an error as well — "can't look" is something to show)."""
    previous = load(storage)
    installed = installed_version(repo)
    state = UpdateState(checked_at=datetime.now().isoformat(timespec="seconds"), installed=installed,
                        notified_version=previous.notified_version)
    try:
        release = latest()
        if release and releases.is_newer(release.version, installed):
            state.version, state.tag, state.url = release.version, release.tag, release.url
            state.notes = release.notes
    except OSError as exc:
        # Only the kind of failure: the message may name a URL.
        state.error = type(getattr(exc, "reason", None) or exc).__name__
    save(storage, state)
    return state


def refusal(storage, repo: Path = PROJECT_DIR) -> str | None:
    """Why an update can't be applied now, in words for the user; None when it can."""
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() if is_checkout(repo) else BRANCH
        if branch != BRANCH:
            return _("The checkout is on branch “{branch}”, not {main}.").format(branch=branch, main=BRANCH)
        if is_checkout(repo):
            if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
                return _("The checkout has uncommitted changes.")
            ahead = _git(repo, "rev-list", "--count", f"{REMOTE}/{BRANCH}..HEAD").stdout.strip()
            if ahead not in ("", "0"):
                return _("The checkout has commits that aren't on {remote}/{main}.").format(remote=REMOTE, main=BRANCH)
    except (OSError, subprocess.TimeoutExpired):
        return _("git can't be run in the ccc checkout.")
    return running_task_reason(storage)


def running_task_reason(storage) -> str | None:
    """Why ccc must not restart now: a task is running and the restart would stop it; None when it may."""
    running = [q.name for q in storage.list_queues() if q.status == QueueStatus.RUNNING and not q.archived]
    if running:
        return _("A task is running ({names}) — the restart would stop it.").format(names=", ".join(running[:3]))
    return None


def version_on_disk() -> str | None:
    """The version of the ccc files installed now (an upgrade of the package replaces them under a
    running ccc): read from the package's own __init__.py, not from the loaded module."""
    try:
        text = (Path(__file__).resolve().parent / "__init__.py").read_text()
    except OSError:
        return None
    found = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    return found.group(1) if found else None


def restart_needed(storage) -> bool:
    """ccc was upgraded under its own running processes: the installed files are a different version
    than the TUI / API running here or the daemon (it stamps its version with every heartbeat).
    A checkout or a build by hand has no version (0.0.0-dev) and never asks."""
    disk = version_on_disk()
    if not disk or not releases.parse_version(disk):
        return False
    running = {__version__, storage.get_setting(heartbeat.VERSION_KEY) or disk}
    return running != {disk}


def running_units() -> list[str]:
    """The ccc systemd user units to restart: the ones active now, and the ones enabled but not running
    (an API killed by the OOM killer, or one that did not come up after a reboot, must not stay dead because
    an update only restarted what happened to be alive — `restart` starts a stopped unit too). The package's
    units live in /usr/lib/systemd/user, a checkout's in ~/.config/systemd/user — either way systemd knows them."""
    wanted = []
    for unit in UNITS:
        try:
            if any(subprocess.run(["systemctl", "--user", verb, "--quiet", unit], capture_output=True,
                                  timeout=10).returncode == 0 for verb in ("is-active", "is-enabled")):
                wanted.append(unit)
        except (OSError, subprocess.TimeoutExpired):
            return []
    return wanted


def restart(storage, spawn=None, units=None) -> tuple[bool, str]:
    """Restart ccc's services (after an upgrade of the package). Only while no task runs: the restart
    would stop it. The conversations (their own tmux server) keep running."""
    reason = running_task_reason(storage)
    if reason:
        return False, reason
    active = (units or running_units)()
    if not active:
        return False, _("No ccc service is running under systemd — restart ccc by hand.")
    (spawn or spawn_install)("systemctl --user restart " + " ".join(active), _log_path(storage))
    return True, _("ccc is restarting.")


def installed_units() -> list[str]:
    """The ccc systemd user units this checkout was installed with."""
    folder = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    return [unit for unit in UNITS if (folder / unit).exists()]


def install_command(units: list[str], repo: Path = PROJECT_DIR) -> str:
    """The shell line after the pull: the install script (the same options
    this checkout was installed with, not starting anything), then the
    units restarted — `enable --now` leaves running ones on the old code."""
    flags = ["--no-start"]
    if "ccc-tui.service" not in units:
        flags.append("--no-tui")
    if "ccc-api.service" in units:
        flags.append("--api")
    if not units:
        flags = ["--no-systemd"]
    line = f"{shlex.quote(str(repo / 'bin' / 'install.sh'))} {' '.join(flags)}"
    if units:
        line += " && systemctl --user restart " + " ".join(units)
    return line


def package_command(artifact: Path, units: list[str]) -> str:
    """The shell line for ccc installed as a package: pip-install the
    release's artifact into the environment ccc runs from, then restart."""
    line = f"{shlex.quote(sys.executable)} -m pip install --quiet --upgrade {shlex.quote(str(artifact))}"
    if units:
        line += " && systemctl --user restart " + " ".join(units)
    return line


def spawn_install(command: str, log: Path) -> None:
    """Run `command` outside this process: the restart stops the TUI /
    daemon / API that asked for it. In its own transient unit when systemd
    has one (its cgroup isn't the restarted unit's), else detached."""
    script = f"{command} >> {shlex.quote(str(log))} 2>&1"
    if shutil.which("systemd-run") and subprocess.run(
            ["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0:
        subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "--unit", "ccc-update", "bash", "-c", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["bash", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _move_checkout(repo: Path, tag: str) -> str | None:
    """Fetch the release's tag and fast-forward the checkout to it; None when
    done, else why not (nothing is changed then)."""
    fetched = _git(repo, "fetch", "--quiet", REMOTE, BRANCH, f"refs/tags/{tag}:refs/tags/{tag}", timeout=FETCH_TIMEOUT * 3)
    if fetched.returncode != 0:
        return (fetched.stderr.strip().splitlines() or ["git fetch failed"])[0][:200]
    # Only a release that is part of main, and only forward from here.
    if (_git(repo, "merge-base", "--is-ancestor", tag, f"{REMOTE}/{BRANCH}").returncode != 0
            or _git(repo, "merge-base", "--is-ancestor", "HEAD", tag).returncode != 0):
        return _("The release {tag} can't be reached from this checkout by a fast-forward of {main}.").format(
            tag=tag, main=BRANCH)
    merged = _git(repo, "merge", "--ff-only", "--quiet", tag, timeout=FETCH_TIMEOUT * 3)
    if merged.returncode != 0:
        return (merged.stderr.strip().splitlines() or ["git merge failed"])[0][:200]
    return None


def apply(storage, repo: Path = PROJECT_DIR, spawn=spawn_install, units=installed_units,
          latest=_latest, download=releases.download) -> tuple[bool, str]:
    """Move to the new release and restart. (True, message) once it's on its
    way; (False, why) when not — nothing is changed then."""
    state = check(storage, repo, latest)
    if state.error:
        return False, _("Can't look for the update: {error}").format(error=state.error)
    if not state.available:
        return False, _("ccc is up to date.")
    kind = installed_by_system()
    if kind == "pacman":
        return False, _("ccc was installed from a system package — update it with: sudo pacman -Syu")
    if kind:
        return False, _("ccc was installed from a system package — update it with: sudo apt update && sudo apt upgrade")
    checkout = is_checkout(repo)
    if checkout:
        # The "commits that aren't on origin/main" test compares with a fresh main.
        try:
            _git(repo, "fetch", "--quiet", REMOTE, BRANCH, timeout=FETCH_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            pass
    reason = refusal(storage, repo)
    if reason:
        return False, reason
    log = _log_path(storage)
    try:
        if checkout:
            failed = _move_checkout(repo, state.tag or state.version)
            if failed:
                return False, failed
            command = install_command(units(), repo)
        else:
            artifact = log.parent / "update" / releases.ASSET
            download(state.url, artifact)
            command = package_command(artifact, units())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, _("The update failed: {error}").format(error=type(exc).__name__)
    spawn(command, log)
    save(storage, UpdateState(checked_at=state.checked_at, installed=state.version,
                              notified_version=state.version))
    return True, _("Updated to {head} — ccc is restarting.").format(head=state.version)


def _log_path(storage) -> Path:
    return Path(storage.db_path).parent / "update.log"


def notice(state: UpdateState) -> str:
    """The message for Telegram / Discord."""
    lines = [_("⬆️ ccc update available: {version} (installed: {installed}).").format(
        version=state.version, installed=state.installed)]
    lines += [f"• {line}" for line in state.notes_lines()[:5]]
    lines.append(_("Update it in ccc (Update window) or from the phone."))
    return "\n".join(lines)
