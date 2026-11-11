import subprocess
from pathlib import Path

import pytest

from ccc import daemon, releases, updates
from ccc.models import QueueStatus
from ccc.storage import Storage


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    """origin (bare), the ccc checkout (a clone on main, tagged 0.1.0) and a second clone that pushes."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    ccc = tmp_path / "ccc"
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(ccc)], check=True, capture_output=True)
    (ccc / "a.txt").write_text("one\n")
    (ccc / "bin").mkdir()
    (ccc / "bin" / "install.sh").write_text("#!/bin/sh\n")
    git(ccc, "add", "."); git(ccc, "commit", "-qm", "first"); git(ccc, "tag", "0.1.0")
    git(ccc, "push", "-q", "origin", "main", "--tags")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    return ccc, other


def release_tag(other, tag, name="b.txt", folder=""):
    """The developer pushes a commit and a tag; the release itself is GitHub's."""
    path = other / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tag)
    git(other, "add", "."); git(other, "commit", "-qm", f"for {tag}"); git(other, "push", "-q", "origin", "main")
    git(other, "tag", tag); git(other, "push", "-q", "origin", tag)


def github(version="0.2.0", notes="Dashboard\n\nFaster start", tag=None):
    """What releases/latest says, as updates.check() gets it."""
    release = releases.Release(version, f"https://x/{version}/ccc.tar.gz", notes, tag or version)
    return lambda: release


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "h.db"))


def test_up_to_date_then_a_newer_release(repos, storage):
    ccc, _other = repos
    assert not updates.check(storage, ccc, github("0.1.0")).available
    state = updates.check(storage, ccc, github("0.2.0"))
    assert state.available and state.version == "0.2.0" and state.installed == "0.1.0" and state.error is None
    assert state.notes_lines() == ["Dashboard", "Faster start"]
    assert updates.load(storage) == state  # kept for the TUI, the API and the phone
    assert (state.behind, state.remote_head, state.commits) == (1, "0.2.0", ["Dashboard", "Faster start"])


def test_a_release_without_the_artifact_or_an_older_one_is_not_an_update(repos, storage):
    ccc, _other = repos
    assert not updates.check(storage, ccc, lambda: None).available
    assert not updates.check(storage, ccc, github("0.0.9")).available


def test_a_ccc_without_a_version_never_asks(tmp_path, storage):
    nowhere = tmp_path / "not-a-checkout"
    nowhere.mkdir()
    assert updates.installed_version(nowhere) == "0.0.0-dev"
    assert not updates.check(storage, nowhere, github("9.9.9")).available


def test_a_stamped_version_wins_over_the_checkouts_tag(repos, monkeypatch):
    ccc, _other = repos
    assert updates.installed_version(ccc) == "0.1.0"
    monkeypatch.setattr(updates, "__version__", "0.1.5")
    assert updates.installed_version(ccc) == "0.1.5"


def test_github_not_reachable_is_an_error_not_a_crash(repos, storage):
    ccc, _other = repos

    def down():
        raise OSError("no route")

    state = updates.check(storage, ccc, down)
    assert state.error == "OSError" and not state.available
    assert updates.load(storage).error == "OSError"


def test_apply_moves_the_checkout_to_the_tag_and_starts_the_install(repos, storage):
    ccc, other = repos
    release_tag(other, "0.2.0")
    release_tag(other, "0.3.0", "c.txt")  # main has moved on past the release
    spawned = []
    ok, message = updates.apply(storage, ccc, spawn=lambda command, log: spawned.append((command, log)),
                                units=lambda: ["ccc.service", "ccc-tui.service"], latest=github("0.2.0"))
    assert ok and "0.2.0" in message and "restarting" in message
    assert (ccc / "b.txt").exists() and not (ccc / "c.txt").exists()  # the release, not main's head
    command, log = spawned[0]
    assert command.endswith("--no-start && systemctl --user restart ccc.service ccc-tui.service")
    assert "--no-tui" not in command and "--api" not in command
    assert log.name == "update.log"
    assert not updates.load(storage).available and updates.load(storage).installed == "0.2.0"


def test_a_tag_with_a_v_is_fetched_by_its_real_name(repos, storage):
    ccc, other = repos
    release_tag(other, "v0.2.0")
    ok, _ = updates.apply(storage, ccc, spawn=lambda *a: None, units=lambda: [], latest=github("0.2.0", tag="v0.2.0"))
    assert ok and (ccc / "b.txt").exists()


