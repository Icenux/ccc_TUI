import json
from unittest.mock import MagicMock, patch

from ccc.session_discovery import (
    DiscoveredSession,
    discover_sessions,
    find_tmux_pane_for_pid,
    session_ready_for_input,
)

REAL_SAMPLE_OUTPUT = json.dumps(
    [
        {
            "pid": 1280934,
            "id": "d61c8ea6",
            "cwd": "/home/lukas",
            "kind": "background",
            "startedAt": 1788377182066,
            "sessionId": "e1a59594-2c12-4488-9555-98331a713a24",
            "name": "DEV gallery projekt načtení",
            "status": "idle",
            "state": "blocked",
        },
        {
            "pid": 1611743,
            "cwd": "/home/lukas/DEV",
            "kind": "interactive",
            "startedAt": 1788405769995,
            "sessionId": "a65e24bb-eda1-4294-8ad0-b39f3cf8891d",
            "name": "dev-75",
            "status": "busy",
        },
        {"pid": 123, "cwd": "/tmp", "kind": "interactive", "name": "no-session-id"},
    ]
)


def test_discover_sessions_parses_real_shaped_output():
    fake_result = MagicMock(returncode=0, stdout=REAL_SAMPLE_OUTPUT)
    with patch("subprocess.run", return_value=fake_result):
        sessions = discover_sessions()

    assert len(sessions) == 2  # the malformed third entry (no sessionId) is skipped
    assert sessions[0].session_id == "e1a59594-2c12-4488-9555-98331a713a24"
    assert sessions[0].name == "DEV gallery projekt načtení"
    assert sessions[0].kind == "background"
    assert sessions[1].session_id == "a65e24bb-eda1-4294-8ad0-b39f3cf8891d"
    assert sessions[1].cwd == "/home/lukas/DEV"
    assert sessions[1].status == "busy"
    assert sessions[0].pid == 1280934
    assert sessions[0].state == "blocked"
    assert sessions[1].pid == 1611743
    assert sessions[1].state is None


def test_discover_sessions_empty_on_nonzero_exit():
    fake_result = MagicMock(returncode=1, stdout="")
    with patch("subprocess.run", return_value=fake_result):
        assert discover_sessions() == []


def test_discover_sessions_empty_on_garbage_output():
    fake_result = MagicMock(returncode=0, stdout="not json")
    with patch("subprocess.run", return_value=fake_result):
        assert discover_sessions() == []


def test_discover_sessions_empty_when_claude_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert discover_sessions() == []


def _session(status="idle", state=None):
    return DiscoveredSession(
        session_id="s1", name="n", cwd="/tmp", kind="interactive", status=status, pid=1, state=state
    )


def test_session_ready_for_input_true_when_idle_and_not_blocked():
    assert session_ready_for_input(_session(status="idle", state=None)) is True


def test_session_ready_for_input_false_when_busy():
    assert session_ready_for_input(_session(status="busy", state=None)) is False


def test_session_ready_for_input_false_when_blocked_even_if_status_idle():
    assert session_ready_for_input(_session(status="idle", state="blocked")) is False


def test_find_tmux_pane_for_pid_direct_match():
    # tmux launched claude directly as the pane's command — pane_pid IS
    # the target pid, no ancestry walk needed. (Confirmed live: `tmux
    # new-session ... claude` produces exactly this shape.)
    panes_result = MagicMock(
        returncode=0,
        stdout="1:0.0 1280281\ncc-live-test:0.0 2485968\n",
    )
    with patch("subprocess.run", return_value=panes_result):
        assert find_tmux_pane_for_pid(2485968) == "cc-live-test:0.0"


def test_find_tmux_pane_for_pid_matches_via_parent_shell():
    # claude was typed by hand into an already-running shell pane —
    # pane_pid is claude's PARENT. (Confirmed live: tmux session "1",
    # where panes run bash and claude was launched manually inside them.)
    panes_result = MagicMock(
        returncode=0,
        stdout="1:0.0 1280281\n1:0.1 1381393\n1:0.2 1282101\n1:0.3 1289216\n",
    )
    ppid_result = MagicMock(returncode=0, stdout="1282101\n")
    with patch("subprocess.run", side_effect=[panes_result, ppid_result]):
        assert find_tmux_pane_for_pid(1611743) == "1:0.2"


