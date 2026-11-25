"""Runs of Claude Code subagents read from the transcripts (ccc.subagent_runs, #286)."""
import json
import os
import time

import pytest

from ccc import subagent_runs as sr

SESSION = "11111111-2222-3333-4444-555555555555"


def line(**data):
    return json.dumps(data) + "\n"


def tool_use(id_, name, at, **inp):
    return line(type="assistant", timestamp=at, message={"id": "m" + id_, "role": "assistant", "model": "claude-sonnet-5-5",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [{"type": "tool_use", "id": id_, "name": name, "input": inp}]})


def tool_result(id_, at, text="ok", error=False, **extra):
    return line(type="user", timestamp=at, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": id_, "content": text, "is_error": error}]}, **extra)


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    project = root / "-work-shop"
    subs = project / SESSION / "subagents"
    subs.mkdir(parents=True)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", root)
    monkeypatch.setattr("ccc.transcript.profiles.list_profiles", lambda: [])
    main = project / f"{SESSION}.jsonl"

    def agent(agent_id, tool_use_id, description, lines, finished=True, depth=1):
        (subs / f"agent-{agent_id}.jsonl").write_text("".join(lines), encoding="utf-8")
        (subs / f"agent-{agent_id}.meta.json").write_text(json.dumps({
            "agentType": "Explore", "description": description, "toolUseId": tool_use_id, "finished": finished,
            "spawnDepth": depth, "requestShape": "background"}), encoding="utf-8")

    return type("S", (), {"main": main, "agent": staticmethod(agent), "subs": subs})


def test_a_session_without_a_transcript_has_no_runs(session):
    assert sr.read_runs("00000000-0000-0000-0000-000000000000") == []


def test_a_finished_run_has_prompt_steps_result_and_tokens(session):
    session.main.write_text(
        tool_use("toolu_A", "Agent", "2026-11-01T10:00:00Z", description="Find usages", prompt="Look for foo",
                 subagent_type="Explore", run_in_background=True)
        + tool_result("toolu_A", "2026-11-01T10:00:01Z", toolUseResult={"status": "async_launched", "agentId": "aaa"})
        + line(type="queue-operation", timestamp="2026-11-01T10:01:00Z", content=(
            "<task-notification><task-id>aaa</task-id><status>completed</status><result>Found 3.</result>"
            "<usage><subagent_tokens>1234</subagent_tokens><tool_uses>2</tool_uses><duration_ms>50000</duration_ms>"
            "</usage></task-notification>")), encoding="utf-8")
    session.agent("aaa", "toolu_A", "Find usages", [
        line(type="user", timestamp="2026-11-01T10:00:02Z", message={"role": "user", "content": "Look for foo"}),
        tool_use("t1", "Grep", "2026-11-01T10:00:03Z", pattern="foo"),
        tool_result("t1", "2026-11-01T10:00:05Z", "a.py:1"),
        tool_use("t2", "Bash", "2026-11-01T10:00:06Z", command="pytest -q"),
        tool_result("t2", "2026-11-01T10:00:16Z", "Exit code 1\nboom", error=True),
        line(type="assistant", timestamp="2026-11-01T10:00:20Z", message={"id": "end", "role": "assistant",
             "content": [{"type": "text", "text": "Found 3 places."}], "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    [run] = sr.read_runs(SESSION)
    assert (run.id, run.parent_id, run.kind, run.description) == ("aaa", "", "Explore", "Find usages")
    assert run.status == sr.DONE and run.prompt == "Look for foo" and run.result == "Found 3."
    assert run.tokens == 1234 and run.tool_count == 2 and run.background
    assert run.duration_ms == 18000  # first to last line of its own transcript
    grep, bash = run.steps
    assert (grep.tool, grep.arg, grep.offset_ms, grep.duration_ms, grep.error) == ("Grep", "foo", 1000, 2000, "")
    assert (bash.tool, bash.arg, bash.duration_ms, bash.error) == ("Bash", "pytest -q", 10000, "Exit code 1")
    assert run.as_dict()["status"] == "done" and "steps" not in run.as_dict()
    assert len(run.as_dict(detail=True)["steps"]) == 2


def test_a_run_started_by_a_subagent_hangs_under_its_parent(session):
    session.main.write_text(tool_use("toolu_A", "Agent", "2026-11-01T10:00:00Z", description="outer", prompt="p"),
                            encoding="utf-8")
    session.agent("aaa", "toolu_A", "outer", [
        line(type="user", timestamp="2026-11-01T10:00:01Z", message={"role": "user", "content": "p"}),
        tool_use("toolu_B", "Agent", "2026-11-01T10:00:02Z", description="inner", prompt="q"),
        tool_result("toolu_B", "2026-11-01T10:00:09Z", toolUseResult={"status": "completed", "agentId": "bbb"}),
    ])
    session.agent("bbb", "toolu_B", "inner", [
        line(type="user", timestamp="2026-11-01T10:00:03Z", message={"role": "user", "content": "q"}),
        line(type="assistant", timestamp="2026-11-01T10:00:08Z", message={"id": "x", "role": "assistant",
             "content": [{"type": "text", "text": "done"}]}),
    ], depth=2)
    outer, inner = sr.read_runs(SESSION)
    assert (outer.parent_id, outer.depth) == ("", 1)
    assert (inner.parent_id, inner.depth) == ("aaa", 2)
    assert outer.steps[0].agent_id == "bbb"  # the `Agent` step leads to the nested run


def test_an_unfinished_run_runs_until_it_has_been_silent_too_long(session):
    session.main.write_text(tool_use("toolu_A", "Agent", "2026-11-01T10:00:00Z", description="d", prompt="p"),
                            encoding="utf-8")
    session.agent("aaa", "toolu_A", "d", [line(type="user", timestamp="2026-11-01T10:00:01Z",
                                               message={"role": "user", "content": "p"})], finished=False)
    file = session.subs / "agent-aaa.jsonl"
    assert sr.read_runs(SESSION)[0].status == sr.RUNNING
    old = time.time() - sr.STALE_SECONDS - 60
    os.utime(file, (old, old))
    assert sr.read_runs(SESSION)[0].status == sr.STOPPED


def test_a_failed_notification_marks_the_run_as_an_error(session):
    session.main.write_text(
        tool_use("toolu_A", "Agent", "2026-11-01T10:00:00Z", description="d", prompt="p")
        + line(type="queue-operation", timestamp="2026-11-01T10:01:00Z",
               content="<task-notification><task-id>aaa</task-id><status>failed</status><result>x</result>"
                       "</task-notification>"), encoding="utf-8")
    session.agent("aaa", "toolu_A", "d", [line(type="user", timestamp="2026-11-01T10:00:01Z",
                                               message={"role": "user", "content": "p"})])
    assert sr.find_run(SESSION, "aaa").status == sr.ERROR
    assert sr.find_run(SESSION, "nope") is None


# --- over the API ---------------------------------------------------------------------------


def test_the_api_lists_runs_with_a_summary_and_gives_one_in_detail(session, tmp_path):
    from ccc import api
    from ccc.storage import Storage

    storage = Storage(str(tmp_path / "ccc.db"))
    queue = storage.create_queue("shop", str(tmp_path), SESSION)
    session.main.write_text(tool_use("toolu_A", "Agent", "2026-11-01T10:00:00Z", description="d", prompt="p"),
                            encoding="utf-8")
    session.agent("aaa", "toolu_A", "d", [
        line(type="user", timestamp="2026-11-01T10:00:01Z", message={"role": "user", "content": "p"}),
        tool_use("t1", "Read", "2026-11-01T10:00:02Z", file_path="/x"),
        tool_result("t1", "2026-11-01T10:00:03Z"),
    ])
    server = api.Api(storage)
    status, listed = server.route("GET", f"/api/v1/conversations/{queue.id}/subagents", {}, {})
    assert status == 200 and listed["summary"]["total"] == 1 and listed["summary"]["running"] == 0
    assert listed["runs"][0]["id"] == "aaa" and "steps" not in listed["runs"][0]
    status, one = server.route("GET", f"/api/v1/conversations/{queue.id}/subagents/aaa", {}, {})
    assert one["prompt"] == "p" and one["steps"][0]["tool"] == "Read"
    with pytest.raises(api.ApiError) as missing:
        server.route("GET", f"/api/v1/conversations/{queue.id}/subagents/zzz", {}, {})
    assert missing.value.status == 404