def test_a_release_that_isnt_part_of_main_is_not_applied(repos, storage):
    ccc, other = repos
    git(other, "checkout", "-qb", "side")
    (other / "s.txt").write_text("s")
    git(other, "add", "."); git(other, "commit", "-qm", "side"); git(other, "tag", "0.2.0")
    git(other, "push", "-q", "origin", "0.2.0")
    ok, message = updates.apply(storage, ccc, spawn=lambda *a: pytest.fail("not this"), units=lambda: [],
                                latest=github("0.2.0"))
    assert not ok and "fast-forward" in message and not (ccc / "s.txt").exists()


def test_ccc_as_a_package_downloads_the_artifact_and_pip_installs_it(tmp_path, storage, monkeypatch):
    package = tmp_path / "site-packages"
    package.mkdir()
    monkeypatch.setattr(updates, "__version__", "0.1.0")
    spawned, downloaded = [], []
    ok, message = updates.apply(storage, package, spawn=lambda command, log: spawned.append(command),
                                units=lambda: ["ccc.service"], latest=github("0.2.0"),
                                download=lambda url, target: downloaded.append((url, target)))
    assert ok, message
    assert downloaded[0][0] == "https://x/0.2.0/ccc.tar.gz" and downloaded[0][1].name == "ccc.tar.gz"
    assert "-m pip install --quiet --upgrade" in spawned[0] and spawned[0].endswith("restart ccc.service")


def test_ccc_from_a_system_package_leaves_the_update_to_apt(tmp_path, storage, monkeypatch):
    package = tmp_path / "usr-lib-ccc"
    (package / "venv").mkdir(parents=True)
    (package / "install-kind").write_text("deb\n")
    monkeypatch.setattr(updates.sys, "prefix", str(package / "venv"))
    monkeypatch.setattr(updates, "__version__", "0.1.0")
    assert updates.installed_by_system() == "deb"
    ok, message = updates.apply(storage, package, spawn=lambda *a: pytest.fail("apt does this"), units=lambda: [],
                                latest=github("0.2.0"), download=lambda *a: pytest.fail("apt does this"))
    assert not ok and "apt upgrade" in message


def test_ccc_from_the_arch_package_leaves_the_update_to_pacman(tmp_path, storage, monkeypatch):
    package = tmp_path / "usr-lib-ccc"
    (package / "venv").mkdir(parents=True)
    (package / "install-kind").write_text("pacman\n")
    monkeypatch.setattr(updates.sys, "prefix", str(package / "venv"))
    monkeypatch.setattr(updates, "__version__", "0.1.0")
    assert updates.installed_by_system() == "pacman"
    ok, message = updates.apply(storage, package, spawn=lambda *a: pytest.fail("pacman does this"), units=lambda: [],
                                latest=github("0.2.0"), download=lambda *a: pytest.fail("pacman does this"))
    assert not ok and "pacman -Syu" in message and "apt" not in message


def test_a_download_that_fails_changes_nothing(tmp_path, storage, monkeypatch):
    package = tmp_path / "site-packages"
    package.mkdir()
    monkeypatch.setattr(updates, "__version__", "0.1.0")

    def broken(url, target):
        raise OSError("reset")

    ok, message = updates.apply(storage, package, spawn=lambda *a: pytest.fail("nothing to install"),
                                units=lambda: [], latest=github("0.2.0"), download=broken)
    assert not ok and "OSError" in message


@pytest.mark.parametrize("units, expected", [
    (["ccc.service"], "--no-start --no-tui"),
    (["ccc.service", "ccc-tui.service", "ccc-api.service"], "--no-start --api"),
    ([], "--no-systemd"),
])
def test_install_flags_follow_how_it_was_installed(units, expected):
    command = updates.install_command(units, Path("/opt/ccc"))
    assert f"/opt/ccc/bin/install.sh {expected}" in command
    assert ("systemctl" in command) == bool(units)


@pytest.mark.parametrize("prepare, word", [
    (lambda ccc, other, st: git(ccc, "checkout", "-qb", "feature"), "feature"),
    (lambda ccc, other, st: (ccc / "a.txt").write_text("changed\n"), "uncommitted"),
    (lambda ccc, other, st: (((ccc / "x.txt").write_text("x"), git(ccc, "add", "."), git(ccc, "commit", "-qm", "local"))),
     "aren't on"),
    (lambda ccc, other, st: st.update_queue_status(st.create_queue("shop", "/tmp", "s1").id, QueueStatus.RUNNING),
     "shop"),
])
def test_apply_refuses_when_it_isnt_safe_and_changes_nothing(repos, storage, prepare, word):
    ccc, other = repos
    release_tag(other, "0.2.0")
    prepare(ccc, other, storage)
    spawned = []
    ok, message = updates.apply(storage, ccc, spawn=lambda *a: spawned.append(a), units=lambda: [], latest=github("0.2.0"))
    assert not ok and word in message
    assert spawned == [] and not (ccc / "b.txt").exists()  # nothing was moved


