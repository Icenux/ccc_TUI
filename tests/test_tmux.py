import os
import shutil
import socket
import subprocess

import pytest

from ccc import tmux


def _listen(path: str) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    return server


def _legacy_dir() -> str:
    path = os.path.dirname(tmux.legacy_socket_path("x"))
    os.makedirs(path, exist_ok=True)
    return path


def test_binary_is_tmux_unless_set(monkeypatch):
    assert tmux.binary() == "tmux"
    monkeypatch.setenv("CCC_TMUX_BIN", "/opt/ccc/tmux")
    assert tmux.binary() == "/opt/ccc/tmux"
    assert tmux.command("ccc", "ls")[0] == "/opt/ccc/tmux"
    assert tmux.own("detach-client") == ["/opt/ccc/tmux", "detach-client"]


def test_socket_dir_is_ccc_own_not_tmux(monkeypatch):
    monkeypatch.delenv("CCC_TMUX_DIR")
    assert tmux.socket_dir() == f"/tmp/ccc-tmux-{os.getuid()}"
    monkeypatch.setenv("TMUX_TMPDIR", "/elsewhere")  # the user's tmux setting doesn't move ccc's
    assert tmux.socket_dir() == f"/tmp/ccc-tmux-{os.getuid()}"


def test_command_uses_own_socket_and_no_user_config(tmux_dir):
    assert tmux.command("ccc-sessions", "has-session", "-t", "=q1:") == [
        "tmux", "-S", f"{tmux_dir}/ccc-sessions", "-f", "/dev/null", "has-session", "-t", "=q1:",
    ]


def test_socket_dir_is_created_private(tmp_path, monkeypatch):
    path = tmp_path / "sockets"
    monkeypatch.setenv("CCC_TMUX_DIR", str(path))
    assert tmux.server("ccc") == ["-S", str(path / "ccc")]
    assert path.is_dir() and (path.stat().st_mode & 0o777) == 0o700
    path.chmod(0o755)
    tmux.server("ccc")
    assert (path.stat().st_mode & 0o777) == 0o700


def test_a_symlinked_socket_dir_is_not_used(tmp_path, monkeypatch):
    (tmp_path / "real").mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    monkeypatch.setenv("CCC_TMUX_DIR", str(tmp_path / "link"))
    assert tmux.server("ccc") == ["-L", "ccc"]


def test_a_server_from_before_keeps_being_used(tmux_dir):
    legacy = _listen(os.path.join(_legacy_dir(), "ccc-sessions"))
    try:
        assert tmux.server("ccc-sessions") == ["-L", "ccc-sessions"]
        assert tmux.server("ccc") == ["-S", f"{tmux_dir}/ccc"]  # only the server that runs
        new = _listen(os.path.join(tmux_dir, "ccc-sessions"))
        try:
            assert tmux.server("ccc-sessions") == ["-S", f"{tmux_dir}/ccc-sessions"]
        finally:
            new.close()
    finally:
        legacy.close()


def test_a_stale_socket_from_before_is_ignored(tmux_dir):
    path = os.path.join(_legacy_dir(), "ccc-sessions")
    _listen(path).close()  # the file stays, nobody listens
    assert os.path.exists(path)
    assert tmux.server("ccc-sessions") == ["-S", f"{tmux_dir}/ccc-sessions"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_a_real_server_lives_in_ccc_own_directory(tmux_dir):
    name = "ccc-test"
    try:
        started = subprocess.run(tmux.command(name, "new-session", "-d", "-s", "t", "sleep 30"),
                                 capture_output=True, timeout=10)
        assert started.returncode == 0, started.stderr
        assert os.path.exists(os.path.join(tmux_dir, name))
        assert not os.path.exists(tmux.legacy_socket_path(name))
        assert subprocess.run(tmux.command(name, "has-session", "-t", "=t"), timeout=10).returncode == 0
    finally:
        subprocess.run(tmux.command(name, "kill-server"), capture_output=True, timeout=10)
