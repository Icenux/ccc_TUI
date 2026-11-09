"""The overview of a conversation (ccc.overview): now, changes, usage, errors."""
import subprocess
from datetime import date, datetime

import pytest

from ccc import gitops, overview
from ccc.models import PromptRun, QueueStatus, RunStatus
from ccc.storage import Storage
from ccc.transcript import TranscriptEvent


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "ccc.db"))


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        _git(folder, *args)
    (folder / "a.txt").write_text("one\ntwo\n")
    (folder / "b.txt").write_text("keep\n")
    _git(folder, "add", ".")
    _git(folder, "commit", "-qm", "init")
    return folder


def _run(storage, queue, status, error="", at=None):
    now = at or datetime.now()
    return storage.insert_prompt_run(PromptRun(
        id=0, queue_id=queue.id, queue_item_id=None, prompt_text="p", started_at=now, finished_at=now,
        status=status, error_text=error, input_tokens=100, output_tokens=50))


def test_now_says_what_the_conversation_does(storage, tmp_path):
    queue = storage.create_queue("shop", str(tmp_path), "s1")
    event = TranscriptEvent("tool_call", "Edit a.py", datetime(2026, 11, 9, 10, 30))
    now = overview.now_of(storage, queue, event)
    assert now["state"] == "idle" and now["pending"] == 0
    assert now["last_event"] == {"kind": "tool_call", "text": "Edit a.py", "time": "2026-11-09T10:30:00"}
    storage.set_live_busy(queue.id, True)
    assert overview.now_of(storage, storage.get_queue(queue.id), None)["state"] == "working"
    assert overview.now_of(storage, storage.get_queue(queue.id), None)["last_event"] is None


def test_state_prefers_approval_then_activity_then_the_blocking_status(storage, tmp_path):
    queue = storage.create_queue("shop", str(tmp_path), "s1")
    for status, state in ((QueueStatus.RUNNING, "working"), (QueueStatus.WAITING_RETRY, "waiting_limit"),
                          (QueueStatus.PAUSED_LIMIT, "paused_limit"), (QueueStatus.BLOCKED_ERROR, "blocked")):
        queue.status = status
        assert overview.state_of(queue, False) == state
    queue.status = QueueStatus.RUNNING
    assert overview.state_of(queue, True) == "waiting_approval"


def test_changes_list_modified_and_new_files(storage, repo):
    queue = storage.create_queue("repo", str(repo), "s1")
    assert overview.changes_of(queue)["files"] == []
    (repo / "a.txt").write_text("one\nthree\nfour\n")
    (repo / "c.txt").write_text("fresh\n")
    changes = overview.changes_of(queue)
    assert changes["files"] == [
        {"path": "a.txt", "status": "modified", "added": 2, "removed": 1},
        {"path": "c.txt", "status": "new", "added": None, "removed": None},
    ]
    assert changes["added"] == 2 and changes["removed"] == 1 and changes["truncated"] is False


def test_changes_are_not_read_where_claude_may_not_run_commands(storage, repo):
    queue = storage.create_queue("repo", str(repo), "s1", launch_flags=["--permission-mode", "acceptEdits"])
    assert overview.changes_of(queue) is None
    assert gitops.changed_files(str(repo.parent)) is None  # not a git folder


def test_usage_sums_tokens_per_day_for_this_conversation_only(storage, tmp_path):
    queue = storage.create_queue("a", str(tmp_path), "s1")
    other = storage.create_queue("b", str(tmp_path), "s2")
    _run(storage, queue, RunStatus.SUCCESS)
    _run(storage, other, RunStatus.SUCCESS)
    usage = overview.usage_of(storage, queue)
    assert len(usage["days"]) == overview.USAGE_DAYS
    assert usage["days"][-1] == {"day": date.today().isoformat(), "tokens": 150}
    assert usage["today"] == 150 and usage["total"] == 150
    assert usage["models"] == [{"model": "unknown", "tokens": 150}]


def test_errors_list_the_latest_failures_newest_first(storage, tmp_path):
    queue = storage.create_queue("a", str(tmp_path), "s1")
    assert overview.errors_of(storage, queue)["recent"] == []
    _run(storage, queue, RunStatus.SUCCESS)
    _run(storage, queue, RunStatus.ERROR, "first", datetime(2026, 11, 1, 9, 0))
    _run(storage, queue, RunStatus.RATE_LIMITED, "second", datetime(2026, 11, 2, 9, 0))
    errors = overview.errors_of(storage, queue, datetime(2026, 11, 3))
    assert [(e["status"], e["text"]) for e in errors["recent"]] == [("rate_limited", "second"), ("error", "first")]
    assert errors["count"] == 2 and errors["current"] is None


def test_transcript_tail_reads_only_what_was_appended(tmp_path, monkeypatch):
    import json

    path = tmp_path / "s.jsonl"

    def line(text, minute):
        return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}, "timestamp": f"2026-11-09T10:{minute:02d}:00.000Z"}) + "\n"

    path.write_text(line("first", 1))
    monkeypatch.setattr("ccc.transcript.find_transcript_path", lambda session: path)
    tail = overview.TranscriptTail()
    assert tail.last("s").text == "first"
    with path.open("a") as f:
        f.write(line("second", 2))
    assert tail.last("s").text == "second"
    assert tail.last("s").text == "second"
    monkeypatch.setattr("ccc.transcript.find_transcript_path", lambda session: None)
    assert overview.TranscriptTail().last("none") is None
