import subprocess

import pytest

from ccc import managed_sessions as ms
from ccc.models import DeliveryMode, Queue


def _queue(**kw):
    base = dict(id=7, name="my conv", project_path="/tmp/p", session_id="sid-1",
                delivery_mode=DeliveryMode.REMOTE_CONTROL, launch_flags=["--model", "haiku"])
    base.update(kw)
    return Queue(**base)


def test_command_new_session_uses_session_id():
    assert ms.build_interactive_command(_queue(), has_transcript=False) == [
        "claude", "--session-id", "sid-1", "--remote-control", "my conv", "--model", "haiku",
    ]


def test_interactive_command_has_no_remote_control():
    q = _queue(delivery_mode=DeliveryMode.INTERACTIVE)
    assert ms.build_interactive_command(q, has_transcript=False) == [
        "claude", "--session-id", "sid-1", "--model", "haiku",
    ]


def test_start_makes_the_server_transparent(monkeypatch):
    calls = []
    def fake(*args, input_text=None):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "")  # no detach key bound yet

    monkeypatch.setattr(ms, "_tmux", fake)
    ms.start(_queue(delivery_mode=DeliveryMode.INTERACTIVE), ["claude"])
    assert calls[0][:4] == ("new-session", "-d", "-s", "q7")
    assert ("set-option", "-g", "prefix", "None") in calls
    assert ("set-option", "-g", "status", "off") in calls
    assert ("bind-key", "-n", "C-]", "detach-client") in calls
    # Programs in the session must not be able to set the clipboard (OSC 52),
    # also on a server that was started with another value.
    assert [c for c in calls if "set-clipboard" in c] == [("set-option", "-g", "set-clipboard", "external")]
    # Mouse copies only fill a paste buffer (clipboard_bridge takes it on).
    # The selection stays shown after letting go (#276); Ctrl+Shift+C leaves copy mode.
    assert ("bind-key", "-T", "copy-mode", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-no-clear") in calls
    assert ("bind-key", "-T", "copy-mode", "C-S-c", "send-keys", "-X", "copy-pipe-and-cancel") in calls
    # The embedded frame's sequences: copy-mode only, nothing outside it.
    assert ("set-option", "-s", "user-keys[0]", ms.COPY_SEQUENCE) in calls
    assert ("set-option", "-s", "user-keys[1]", ms.CANCEL_SEQUENCE) in calls
    assert ("bind-key", "-n", "User1", "if-shell", "-F", "#{pane_in_mode}", "send-keys -X cancel", "") in calls
    assert not any("terminal-features" in c and "clipboard" in str(c) for c in calls)


def test_a_changed_detach_key_replaces_the_old_one(monkeypatch):
    calls = []

    def fake(*args, input_text=None):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "C-]\n", "")

    monkeypatch.setattr(ms, "_tmux", fake)
    ms.set_detach_key("F5")
    assert ("unbind-key", "-n", "C-]") in calls
    assert ("bind-key", "-n", "F5", "detach-client") in calls
    assert ("set-option", "-g", ms.DETACH_OPTION, "F5") in calls


def test_command_existing_session_resumes():
    assert ms.build_interactive_command(_queue(), has_transcript=True)[1:3] == ["--resume", "sid-1"]


def test_command_first_run_flags_pick_the_session():
    q = _queue(first_run_flags=["--resume", "orig", "--fork-session"])
    assert ms.build_interactive_command(q, has_transcript=False)[:4] == ["claude", "--resume", "orig", "--fork-session"]


