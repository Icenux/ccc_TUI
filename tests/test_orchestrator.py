import asyncio

import pytest

from ccc.models import ItemStatus, QueueStatus, RunStatus
from ccc.orchestrator import Orchestrator, enqueue_prompt
from ccc.runner import RunResult
from ccc.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return str(d)


async def test_add_prompt_runs_and_marks_done(storage, project_dir):
    calls = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls.append((prompt, resume))
        return RunResult(status=RunStatus.SUCCESS, result_text="done", cost_usd=0.01)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "first prompt")

    for _ in range(50):
        await asyncio.sleep(0.01)
        queue = storage.get_queue_by_path(project_dir)
        if queue.status == QueueStatus.IDLE and calls:
            break

    await orch.stop_all()

    assert calls == [("first prompt", False)]
    runs = storage.runs_for_queue(storage.get_queue_by_path(project_dir).id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCESS


async def test_persists_per_model_usage(storage, project_dir):
    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(
            status=RunStatus.SUCCESS,
            model_usage={"claude-sonnet-5": {"input_tokens": 2, "output_tokens": 4}},
        )

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "first prompt")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if storage.total_tokens_by_model():
            break

    await orch.stop_all()

    assert storage.total_tokens_by_model() == [("claude-sonnet-5", 6)]


async def test_second_prompt_resumes_session(storage, project_dir):
    calls = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls.append(resume)
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "one")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(calls) >= 1:
            break

    orch.add_prompt(project_dir, "two")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(calls) >= 2:
            break

    await orch.stop_all()

    assert calls == [False, True]


async def test_hard_error_blocks_queue(storage, project_dir):
    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.ERROR, error_text="tool crashed")

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "bad prompt")

    for _ in range(50):
        await asyncio.sleep(0.01)
        queue = storage.get_queue_by_path(project_dir)
        if queue.status == QueueStatus.BLOCKED_ERROR:
            break

    await orch.stop_all()

    queue = storage.get_queue_by_path(project_dir)
    assert queue.status == QueueStatus.BLOCKED_ERROR
    assert queue.last_error == "tool crashed"
    run = storage.runs_for_queue(queue.id)[0]
    assert run.status == RunStatus.ERROR


async def test_rate_limited_reschedules_with_short_backoff(storage, project_dir, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.next_backoff_seconds", lambda prev: 0)
    call_count = {"n": 0}

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return RunResult(status=RunStatus.RATE_LIMITED, error_text="usage limit reached")
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "retry me")

    for _ in range(100):
        await asyncio.sleep(0.01)
        if call_count["n"] >= 2:
            break

    await orch.stop_all()

    assert call_count["n"] == 2
    runs = storage.runs_for_queue(storage.get_queue_by_path(project_dir).id)
    statuses = sorted(r.status for r in runs)
    assert RunStatus.RATE_LIMITED in statuses
    assert RunStatus.SUCCESS in statuses


