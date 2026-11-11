import subprocess

from ccc import instance


def _tmux_has_session(monkeypatch, running):
    monkeypatch.setattr(instance.subprocess, "run",
                        lambda args, capture_output: subprocess.CompletedProcess(args, 0 if running else 1))


def test_attaches_to_the_running_instance(monkeypatch, tmux_dir):
    _tmux_has_session(monkeypatch, True)
    assert instance.attach_command() == ["tmux", "-S", f"{tmux_dir}/ccc", "-f", "/dev/null", "attach-session", "-t", "ccc"]


def test_starts_the_instance_when_it_isnt_running(monkeypatch, tmux_dir):
    _tmux_has_session(monkeypatch, False)
    command = instance.attach_command()
    assert command[:7] == ["tmux", "-S", f"{tmux_dir}/ccc", "-f", "/dev/null", "new-session", "-s"]
    assert command[-1] == instance.tui_loop()
    assert "-m ccc.cli tui" in instance.tui_loop() and instance.tui_loop().startswith("while true")


def test_inside_ccc_tmux(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/ccc,1,0")
    assert instance.inside_ccc_tmux()
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    assert not instance.inside_ccc_tmux()
    monkeypatch.delenv("TMUX")
    assert not instance.inside_ccc_tmux()


def test_attach_is_a_nested_client(monkeypatch):
    _tmux_has_session(monkeypatch, True)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    called = {}
    monkeypatch.setattr(instance.os, "execvpe", lambda file, args, env: called.update(args=args, env=env))
    instance.attach()
    assert called["args"][-1] == "ccc" and "TMUX" not in called["env"]
