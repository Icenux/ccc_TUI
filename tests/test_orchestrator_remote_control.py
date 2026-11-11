import asyncio

import pytest

from ccc import managed_sessions as ms
from ccc.launch_options import DEFAULT_SELECTION, FORK, SESSION_RESUME, toggle
from ccc.live_delivery import DeliveryResult
from ccc.models import DeliveryMode, ItemStatus, QueueStatus
from ccc.orchestrator import DEFAULT_CONTINUE_TEXT, Orchestrator, start_conversation
from ccc.session_discovery import DiscoveredSession
from ccc.storage import Storage


class FakeTmux:
    """Stands in for the managed tmux sessions and `claude agents`."""

    def __init__(self):
        self.running: set[int] = set()
        self.started: list[list[str]] = []
        self.stopped: list[int] = []
        self.pasted: list[str] = []
        self.confirmed = 0
        self.pane_text = ""
        self.status = "idle"
        self.state = None
        self.session_id_override = None
        self.queue_session = {}

    def install(self, monkeypatch):
        monkeypatch.setattr(ms, "is_running", lambda qid: qid in self.running)

        def start(queue, command):
            self.running.add(queue.id)
            self.started.append(command)
            self.queue_session[queue.id] = queue.session_id

        monkeypatch.setattr(ms, "start", start)
        monkeypatch.setattr(ms, "stop", lambda qid: (self.running.discard(qid), self.stopped.append(qid)))
        monkeypatch.setattr(ms, "capture", lambda qid: self.pane_text)
        monkeypatch.setattr(ms, "pane_pid", lambda qid: 1000 + qid)
        monkeypatch.setattr(ms, "confirm", lambda qid: setattr(self, "confirmed", self.confirmed + 1))

        def paste(qid, text):
            self.pasted.append(text)
            self.status = "busy"
            return DeliveryResult(delivered=True)

        monkeypatch.setattr(ms, "paste_and_submit", paste)

        def discover():
            return [
                DiscoveredSession(
                    session_id=self.session_id_override or self.queue_session.get(qid, "?"),
                    name="x", cwd="/tmp", kind="interactive", status=self.status, pid=1000 + qid, state=self.state,
                )
                for qid in self.running
            ]

        monkeypatch.setattr("ccc.orchestrator.discover_sessions", discover)


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "t.db")


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return str(d)