async def test_unhandled_exception_is_caught_and_blocks_queue_visibly(storage, project_dir):
    """A run_fn that raises (e.g. a bad cwd raising FileNotFoundError)
    must never leave the queue silently stuck at 'running' forever."""

    async def raising_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'nope'")

    orch = Orchestrator(storage, run_fn=raising_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "boom")

    for _ in range(50):
        await asyncio.sleep(0.01)
        queue = storage.get_queue_by_path(project_dir)
        if queue.status == QueueStatus.BLOCKED_ERROR:
            break

    await orch.stop_all()

    queue = storage.get_queue_by_path(project_dir)
    assert queue.status == QueueStatus.BLOCKED_ERROR
    assert "FileNotFoundError" in queue.last_error
    runs = storage.runs_for_queue(queue.id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.ERROR
    assert "FileNotFoundError" in runs[0].error_text


async def test_attach_to_session_creates_live_terminal_queue(storage, project_dir):
    """attach_to_session() is for a session found via discovery — it must
    default to live_terminal delivery (typing into that session's own
    tmux pane) rather than the headless resume path, since that's the
    whole point of attaching to something already live."""
    from ccc.models import DeliveryMode
    from ccc.orchestrator import attach_to_session

    attach_to_session(storage, "live-session-id", project_dir, "dev-75", "ahoj")

    queue = storage.get_queue_by_session_id("live-session-id")
    assert queue.delivery_mode == DeliveryMode.LIVE_TERMINAL
    assert queue.always_resume is True


async def test_live_terminal_item_waits_until_session_ready_then_delivers(storage, project_dir, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)

    from ccc.live_delivery import DeliveryResult
    from ccc.orchestrator import attach_to_session
    from ccc.session_discovery import DiscoveredSession

    attach_to_session(storage, "live-session-id", project_dir, "dev-75", "ahoj")

    call_count = {"n": 0}

    def fake_discover_sessions():
        call_count["n"] += 1
        # busy on the first two polls, then idle and ready
        status = "busy" if call_count["n"] < 3 else "idle"
        return [DiscoveredSession(session_id="live-session-id", name="dev-75", cwd=project_dir, kind="interactive", status=status)]

    delivered = []

    def fake_deliver(session_id, prompt_text):
        delivered.append((session_id, prompt_text))
        return DeliveryResult(delivered=True)

    monkeypatch.setattr("ccc.orchestrator.discover_sessions", fake_discover_sessions)
    monkeypatch.setattr("ccc.orchestrator.deliver_to_live_session", fake_deliver)

    orch = Orchestrator(storage)
    await orch.start_all()

    for _ in range(100):
        await asyncio.sleep(0.01)
        if delivered:
            break

    await orch.stop_all()

    assert delivered == [("live-session-id", "ahoj")]
    assert call_count["n"] >= 3  # actually waited through the busy polls

    queue = storage.get_queue_by_session_id("live-session-id")
    assert queue.status == QueueStatus.IDLE
    runs = storage.runs_for_queue(queue.id)
    assert runs[0].status == RunStatus.SUCCESS


async def test_live_terminal_item_fails_when_session_gone(storage, project_dir, monkeypatch):
    from ccc.orchestrator import attach_to_session

    attach_to_session(storage, "live-session-id", project_dir, "dev-75", "ahoj")

    monkeypatch.setattr("ccc.orchestrator.discover_sessions", lambda: [])

    orch = Orchestrator(storage)
    await orch.start_all()

    for _ in range(50):
        await asyncio.sleep(0.01)
        queue = storage.get_queue_by_session_id("live-session-id")
        if queue.status == QueueStatus.BLOCKED_ERROR:
            break

    await orch.stop_all()

    queue = storage.get_queue_by_session_id("live-session-id")
    assert queue.status == QueueStatus.BLOCKED_ERROR
    assert "no longer running" in queue.last_error


async def test_discovers_queue_created_after_start_all(storage, tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)
    calls = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()  # no queues exist yet

    # simulate another process (e.g. `ccc add`) creating a queue
    # directly via storage, the way enqueue_prompt() does, without going
    # through this orchestrator at all
    late_dir = tmp_path / "late-proj"
    late_dir.mkdir()
    enqueue_prompt(storage, str(late_dir), "discovered later")

    for _ in range(100):
        await asyncio.sleep(0.01)
        if calls:
            break

    await orch.stop_all()

    assert calls == ["discovered later"]


async def test_two_orchestrators_never_double_execute_same_item(tmp_path):
    """Simulates the daemon + a second process (e.g. someone else's
    Orchestrator against the same DB file) racing for the same queue."""
    db_path = tmp_path / "shared.db"
    storage_a = Storage(db_path)
    storage_b = Storage(db_path)
    project_dir = tmp_path / "shared-proj"
    project_dir.mkdir()

    calls_a = []
    calls_b = []

    async def fake_run_a(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls_a.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    async def fake_run_b(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls_b.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    queue = storage_a.create_queue("shared-proj", str(project_dir), "sess-1")
    storage_a.add_queue_item(queue.id, "only prompt")

    orch_a = Orchestrator(storage_a, run_fn=fake_run_a)
    orch_b = Orchestrator(storage_b, run_fn=fake_run_b)
    await orch_a.start_all()
    await orch_b.start_all()

    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls_a or calls_b:
            break

    await orch_a.stop_all()
    await orch_b.stop_all()

    assert calls_a + calls_b == ["only prompt"]
    runs = storage_a.runs_for_queue(queue.id)
    assert len(runs) == 1


async def test_crash_recovery_requeues_running_items(storage, project_dir):
    queue = storage.create_queue("proj", project_dir, "sess-1")
    storage.add_queue_item(queue.id, "interrupted prompt")
    item = storage.next_pending_item(queue.id)
    storage.set_item_status(item.id, ItemStatus.RUNNING)
    storage.update_queue_status(queue.id, QueueStatus.RUNNING)

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()

    for _ in range(50):
        await asyncio.sleep(0.01)
        reloaded = storage.get_queue_by_path(project_dir)
        if reloaded.status == QueueStatus.IDLE:
            break

    await orch.stop_all()

    runs = storage.runs_for_queue(queue.id)
    assert any(r.prompt_text == "interrupted prompt" and r.status == RunStatus.SUCCESS for r in runs)


from ccc.launch_options import DEFAULT_SELECTION, FORK, SESSION_CONTINUE, SESSION_FROM_PR, SESSION_RESUME, toggle
from ccc.orchestrator import start_conversation


def test_start_new_conversation_creates_fresh_queue_per_launch(storage, project_dir):
    a = start_conversation(storage, str(project_dir), toggle(DEFAULT_SELECTION, "model_haiku", True), interactive=False)
    b = start_conversation(storage, str(project_dir), DEFAULT_SELECTION, name="second", interactive=False)
    assert a.id != b.id and a.session_id != b.session_id
    assert a.launch_flags == ["--dangerously-skip-permissions", "--model", "haiku"]
    assert not a.always_resume and a.first_run_flags is None and b.name == "second"


def test_start_rejects_missing_directory(storage, tmp_path):
    with pytest.raises(ValueError):
        start_conversation(storage, str(tmp_path / "nope"), DEFAULT_SELECTION, interactive=False)


def _recorded(monkeypatch, *ids):
    """Claude Code has recorded these sessions in the folder (a session is resumed only from there, #160)."""
    from ccc.transcript import SessionSummary
    from datetime import datetime
    monkeypatch.setattr("ccc.orchestrator.list_sessions_for_directory",
                        lambda path: [SessionSummary(i, datetime.now(), "hi", 2) for i in ids])
    monkeypatch.setattr("ccc.orchestrator.session_recorded_in", lambda session_id, path: session_id in ids)


def test_resume_reuses_existing_queue_unless_fork(storage, project_dir, monkeypatch):
    _recorded(monkeypatch, "abc")
    sel = toggle(DEFAULT_SELECTION, SESSION_RESUME, True)
    first = start_conversation(storage, str(project_dir), sel, session_id="abc", interactive=False)
    assert first.always_resume and first.session_id == "abc"
    assert start_conversation(storage, str(project_dir), sel, session_id="abc", interactive=False).id == first.id
    fork = start_conversation(storage, str(project_dir), toggle(sel, FORK, True), session_id="abc", interactive=False)
    assert fork.id != first.id and fork.first_run_flags == ["--resume", "abc", "--fork-session"]


def test_continue_picks_latest_session_of_directory(storage, project_dir, monkeypatch):
    from ccc.transcript import SessionSummary
    from datetime import datetime
    monkeypatch.setattr("ccc.orchestrator.list_sessions_for_directory",
                        lambda path: [SessionSummary("newest", datetime.now(), "hi", 2)])
    monkeypatch.setattr("ccc.orchestrator.session_recorded_in", lambda session_id, path: True)
    q = start_conversation(storage, str(project_dir), toggle(DEFAULT_SELECTION, SESSION_CONTINUE, True), interactive=False)
    assert q.session_id == "newest"
    monkeypatch.setattr("ccc.orchestrator.list_sessions_for_directory", lambda path: [])
    with pytest.raises(ValueError):
        start_conversation(storage, str(project_dir), toggle(DEFAULT_SELECTION, SESSION_CONTINUE, True), name="x", interactive=False)


def test_from_pr_needs_value(storage, project_dir):
    sel = toggle(DEFAULT_SELECTION, SESSION_FROM_PR, True)
    with pytest.raises(ValueError):
        start_conversation(storage, str(project_dir), sel, pr="  ", interactive=False)
    q = start_conversation(storage, str(project_dir), sel, pr="42", interactive=False)
    assert q.first_run_flags == ["--from-pr", "42"]


async def test_first_run_adopts_returned_session_id(storage, project_dir, monkeypatch):
    _recorded(monkeypatch, "orig")
    q = start_conversation(storage, str(project_dir),
                           toggle(toggle(DEFAULT_SELECTION, SESSION_RESUME, True), FORK, True), session_id="orig", interactive=False)
    storage.add_queue_item(q.id, "hello")
    calls = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls.append((session_id, first_run_flags, launch_flags))
        return RunResult(status=RunStatus.SUCCESS, session_id="forked-id")

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    await asyncio.sleep(0.3)
    await orch.stop_all()
    assert calls[0][1] == ["--resume", "orig", "--fork-session"]
    q = storage.get_queue(q.id)
    assert q.session_id == "forked-id" and q.first_run_flags is None and q.always_resume


# --- auto-continue / usage-based limits ------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from ccc.orchestrator import DEFAULT_CONTINUE_TEXT  # noqa: E402
from ccc.usage_api import ProUsage, WindowUsage  # noqa: E402


def _five_hour_usage(pct, reset_local):
    return ProUsage(
        five_hour=WindowUsage(pct, reset_local.astimezone().astimezone(timezone.utc)), seven_day=None, per_model=[]
    )


async def _until(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr("ccc.orchestrator.next_backoff_seconds", lambda prev: 0)


def _limited_then_ok(sent):
    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        if len(sent) == 1:
            return RunResult(status=RunStatus.RATE_LIMITED, error_text="usage limit reached")
        return RunResult(status=RunStatus.SUCCESS)

    return fake_run


async def test_auto_continue_sends_continue_text_when_prompt_reached_conversation(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: "do work")
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    assert await _until(lambda: len(sent) >= 2)
    await orch.stop_all()
    assert sent == ["do work", DEFAULT_CONTINUE_TEXT]
    queue = storage.get_queue_by_path(project_dir)
    assert sorted((r.id, r.prompt_text) for r in storage.runs_for_queue(queue.id))[-1][1] == DEFAULT_CONTINUE_TEXT


async def test_prompt_not_in_transcript_is_resent(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: None)
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    assert await _until(lambda: len(sent) >= 2)
    await orch.stop_all()
    assert sent == ["do work", "do work"]


async def test_custom_continue_text_from_settings(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: "do work")
    storage.set_setting("continue_text", "dál")
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    assert await _until(lambda: len(sent) >= 2)
    await orch.stop_all()
    assert sent[1] == "dál"


async def test_auto_continue_off_pauses_until_user_resumes(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: None)
    queue = enqueue_prompt(storage, project_dir, "do work")
    storage.set_queue_auto_continue(queue.id, False)
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    assert await _until(lambda: storage.get_queue(queue.id).status == QueueStatus.PAUSED_LIMIT)
    await asyncio.sleep(0.2)
    assert len(sent) == 1
    storage.update_queue_status(queue.id, QueueStatus.IDLE)  # what `r` in the TUI does
    assert await _until(lambda: len(sent) >= 2)
    await orch.stop_all()


async def test_rate_limit_waits_for_reset_reported_by_usage(storage, project_dir, fast_poll, monkeypatch):
    reset = datetime.now().replace(microsecond=0) + timedelta(hours=1)
    monkeypatch.setattr("ccc.orchestrator.get_pro_usage", lambda force_refresh=False: _five_hour_usage(99, reset))
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: None)
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    queue_id = storage.get_queue_by_path(project_dir).id
    assert await _until(lambda: storage.get_queue(queue_id).status == QueueStatus.WAITING_RETRY)
    await asyncio.sleep(0.2)
    await orch.stop_all()
    assert len(sent) == 1
    assert storage.get_queue(queue_id).next_retry_at == reset + timedelta(seconds=30)


async def test_exhausted_usage_holds_queue_without_running_claude(storage, project_dir, fast_poll, monkeypatch):
    reset = datetime.now().replace(microsecond=0) + timedelta(hours=2)
    monkeypatch.setattr("ccc.orchestrator.get_pro_usage", lambda force_refresh=False: _five_hour_usage(100, reset))
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent))
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    queue_id = storage.get_queue_by_path(project_dir).id
    assert await _until(lambda: storage.get_queue(queue_id).status == QueueStatus.WAITING_RETRY)
    await asyncio.sleep(0.2)
    await orch.stop_all()
    assert sent == []
    assert storage.get_queue(queue_id).next_retry_at == reset + timedelta(seconds=30)


async def test_paused_queue_starts_nothing_until_unpaused(storage, project_dir, fast_poll):
    queue = enqueue_prompt(storage, project_dir, "do work")
    storage.set_queue_paused(queue.id, True)
    sent = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    await asyncio.sleep(0.2)
    assert sent == []
    storage.set_queue_paused(queue.id, False)
    assert await _until(lambda: sent == ["do work"])
    await orch.stop_all()


async def test_restart_while_waiting_does_not_retry_early(storage, project_dir, fast_poll):
    queue = enqueue_prompt(storage, project_dir, "do work")
    storage.update_queue_status(queue.id, QueueStatus.WAITING_RETRY, next_retry_at=datetime.now() + timedelta(hours=1))
    sent = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    await asyncio.sleep(0.2)
    await orch.stop_all()
    assert sent == []
    assert storage.get_queue(queue.id).status == QueueStatus.WAITING_RETRY


async def test_deleted_queue_stops_its_runtime_quietly(storage, project_dir, fast_poll, caplog):
    queue = enqueue_prompt(storage, project_dir, "never run")
    storage.set_queue_paused(queue.id, True)
    orch = Orchestrator(storage, run_fn=_limited_then_ok([]))
    await orch.start_all()
    await asyncio.sleep(0.05)
    storage.delete_queue(queue.id)
    runtime = orch.runtimes[queue.id]
    assert await _until(lambda: runtime._task.done())
    await asyncio.sleep(0.1)  # discovery loop must not restart it
    await orch.stop_all()
    assert runtime._task.exception() is None
    assert "crashed" not in caplog.text


# --- notifications ----------------------------------------------------------


class FakeNotifier:
    def __init__(self, started=False):
        self.texts = []
        self.started = started  # "a conversation started" is a category of its own (#146)

    def configured(self, category=None):
        return category != "started" or self.started

    def notify(self, text, category=None):
        self.texts.append(text)


async def test_tells_when_a_conversation_starts_after_a_quiet_time(storage, project_dir, fast_poll):
    notifier = FakeNotifier(started=True)

    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.SUCCESS, result_text="")

    queue = enqueue_prompt(storage, project_dir, "write the tests")
    storage.add_queue_item(queue.id, "and run them")
    orch = Orchestrator(storage, run_fn=ok, notifier=notifier)
    await orch.start_all()
    assert await _until(lambda: len(notifier.texts) >= 2)
    await asyncio.sleep(0.1)
    await orch.stop_all()
    started = [t for t in notifier.texts if "starts working" in t]
    assert len(started) == 1 and "write the tests" in started[0]  # the second task follows at once: no second notice