def test_find_tmux_pane_for_pid_no_match_returns_none():
    panes_result = MagicMock(returncode=0, stdout="1:0.0 1280281\n")
    ppid_result = MagicMock(returncode=0, stdout="1\n")  # walks up to init, stops
    with patch("subprocess.run", side_effect=[panes_result, ppid_result]):
        assert find_tmux_pane_for_pid(1611743) is None


def test_find_tmux_pane_for_pid_process_gone_returns_none():
    panes_result = MagicMock(returncode=0, stdout="1:0.0 1280281\n")
    ppid_result = MagicMock(returncode=1, stdout="")
    with patch("subprocess.run", side_effect=[panes_result, ppid_result]):
        assert find_tmux_pane_for_pid(999999) is None


def test_session_with_only_a_background_shell_is_not_busy(tmp_path, monkeypatch):
    """`claude agents --json` says "busy" for a session that only has a background shell running;
    its own file says "shell" — that is not work."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "11.json").write_text(json.dumps({"pid": 11, "status": "shell"}))
    (tmp_path / "sessions" / "12.json").write_text(json.dumps({"pid": 12, "status": "busy"}))
    entries = [
        {"pid": pid, "cwd": "/tmp", "kind": "interactive", "sessionId": f"s{pid}", "name": f"n{pid}", "status": "busy"}
        for pid in (11, 12, 13)  # 13 has no file at all
    ]
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(entries))):
        statuses = {s.pid: s.status for s in discover_sessions()}

    assert statuses == {11: "idle", 12: "busy", 13: "busy"}


def _transcript(tmp_path, monkeypatch, session_id, entries):
    import ccc.transcript

    monkeypatch.setattr(ccc.transcript, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr("ccc.profiles.list_profiles", lambda: [])
    folder = tmp_path / "projects" / "-tmp-proj"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


PROMPT = {"type": "user", "message": {"role": "user", "content": "do it"}}
TOOL_CALL = {"type": "assistant", "message": {"content": [{"type": "tool_use"}], "stop_reason": "tool_use"}}
TOOL_RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
REPLY = {"type": "assistant", "message": {"content": [{"type": "text"}], "stop_reason": "end_turn"}}
TURN_END = {"type": "system", "subtype": "turn_duration"}


def test_turn_finished_follows_claude_codes_end_of_turn_mark(tmp_path, monkeypatch):
    from ccc.session_discovery import turn_finished

    assert not turn_finished("nobody")  # no transcript: can't tell, so not finished
    path = _transcript(tmp_path, monkeypatch, "s1", [PROMPT, TOOL_CALL, TOOL_RESULT])
    assert not turn_finished("s1")  # mid-turn
    with path.open("a") as f:
        f.write(json.dumps(REPLY) + "\n" + json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
    assert not turn_finished("s1")  # the reply, but no end of turn yet
    with path.open("a") as f:
        f.write(json.dumps(TURN_END) + "\n")
    assert turn_finished("s1")
    with path.open("a") as f:  # a /command typed meanwhile starts no turn
        f.write(json.dumps({"type": "user", "message": {"content": "<command-name>/model</command-name>"}}) + "\n")
        f.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": "caveat"}}) + "\n")
        f.write('{"type": "assist')  # a line still being written
    assert turn_finished("s1")
    with path.open("a") as f:
        f.write("\n" + json.dumps(PROMPT) + "\n")
    assert not turn_finished("s1")  # a new prompt: working again


def test_busy_only_because_of_a_background_task_takes_a_message(tmp_path, monkeypatch):
    """Claude Code says "busy" while anything of it runs in the background (here a background
    shell that never ends). Its turn has ended, so ccc must hand it the queued message — it used to
    wait for "idle" forever."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _transcript(tmp_path, monkeypatch, "done", [PROMPT, REPLY, TURN_END])
    _transcript(tmp_path, monkeypatch, "working", [PROMPT, TOOL_CALL])
    entries = [
        {"pid": pid, "cwd": "/tmp", "kind": "interactive", "sessionId": sid, "name": sid, "status": "busy"}
        for pid, sid in ((21, "done"), (22, "working"))
    ]
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(entries))):
        statuses = {s.session_id: s.status for s in discover_sessions()}
    assert statuses == {"done": "idle", "working": "busy"}