def test_apply_when_up_to_date(repos, storage):
    ccc, _other = repos
    ok, message = updates.apply(storage, ccc, spawn=lambda *a: pytest.fail("nothing to install"), units=lambda: [],
                                latest=github("0.1.0"))
    assert not ok and "up to date" in message


class _Notifier:
    def __init__(self):
        self.sent = []

    def send_now(self, text, category=None):
        self.sent.append(text)
        return True


def test_the_daemon_tells_once_per_new_version(repos, storage):
    ccc, _other = repos
    notifier = _Notifier()
    assert daemon.update_tick(storage, notifier, ccc, github("0.1.0")) is None  # nothing new
    text = daemon.update_tick(storage, notifier, ccc, github("0.2.0"))
    assert "0.2.0" in text and "installed: 0.1.0" in text and "Faster start" in text
    assert daemon.update_tick(storage, notifier, ccc, github("0.2.0")) is None  # the same version: not again
    assert daemon.update_tick(storage, notifier, ccc, github("0.3.0")) is not None
    assert len(notifier.sent) == 2


def test_the_check_can_be_turned_off(repos, storage):
    ccc, _other = repos
    storage.set_setting(updates.CHECK_KEY, "off")
    assert daemon.update_tick(storage, _Notifier(), ccc, github("0.2.0")) is None
    assert not updates.load(storage).available


def test_restart_is_needed_after_an_upgrade_under_a_running_ccc(storage, monkeypatch):
    from ccc import heartbeat

    monkeypatch.setattr(updates, "__version__", "0.1.3")
    monkeypatch.setattr(updates, "version_on_disk", lambda: "0.1.3")
    storage.set_setting(heartbeat.VERSION_KEY, "0.1.3")
    assert not updates.restart_needed(storage)
    monkeypatch.setattr(updates, "version_on_disk", lambda: "0.1.4")  # apt replaced the files
    assert updates.restart_needed(storage)
    monkeypatch.setattr(updates, "__version__", "0.1.4")  # the TUI was restarted, the daemon was not
    assert updates.restart_needed(storage)
    storage.set_setting(heartbeat.VERSION_KEY, "0.1.4")
    assert not updates.restart_needed(storage)


def test_a_checkout_or_a_handmade_build_never_needs_a_restart(storage, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.0.0-dev")
    monkeypatch.setattr(updates, "version_on_disk", lambda: "0.0.0-dev")
    assert not updates.restart_needed(storage)
    monkeypatch.setattr(updates, "version_on_disk", lambda: None)
    assert not updates.restart_needed(storage)


def test_version_on_disk_reads_the_installed_file():
    from ccc import __version__

    assert updates.version_on_disk() == __version__


def test_restart_refuses_while_a_task_runs_and_otherwise_restarts_the_units(storage):
    queue = storage.create_queue("shop", "/tmp", "s1")
    storage.update_queue_status(queue.id, QueueStatus.RUNNING)
    ok, message = updates.restart(storage, spawn=lambda *a: pytest.fail("not now"), units=lambda: ["ccc.service"])
    assert not ok and "shop" in message
    storage.update_queue_status(queue.id, QueueStatus.IDLE)
    spawned = []
    ok, _message = updates.restart(storage, spawn=lambda command, log: spawned.append(command),
                                   units=lambda: ["ccc.service", "ccc-tui.service"])
    assert ok and spawned == ["systemctl --user restart ccc.service ccc-tui.service"]
    ok, message = updates.restart(storage, spawn=lambda *a: pytest.fail("nothing to restart"), units=lambda: [])
    assert not ok and "by hand" in message


def test_restart_also_covers_an_enabled_unit_that_is_not_running(monkeypatch):
    """ccc-api was dead (not started after a reboot) and an update left it dead: enabled counts, too."""
    state = {"ccc.service": {"is-active"}, "ccc-tui.service": {"is-active", "is-enabled"}, "ccc-api.service": {"is-enabled"}}

    def fake_run(cmd, **_):
        verb, unit = cmd[2], cmd[-1]
        return subprocess.CompletedProcess(cmd, 0 if verb in state.get(unit, set()) else 3)

    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates.running_units() == ["ccc.service", "ccc-tui.service", "ccc-api.service"]
    state["ccc-api.service"] = set()  # neither running nor enabled: left alone
    assert updates.running_units() == ["ccc.service", "ccc-tui.service"]