async def test_notifies_queue_finished_with_reply(storage, project_dir, fast_poll):
    notifier = FakeNotifier()

    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.SUCCESS, result_text="All done: 3 files changed.")

    queue = enqueue_prompt(storage, project_dir, "one")
    storage.add_queue_item(queue.id, "two")
    orch = Orchestrator(storage, run_fn=ok, notifier=notifier)
    await orch.start_all()
    assert await _until(lambda: notifier.texts)
    await asyncio.sleep(0.1)
    await orch.stop_all()
    assert len(notifier.texts) == 1  # only when the queue is empty, not per task
    assert "finished" in notifier.texts[0] and queue.name in notifier.texts[0]
    assert "All done: 3 files changed." in notifier.texts[0]


async def test_notifies_error(storage, project_dir, fast_poll):
    notifier = FakeNotifier()

    async def fail(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.ERROR, error_text="tool exploded")

    enqueue_prompt(storage, project_dir, "one")
    orch = Orchestrator(storage, run_fn=fail, notifier=notifier)
    await orch.start_all()
    assert await _until(lambda: notifier.texts)
    await orch.stop_all()
    assert "error" in notifier.texts[0].lower() and "tool exploded" in notifier.texts[0]


async def test_notifies_limit_wait_and_resume(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: None)
    notifier = FakeNotifier()
    sent = []
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent), notifier=notifier)
    await orch.start_all()
    orch.add_prompt(project_dir, "do work")
    assert await _until(lambda: len(sent) >= 2)
    assert await _until(lambda: any("finished" in t for t in notifier.texts))
    await orch.stop_all()
    kinds = [t.split()[0] for t in notifier.texts]
    assert any("usage limit" in t and "until" in t for t in notifier.texts)
    assert any("continuing" in t for t in notifier.texts)