@pytest.fixture
def tmux(monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr("ccc.orchestrator.MAINTENANCE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("ccc.orchestrator.DELIVERY_SETTLE_SECONDS", 0.05)
    fake = FakeTmux()
    fake.install(monkeypatch)
    return fake


async def _until(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def _run_orch(storage, body):
    async def never(*a, **k):
        raise AssertionError("remote-control conversations must not run headless")

    orch = Orchestrator(storage, run_fn=never)
    await orch.start_all()
    try:
        await body()
    finally:
        await orch.stop_all()


def _rc(storage, project_dir, **kw):
    return start_conversation(storage, project_dir, DEFAULT_SELECTION, remote_control=True, **kw)


async def test_start_conversation_remote_control_mode(storage, project_dir):
    q = _rc(storage, project_dir)
    assert q.delivery_mode == DeliveryMode.REMOTE_CONTROL
    assert start_conversation(storage, project_dir, DEFAULT_SELECTION).delivery_mode == DeliveryMode.INTERACTIVE
    assert start_conversation(storage, project_dir, DEFAULT_SELECTION, interactive=False).delivery_mode == DeliveryMode.HEADLESS


async def test_daemon_starts_process_and_reads_url_and_trust(storage, project_dir, tmux):
    q = _rc(storage, project_dir, name="conv")
    tmux.pane_text = "Do you trust the files in this folder?\n❯ 1. Yes, proceed\n... https://claude.ai/code/session_ABC123"

    async def body():
        assert await _until(lambda: tmux.started)
        assert await _until(lambda: storage.get_queue(q.id).remote_control_url == "https://claude.ai/code/session_ABC123")
        await asyncio.sleep(0.2)
        assert tmux.confirmed == 1  # at most once per start

    await _run_orch(storage, body)
    cmd = tmux.started[0]
    assert cmd[:3] == ["claude", "--session-id", q.session_id]
    assert "--remote-control" in cmd and "conv" in cmd


async def test_delivers_only_when_idle(storage, project_dir, tmux):
    q = _rc(storage, project_dir)
    tmux.status = "busy"
    storage.add_queue_item(q.id, "line 1\nline 2")

    async def body():
        await asyncio.sleep(0.2)
        assert tmux.pasted == []
        tmux.status = "idle"
        assert await _until(lambda: tmux.pasted == ["line 1\nline 2"])
        assert await _until(lambda: not storage.list_pending_items(q.id))

    await _run_orch(storage, body)
    runs = storage.runs_for_queue(q.id)
    assert runs and "interactive session" in runs[0].result_text


async def test_blocked_session_queues_continue_and_waits(storage, project_dir, tmux):
    q = _rc(storage, project_dir)

    async def body():
        assert await _until(lambda: tmux.started)
        tmux.state = "blocked"
        assert await _until(lambda: storage.get_queue(q.id).status == QueueStatus.WAITING_RETRY)
        assert [i.prompt_text for i in storage.list_pending_items(q.id)] == [DEFAULT_CONTINUE_TEXT]
        await asyncio.sleep(0.2)
        assert len(storage.list_pending_items(q.id)) == 1  # only once per blocked episode

    await _run_orch(storage, body)


async def test_blocked_with_auto_continue_off_pauses(storage, project_dir, tmux):
    q = start_conversation(storage, project_dir, DEFAULT_SELECTION, remote_control=True, auto_continue=False)

    async def body():
        assert await _until(lambda: tmux.started)
        tmux.state = "blocked"
        assert await _until(lambda: storage.get_queue(q.id).status == QueueStatus.PAUSED_LIMIT)

    await _run_orch(storage, body)


async def test_archive_and_delete_stop_the_process(storage, project_dir, tmux):
    a = _rc(storage, project_dir)
    b = _rc(storage, project_dir)

    async def body():
        assert await _until(lambda: {a.id, b.id} <= tmux.running)
        storage.set_queue_archived(a.id, True)
        assert await _until(lambda: a.id in tmux.stopped)
        await asyncio.sleep(0.1)
        assert a.id not in tmux.running  # not restarted while archived
        storage.delete_queue(b.id)
        assert await _until(lambda: b.id in tmux.stopped)

    await _run_orch(storage, body)


async def test_fork_adopts_real_session_id(storage, project_dir, tmux, monkeypatch):
    # A session is resumed only from the folder it was recorded in (#160): "orig" was recorded here.
    from datetime import datetime
    from ccc.transcript import SessionSummary
    monkeypatch.setattr("ccc.orchestrator.list_sessions_for_directory",
                        lambda path: [SessionSummary("orig", datetime(2026, 10, 1, 9, 0), "x", 1)])
    monkeypatch.setattr("ccc.orchestrator.session_recorded_in", lambda session_id, path: True)
    sel = toggle(toggle(DEFAULT_SELECTION, SESSION_RESUME, True), FORK, True)
    q = start_conversation(storage, project_dir, sel, session_id="orig", remote_control=True)
    tmux.session_id_override = "forked-real-id"

    async def body():
        assert await _until(lambda: storage.get_queue(q.id).session_id == "forked-real-id")

    await _run_orch(storage, body)
    assert tmux.started[0][:4] == ["claude", "--resume", "orig", "--fork-session"]
    assert storage.get_queue(q.id).first_run_flags is None


async def test_plain_resume_follows_new_session_id(storage, project_dir, tmux):
    # `claude --resume X` writes to a NEW session id; the app must read that transcript, not X's old one.
    q = _rc(storage, project_dir)
    assert storage.get_queue(q.id).first_run_flags is None
    tmux.session_id_override = "resumed-real-id"

    async def body():
        assert await _until(lambda: storage.get_queue(q.id).session_id == "resumed-real-id")

    await _run_orch(storage, body)


async def test_trust_prompt_not_confirmed_after_startup_window(storage, project_dir, tmux, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.TRUST_CONFIRM_WINDOW_SECONDS", 0)
    _rc(storage, project_dir)
    tmux.pane_text = "Do you trust the files in this folder?\n❯ 1. Yes, proceed"

    async def body():
        assert await _until(lambda: tmux.started)
        await asyncio.sleep(0.2)

    await _run_orch(storage, body)
    assert tmux.confirmed == 0


async def test_trust_dialog_confirmed_while_a_message_waits(storage, project_dir, tmux, monkeypatch):
    # Kompas, 2026-10-16: a message was queued, so the daemon sat waiting
    # for Claude to go idle — but Claude sat on the folder-trust dialog,
    # which only the main loop answered. Both waited forever.
    monkeypatch.setattr("ccc.orchestrator.MAINTENANCE_INTERVAL_SECONDS", 0)
    q = _rc(storage, project_dir)
    storage.add_queue_item(q.id, "/model")
    tmux.status = "busy"  # the dialog: Claude isn't idle
    tmux.pane_text = "Do you trust the files in this folder?\n❯ 1. Yes, proceed"

    async def body():
        assert await _until(lambda: tmux.confirmed == 1, timeout=3)
        tmux.status = "idle"
        assert await _until(lambda: tmux.pasted == ["/model"], timeout=3)

    await _run_orch(storage, body)


async def test_work_typed_into_claude_code_is_tracked_busy_then_unseen(storage, project_dir, tmux):
    q = _rc(storage, project_dir)
    storage.mark_seen(q.id)

    async def body():
        assert await _until(lambda: q.id in tmux.running)
        tmux.status = "busy"  # you typed into the pane, no queued prompt
        assert await _until(lambda: storage.get_queue(q.id).live_busy)
        assert q.id not in storage.unseen_queue_ids()
        tmux.status = "idle"
        assert await _until(lambda: q.id in storage.unseen_queue_ids())
        assert not storage.get_queue(q.id).live_busy

    await _run_orch(storage, body)


LIMIT_PANE = """● Bash(cargo build)
  ⎿  You've hit your 5-hour limit · resets 8pm
     (Europe/Prague)
     /upgrade or /usage-credits to finish what
     you're working on.

✻ Crunched for 1h 9m 28s · done 19:24

❯
"""


async def test_limit_message_on_screen_is_a_limit_without_blocked_state(storage, project_dir, tmux):
    """Claude Code reports no `blocked` state: the notice on screen is all there is (auto-continue on)."""
    q = _rc(storage, project_dir)

    async def body():
        assert await _until(lambda: tmux.started)
        tmux.pane_text = LIMIT_PANE
        assert await _until(lambda: storage.get_queue(q.id).status == QueueStatus.WAITING_RETRY)
        assert [i.prompt_text for i in storage.list_pending_items(q.id)] == [DEFAULT_CONTINUE_TEXT]
        await asyncio.sleep(0.2)
        assert len(storage.list_pending_items(q.id)) == 1

    await _run_orch(storage, body)


async def test_limit_message_with_auto_continue_off_pauses(storage, project_dir, tmux):
    q = start_conversation(storage, project_dir, DEFAULT_SELECTION, remote_control=True, auto_continue=False)

    async def body():
        assert await _until(lambda: tmux.started)
        tmux.pane_text = LIMIT_PANE
        assert await _until(lambda: storage.get_queue(q.id).status == QueueStatus.PAUSED_LIMIT)

    await _run_orch(storage, body)


def test_limit_message_ignored_once_answered_or_continued():
    assert ms.limit_message(LIMIT_PANE)
    assert ms.limit_message(LIMIT_PANE.replace("❯\n", "❯ Pokracuj\n\n● Překlad prošel.\n")) is None
    assert ms.limit_message("● hotovo\n❯\n") is None


def test_limit_message_cannot_be_faked_by_text_on_screen():
    assert ms.limit_message("● cat poznamky.txt\n  You've hit your 5-hour limit · resets 8pm\n❯\n") is None
    assert ms.limit_message("● echo\n  ⎿  see: You've hit your 5-hour limit\n❯\n") is None
