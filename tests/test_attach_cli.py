import io

import pytest

from ccc import attach_cli, managed_sessions


def test_attach_command_targets_the_session_server(tmux_dir):
    assert attach_cli.attach_command(7) == [
        "tmux", "-S", f"{tmux_dir}/ccc-sessions", "-f", "/dev/null", "attach-session", "-t", "=q7:",
    ]


def test_waits_for_the_session_and_hints_about_the_daemon(monkeypatch):
    answers = iter([False] * 5 + [True])
    monkeypatch.setattr(managed_sessions, "is_running", lambda queue_id: next(answers))
    now = [0.0]

    def sleep(seconds):
        now[0] += 20

    out = io.StringIO()
    attach_cli.wait_for_session(3, out=out, sleep=sleep, clock=lambda: now[0])
    text = out.getvalue()
    assert "Starting Claude Code" in text
    assert text.count("is the ccc daemon running") == 1


def test_main_execs_a_nested_client_without_tmux_env(monkeypatch):
    monkeypatch.setattr(managed_sessions, "is_running", lambda queue_id: True)
    detach = []
    monkeypatch.setattr(managed_sessions, "make_transparent", lambda key=None: detach.append(key))
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/ccc,1,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    called = {}

    def fake_exec(file, args, env):
        called.update(file=file, args=args, env=env)

    monkeypatch.setattr(attach_cli.os, "execvpe", fake_exec)
    attach_cli.main(["5", "F5"])
    assert called["args"] == attach_cli.attach_command(5)
    assert detach == ["F5"]  # the user's close-Claude key (cli_pane passes it)
    assert "TMUX" not in called["env"] and "TMUX_PANE" not in called["env"]


def test_main_rejects_bad_arguments():
    with pytest.raises(SystemExit):
        attach_cli.main(["abc"])