async def test_notifies_needs_you_when_paused_at_limit(storage, project_dir, fast_poll, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.last_human_text", lambda session_id: None)
    notifier = FakeNotifier()
    queue = enqueue_prompt(storage, project_dir, "do work")
    storage.set_queue_auto_continue(queue.id, False)
    orch = Orchestrator(storage, run_fn=_limited_then_ok([]), notifier=notifier)
    await orch.start_all()
    assert await _until(lambda: notifier.texts)
    await asyncio.sleep(0.1)
    await orch.stop_all()
    assert len(notifier.texts) == 1 and "needs you" in notifier.texts[0]


# --- auto-commit ------------------------------------------------------------


async def test_successful_task_in_git_repo_is_committed(storage, tmp_path, fast_poll):
    import subprocess

    repo = tmp_path / "gitproj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "x.txt").write_text("1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    async def edit(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        (repo / "x.txt").write_text("2\n")
        return RunResult(status=RunStatus.SUCCESS, result_text="edited")

    queue = enqueue_prompt(storage, str(repo), "Bump x")
    orch = Orchestrator(storage, run_fn=edit)
    await orch.start_all()
    assert await _until(lambda: storage.runs_for_queue(queue.id))
    await orch.stop_all()
    run = storage.runs_for_queue(queue.id)[0]
    assert run.git_commit and run.git_note == "committed"
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
    assert log.strip() == "ccc: Bump x"


async def test_task_outside_git_has_no_commit(storage, project_dir, fast_poll):
    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.SUCCESS)

    queue = enqueue_prompt(storage, project_dir, "x")
    orch = Orchestrator(storage, run_fn=ok)
    await orch.start_all()
    assert await _until(lambda: storage.runs_for_queue(queue.id))
    await orch.stop_all()
    run = storage.runs_for_queue(queue.id)[0]
    assert run.git_commit is None and run.git_note is None


# --- scheduling -------------------------------------------------------------


async def test_scheduled_task_waits_and_blocks_later_ones(storage, project_dir, fast_poll):
    sent = []

    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    queue = enqueue_prompt(storage, project_dir, "first")
    later = storage.add_queue_item(queue.id, "scheduled", not_before=datetime.now() + timedelta(hours=1))
    storage.add_queue_item(queue.id, "after")
    orch = Orchestrator(storage, run_fn=ok)
    await orch.start_all()
    assert await _until(lambda: sent == ["first"])
    await asyncio.sleep(0.2)
    assert sent == ["first"]  # "after" keeps its place behind the scheduled one
    storage.update_item_schedule(later.id, datetime.now() - timedelta(seconds=1))
    assert await _until(lambda: sent == ["first", "scheduled", "after"])
    await orch.stop_all()


async def test_no_git_commands_for_conversations_without_command_permission(storage, tmp_path, fast_poll, monkeypatch):
    import subprocess

    from ccc.launch_options import DEFAULT_SELECTION, toggle
    from ccc.orchestrator import start_conversation

    repo = tmp_path / "gitproj2"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    calls = []
    monkeypatch.setattr("ccc.gitops._git", lambda *a: calls.append(a))

    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        return RunResult(status=RunStatus.SUCCESS)

    q = start_conversation(storage, str(repo), toggle(DEFAULT_SELECTION, "perm_accept_edits", True), interactive=False)
    storage.add_queue_item(q.id, "edit things")
    orch = Orchestrator(storage, run_fn=ok)
    await orch.start_all()
    assert await _until(lambda: storage.runs_for_queue(q.id))
    await orch.stop_all()
    run = storage.runs_for_queue(q.id)[0]
    assert calls == []
    assert run.git_commit is None and "Skip all permission checks" in run.git_note


# --- permission prompt tool -------------------------------------------------


async def test_permission_tool_attached_only_outside_yolo(storage, project_dir, fast_poll):
    import json as _json

    from ccc.launch_options import DEFAULT_SELECTION, toggle
    from ccc.orchestrator import start_conversation

    seen = {}

    async def capture(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        seen[prompt] = launch_flags
        return RunResult(status=RunStatus.SUCCESS)

    yolo = start_conversation(storage, project_dir, DEFAULT_SELECTION, interactive=False)
    storage.add_queue_item(yolo.id, "yolo")
    edits = start_conversation(storage, project_dir, toggle(DEFAULT_SELECTION, "perm_accept_edits", True), interactive=False)
    storage.add_queue_item(edits.id, "edits")
    orch = Orchestrator(storage, run_fn=capture)
    await orch.start_all()
    assert await _until(lambda: len(seen) == 2)
    await orch.stop_all()
    assert "--permission-prompt-tool" not in seen["yolo"]
    flags = seen["edits"]
    assert flags[:2] == ["--permission-mode", "acceptEdits"]
    assert flags[flags.index("--permission-prompt-tool") + 1] == "mcp__ccc__approve"
    config = _json.loads(flags[flags.index("--mcp-config") + 1])
    server = config["mcpServers"]["ccc"]
    assert server["args"] == ["-I", "-m", "ccc.permission_mcp"]
    assert server["env"]["CCC_QUEUE_ID"] == str(edits.id)
    assert server["env"]["CCC_DB"] == storage.db_path
    assert storage.get_queue(edits.id).launch_flags == ["--permission-mode", "acceptEdits"]  # stored flags untouched


# --- usage reserve ----------------------------------------------------------


async def test_usage_reserve_holds_before_starting(storage, project_dir, fast_poll, monkeypatch):
    reset = datetime.now().replace(microsecond=0) + timedelta(hours=1)
    monkeypatch.setattr("ccc.orchestrator.get_pro_usage", lambda force_refresh=False: _five_hour_usage(85, reset))
    storage.set_setting("usage_reserve", "80")
    notifier = FakeNotifier()
    sent = []
    queue = enqueue_prompt(storage, project_dir, "work")
    storage.set_queue_auto_continue(queue.id, False)  # reserve waits regardless
    orch = Orchestrator(storage, run_fn=_limited_then_ok(sent), notifier=notifier)
    await orch.start_all()
    assert await _until(lambda: storage.get_queue(queue.id).status == QueueStatus.WAITING_RETRY)
    await asyncio.sleep(0.15)
    await orch.stop_all()
    q = storage.get_queue(queue.id)
    assert sent == [] and q.next_retry_at == reset + timedelta(seconds=30)
    assert q.last_error == "usage reserve 80%"
    assert len(notifier.texts) == 1 and "80% reserve" in notifier.texts[0]


async def test_usage_reserve_off_or_below_runs(storage, project_dir, fast_poll, monkeypatch):
    reset = datetime.now() + timedelta(hours=1)
    monkeypatch.setattr("ccc.orchestrator.get_pro_usage", lambda force_refresh=False: _five_hour_usage(85, reset))
    sent = []

    async def ok(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    enqueue_prompt(storage, project_dir, "a")
    storage.set_setting("usage_reserve", "90")
    orch = Orchestrator(storage, run_fn=ok)
    await orch.start_all()
    assert await _until(lambda: sent == ["a"])
    await orch.stop_all()


async def test_retry_runs_the_failed_task_again_first(storage, project_dir, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.01)
    calls = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        calls.append(prompt)
        if len(calls) == 1:
            return RunResult(status=RunStatus.ERROR, error_text="tool crashed")
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    orch.add_prompt(project_dir, "flaky")
    queue = storage.get_queue_by_path(project_dir)
    storage.add_queue_item(queue.id, "next")

    for _ in range(100):
        await asyncio.sleep(0.01)
        if storage.get_queue(queue.id).status == QueueStatus.BLOCKED_ERROR:
            break
    assert calls == ["flaky"]

    assert storage.retry_queue(queue.id)  # r in the TUI
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(calls) == 3:
            break
    await orch.stop_all()

    assert calls == ["flaky", "flaky", "next"]
    assert storage.get_queue(queue.id).status == QueueStatus.IDLE


def test_a_session_not_recorded_in_the_folder_is_not_resumed(storage, project_dir, monkeypatch):
    _recorded(monkeypatch)
    with pytest.raises(ValueError):
        start_conversation(storage, str(project_dir), toggle(DEFAULT_SELECTION, SESSION_RESUME, True),
                           session_id="from-elsewhere", interactive=False)


async def test_archived_headless_conversation_runs_nothing_until_restored(storage, project_dir, fast_poll):
    queue = enqueue_prompt(storage, project_dir, "do work")
    storage.set_queue_archived(queue.id, True)
    sent = []

    async def fake_run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        sent.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    orch = Orchestrator(storage, run_fn=fake_run)
    await orch.start_all()
    await asyncio.sleep(0.2)
    assert sent == []
    storage.set_queue_archived(queue.id, False)
    assert await _until(lambda: sent == ["do work"])
    await orch.stop_all()


def test_nothing_can_be_added_to_an_archived_conversation(storage, project_dir):
    from ccc.orchestrator import send_message
    from ccc.storage import ArchivedConversationError

    queue = enqueue_prompt(storage, project_dir, "first")
    storage.set_queue_archived(queue.id, True)
    with pytest.raises(ArchivedConversationError):
        send_message(storage, queue.id, "hello")
    with pytest.raises(ArchivedConversationError):
        enqueue_prompt(storage, project_dir, "more")
    assert len(storage.list_pending_items(queue.id)) == 1
    storage.set_queue_archived(queue.id, False)
    send_message(storage, queue.id, "hello")
    assert len(storage.list_pending_items(queue.id)) == 2