def test_parse_remote_url_and_trust_prompt():
    pane = "  /remote-control is active · Continue here, on your phone, or at https://claude.ai/code/session_012kihHi2x9g\n"
    assert ms.parse_remote_url(pane) == "https://claude.ai/code/session_012kihHi2x9g"
    assert ms.parse_remote_url("nothing here") is None
    assert ms.needs_trust_confirmation("Do you trust the files in this folder?\n❯ 1. Yes, proceed\n  2. No, exit")
    assert not ms.needs_trust_confirmation("❯ Řekni jen OK")
    # Claude merely mentioning the phrase in a reply is not the dialog.
    assert not ms.needs_trust_confirmation("● Do you trust the files in this folder? That's the dialog text.")
    # Any other prompt (e.g. a tool permission question) is never confirmed.
    assert not ms.needs_trust_confirmation("Do you want to proceed?\n❯ 1. Yes\n  2. No")


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(args, input_text=None):
        recorded.append((args, input_text))
        if "has-session" in args:
            return subprocess.CompletedProcess(args, 1, "", "")
        if "display-message" in args:
            return subprocess.CompletedProcess(args, 0, "4242\n", "")
        if "capture-pane" in args:
            return subprocess.CompletedProcess(args, 0, "pane text\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ms, "_run", fake_run)
    # Never let a test signal a real process: the fake pane pid is 4242.
    monkeypatch.setattr(ms, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ms, "_signal", lambda pid, sig: None)
    return recorded


def test_tmux_calls_use_own_socket_and_target(calls, tmux_dir):
    q = _queue()
    assert ms.is_running(q.id) is False
    ms.start(q, ["claude", "--session-id", "sid-1", "--remote-control", "my conv"])
    assert ms.pane_pid(q.id) == 4242
    assert ms.capture(q.id) == "pane text\n"
    ms.stop(q.id)
    for args, _ in calls:
        assert args[:5] == ["tmux", "-S", f"{tmux_dir}/{ms.TMUX_SOCKET}", "-f", "/dev/null"]
    start_args = calls[1][0]
    assert "new-session" in start_args and "-c" in start_args and "/tmp/p" in start_args
    assert start_args[-1] == "env CLAUDE_CODE_NO_FLICKER=1 claude --session-id sid-1 --remote-control 'my conv'"  # fullscreen: prompt at the bottom (#274)
    assert ["kill-session", "-t", "=q7:"] in [args[5:] for args, _ in calls]


def test_paste_and_submit_uses_bracketed_paste(calls):
    result = ms.paste_and_submit(7, "line one\nline two")
    assert result.delivered
    kinds = [args[5] for args, _ in calls]
    assert kinds == ["load-buffer", "paste-buffer", "send-keys"]
    assert calls[0][1] == "line one\nline two"
    assert "-p" in calls[1][0] and "-d" in calls[1][0]
    assert calls[2][0][-1] == "Enter"


def test_stop_escalates_when_process_lingers(calls, monkeypatch):
    alive = {"n": 0}
    signals = []

    def fake_alive(pid):
        alive["n"] += 1
        return not signals or signals[-1] != 9  # dies only on SIGKILL

    monkeypatch.setattr(ms, "_pid_alive", fake_alive)
    monkeypatch.setattr(ms, "_signal", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(ms, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(ms, "STOP_TERM_SECONDS", 0.05)
    ms.stop(7)
    assert calls[0][0][5] == "display-message" and calls[1][0][5] == "kill-session"
    assert signals == [15, 9]


def test_stop_does_not_signal_a_process_that_exited(calls, monkeypatch):
    signals = []
    monkeypatch.setattr(ms, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ms, "_signal", lambda pid, sig: signals.append(sig))
    ms.stop(7)
    assert signals == []


NEW_TRUST = """❯ Spusť pomocí nástroje Bash příkaz: echo hi
──────────────────────────
 Accessing workspace:
 /mnt/AI/DEV/Kompas
 Quick safety check: Is this a project you created or one you trust? (Like your own code)
 Claude Code'll be able to read, edit, and execute files here.
 {no}No, exit
 {yes}Yes, I trust this folder
 Enter to confirm · Esc to cancel
"""


def _new_trust(selected_yes):
    return NEW_TRUST.format(no="  " if selected_yes else "❯ ", yes="❯ " if selected_yes else "  ")


def test_new_trust_dialog_is_recognised():
    assert ms.needs_trust_confirmation(_new_trust(False))
    # Old "❯ prompt" lines in the scrollback above the dialog don't count.
    assert ms.trust_choice_selected(_new_trust(False)) is False
    assert ms.trust_choice_selected(_new_trust(True)) is True
    assert ms.trust_choice_selected("❯ just a prompt") is None


def test_confirm_moves_to_yes_before_enter(monkeypatch):
    screens = [_new_trust(False), _new_trust(True)]
    sent = []
    monkeypatch.setattr(ms, "capture", lambda qid: screens[0])

    def tmux(*args, input_text=None):
        sent.append(args[-1])
        if args[-1] == "Down":
            screens.pop(0)

    monkeypatch.setattr(ms, "_tmux", tmux)
    assert ms.confirm(19, sleep=lambda s: None) is True
    assert sent == ["Down", "Enter"]


def test_confirm_never_presses_enter_on_no(monkeypatch):
    sent = []
    monkeypatch.setattr(ms, "capture", lambda qid: _new_trust(False))  # cursor never moves
    monkeypatch.setattr(ms, "_tmux", lambda *args, input_text=None: sent.append(args[-1]))
    assert ms.confirm(19, sleep=lambda s: None) is False
    assert "Enter" not in sent


def test_old_trust_dialog_still_confirmed(monkeypatch):
    sent = []
    monkeypatch.setattr(ms, "capture", lambda qid: "Do you trust the files in this folder?\n❯ 1. Yes, proceed\n  2. No")
    monkeypatch.setattr(ms, "_tmux", lambda *args, input_text=None: sent.append(args[-1]))
    assert ms.confirm(19, sleep=lambda s: None) is True
    assert sent == ["Enter"]


def _fake_tmux(monkeypatch, answers=None):
    calls = []

    def fake(*args, input_text=None):
        calls.append(args)
        out = (answers or {}).get(args[0], "")
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(ms, "_tmux", fake)
    return calls


def test_screen_has_the_colours_and_the_cursor(monkeypatch):
    calls = _fake_tmux(monkeypatch, {"capture-pane": "\x1b[32m> hi\x1b[0m\n", "display-message": "120 40 3 7\n"})
    shown = ms.screen(7)
    assert shown == ms.Screen("\x1b[32m> hi\x1b[0m\n", 120, 40, 3, 7)
    assert calls[0] == ("capture-pane", "-p", "-e", "-t", "=q7:")


def test_only_allowed_keys_are_pressed(monkeypatch):
    calls = _fake_tmux(monkeypatch)
    assert ms.press(7, ["Escape", "C-c"]).delivered
    assert calls == [("send-keys", "-t", "=q7:", "Escape", "C-c")]
    assert not ms.press(7, ["Enter", "C-x"]).delivered
    assert not ms.press(7, []).delivered
    assert len(calls) == 1


def test_typed_text_is_literal_short_and_plain(monkeypatch):
    calls = _fake_tmux(monkeypatch)
    assert ms.type_text(7, "y").delivered
    assert calls == [("send-keys", "-t", "=q7:", "-l", "y")]
    for bad in ("", "a\nb", "\x1b[A", "x" * 201):
        assert not ms.type_text(7, bad).delivered
    assert len(calls) == 1


def test_command_resolves_the_mcp_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    q = _queue(delivery_mode=DeliveryMode.INTERACTIVE, launch_flags=["--ccc-mcp="])
    assert ms.build_interactive_command(q, False, ["--mcp-config", "{}"]) == [
        "claude", "--session-id", "sid-1", "--strict-mcp-config", "--mcp-config", "{}",
    ]


def test_target_is_an_exact_match_not_a_prefix():
    """tmux treats a plain `-t q1` as a prefix: q12 would get q1's keys (#259)."""
    assert ms.target(1) == "q1"
    assert ms.exact(1) == "=q1:"
