import subprocess
import threading

from ccc import clipboard_bridge


def test_changed_buffer_parses_only_the_notification():
    assert clipboard_bridge.changed_buffer("%paste-buffer-changed buffer3\n") == "buffer3"
    assert clipboard_bridge.changed_buffer("%paste-buffer-deleted buffer3") is None
    assert clipboard_bridge.changed_buffer("%output %1 hello") is None


def test_ccc_own_paste_buffers_never_reach_the_clipboard():
    # paste_and_submit loads queued prompts / presets as "ccc-<id>".
    assert clipboard_bridge.changed_buffer("%paste-buffer-changed ccc-19") is None
    assert clipboard_bridge.changed_buffer("%paste-buffer-changed mybuf") is None
    assert clipboard_bridge.changed_buffer("%paste-buffer-changed buffer12x") is None


def test_forward_loads_into_ccc_with_clipboard(monkeypatch, tmux_dir):
    calls = []

    def run(args, input=None, capture_output=True, timeout=5):
        calls.append((args, input))
        return subprocess.CompletedProcess(args, 0, b"copied text" if "save-buffer" in args else b"", b"")

    monkeypatch.setattr(clipboard_bridge.subprocess, "run", run)
    assert clipboard_bridge.forward("buffer3")
    assert calls[0][0] == ["tmux", "-S", f"{tmux_dir}/ccc-sessions", "-f", "/dev/null",
                           "save-buffer", "-b", "buffer3", "-"]
    assert calls[1] == (["tmux", "-S", f"{tmux_dir}/ccc", "-f", "/dev/null", "load-buffer", "-w", "-"],
                        b"copied text")


def test_control_client_is_read_only(tmux_dir):
    assert clipboard_bridge.control_command(7) == [
        "tmux", "-S", f"{tmux_dir}/ccc-sessions", "-f", "/dev/null", "-C", "attach-session", "-r", "-t", "=q7:",
    ]


def test_listen_forwards_each_new_buffer(monkeypatch):
    stop = threading.Event()
    forwarded = []

    class FakeClient:
        def __init__(self, *a, **k):
            self.stdout = iter(["%begin 1\n", "%paste-buffer-changed buffer1\n", "%output %1 x\n",
                                "%paste-buffer-changed buffer2\n"])

        def kill(self):
            stop.set()

        def wait(self):
            pass

    monkeypatch.setattr(clipboard_bridge.subprocess, "Popen", FakeClient)
    monkeypatch.setattr(clipboard_bridge, "forward", lambda name: forwarded.append(name))
    clipboard_bridge.listen(7, stop)
    assert forwarded == ["buffer1", "buffer2"]
