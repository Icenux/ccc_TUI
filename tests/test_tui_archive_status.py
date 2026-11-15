from datetime import datetime

import pytest
from textual.app import App
from textual.widgets import DataTable

from ccc.models import PromptRun, QueueStatus, RunStatus
from ccc.storage import Storage
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.layout import WORKING_FRAMES, WORKING_STYLE, ConfirmModal, queue_marker


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


def _names(table: DataTable) -> list[str]:
    return [str(table.get_row(k)[0]) for k in table.rows]


def _finished_run(storage, queue_id):
    now = datetime.now()
    storage.insert_prompt_run(
        PromptRun(id=0, queue_id=queue_id, queue_item_id=None, prompt_text="p", started_at=now, finished_at=now,
                  status=RunStatus.SUCCESS)
    )


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    return Storage(tmp_path / "h.db")


def test_queue_marker():
    from ccc.models import Queue

    q = Queue(id=1, name="a", project_path="/a", session_id="s")
    assert queue_marker(q, unseen=False)[0] == "○"  # idle, already looked at
    assert queue_marker(q, unseen=True) == ("●", "bold #00af00")  # green: finished, not looked at
    q.archived = True
    assert queue_marker(q, unseen=False)[0] == " "
    q.archived = False
    for status, icon in [(QueueStatus.WAITING_RETRY, "‖"), (QueueStatus.PAUSED_LIMIT, "‖"),
                         (QueueStatus.BLOCKED_ERROR, "●")]:
        q.status = status
        assert queue_marker(q, unseen=True)[0] == icon  # activity beats "new"
    assert queue_marker(q, unseen=True)[1] == "bold red"  # error: red
    q.status = QueueStatus.RUNNING
    assert queue_marker(q, unseen=True)[0] in WORKING_FRAMES  # gold, turning
    assert queue_marker(q, unseen=True)[1] == WORKING_STYLE
    assert queue_marker(q, unseen=False, needs_approval=True) == ("●", "bold dodger_blue1")  # blue: needs you
    q.status = QueueStatus.IDLE
    q.live_busy = True  # typed into its Claude Code, not a queued prompt
    assert queue_marker(q, unseen=False)[0] in WORKING_FRAMES
    q.live_busy = False
    q.paused = True
    assert queue_marker(q, unseen=False)[0] == "‖"


@pytest.mark.asyncio
async def test_list_shows_markers_and_pending_count_and_clears_new_when_opened(storage):
    a = storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.update_queue_status(a.id, QueueStatus.RUNNING)
    storage.add_queue_item(a.id, "x")
    storage.add_queue_item(a.id, "y")
    _finished_run(storage, b.id)
    app = _host(ConversationsScreen(storage, select_queue_id=a.id))
    async with app.run_test() as pilot:
        await pilot.pause()
        names = _names(app.screen.query_one("#conversation-queue-list", DataTable))
        assert names[0].startswith(tuple(f"{f} a" for f in WORKING_FRAMES)) and names[0].endswith("·2")
        assert names[1].startswith("● b")
        app.screen.query_one("#conversation-queue-list", DataTable).move_cursor(row=1)
        await pilot.pause()
        await app.screen._refresh_current()
        await pilot.pause()
        assert b.id not in storage.unseen_queue_ids()
        names = _names(app.screen.query_one("#conversation-queue-list", DataTable))
        assert names[1].startswith("○ b")


@pytest.mark.asyncio
async def test_archive_moves_it_to_the_archive_view_and_x_restores(storage):
    from ccc.tui.archive import ArchiveScreen

    storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    app = _host(ConversationsScreen(storage, select_queue_id=b.id))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("x")
        await pilot.pause()
        assert storage.get_queue(b.id).archived is False  # asks first
        await pilot.press("y")
        await pilot.pause()
        assert storage.get_queue(b.id).archived is True
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        assert [n.strip() for n in _names(table)] == ["○ a"]
        await pilot.press("h")  # the Archive tab
        await pilot.pause()
        assert isinstance(app.screen, ArchiveScreen)
        assert app.screen.query_one("#nav-archive").has_class("-current")
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        assert [n.strip() for n in _names(table)] == ["b"]
        await pilot.press("x")
        await pilot.pause()
        assert storage.get_queue(b.id).archived is False
        assert _names(table) == []
        assert app.screen.has_class("-no-conversation")  # the archive is empty


@pytest.mark.asyncio
async def test_enter_in_archive_restores_and_opens_in_conversations(storage):
    from ccc.tui.archive import ArchiveScreen

    storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.set_queue_archived(b.id, True)
    app = _host(ArchiveScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen._current_queue_id == b.id
        assert app.focused.id == "conversation-queue-list"
        # Conversation-only keys are off here.
        assert app.screen.check_action("tasks", ()) is False
        await pilot.press("enter")
        await pilot.pause()
        assert storage.get_queue(b.id).archived is False
        assert type(app.screen) is ConversationsScreen
        assert app.screen._current_queue_id == b.id


@pytest.mark.asyncio
async def test_search_hit_in_archive_opens_the_archive_view(storage):
    from ccc.tui.archive import ArchiveScreen

    storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.set_queue_archived(b.id, True)
    app = _host(ConversationsScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.open_conversation(b.id)
        await pilot.pause()
        assert isinstance(app.screen, ArchiveScreen)
        assert app.screen._current_queue_id == b.id


@pytest.mark.asyncio
async def test_delete_asks_then_deletes(storage):
    a = storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    app = _host(ConversationsScreen(storage, select_queue_id=b.id))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        app.screen.action_delete_conversation()  # no key until #31
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()
        assert storage.find_queue(b.id) is not None
        app.screen.action_delete_conversation()  # no key until #31
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert storage.find_queue(b.id) is None
        assert app.screen._current_queue_id == a.id
        assert [n.strip() for n in _names(app.screen.query_one("#conversation-queue-list", DataTable))] == ["○ a"]


@pytest.mark.asyncio
async def test_running_conversation_cannot_be_deleted(storage):
    a = storage.create_queue("a", "/tmp/a", "sa")
    storage.update_queue_status(a.id, QueueStatus.RUNNING)
    app = _host(ConversationsScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        app.screen.action_delete_conversation()  # no key until #31
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert "working" in str(app.screen.query_one("#confirm-text").render())
        await pilot.press("y")
        await pilot.pause()
        assert storage.find_queue(a.id) is not None


@pytest.mark.asyncio
async def test_dashboard_hides_archived_and_marks_new(storage):
    a = storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.set_queue_archived(b.id, True)
    _finished_run(storage, a.id)
    app = _host(DashboardScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#dashboard-table", DataTable)
        # [0] is the cursor's ">"; the name's line is first, its folder under it.
        names = [str(table.get_row(k)[1]).splitlines()[0] for k in table.rows]
        assert names == ["● a"]


@pytest.mark.asyncio
async def test_delete_key_in_archive_asks_then_deletes(storage):
    from ccc.tui.archive import ArchiveScreen

    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.set_queue_archived(b.id, True)
    app = _host(ArchiveScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")
        await pilot.pause()
        assert storage.find_queue(b.id) is None
        assert _names(app.screen.query_one("#conversation-queue-list", DataTable)) == []
        assert app.screen.has_class("-no-conversation")
        banner = app.screen.query_one("#no-conversation")
        assert banner.banner_text == "EMPTY"


@pytest.mark.asyncio
async def test_no_delete_key_in_conversations(storage):
    # Claude Code runs next to it there; deleting stays in the Archive.
    b = storage.create_queue("b", "/tmp/b", "sb")
    app = _host(ConversationsScreen(storage, select_queue_id=b.id))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("delete")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        assert storage.find_queue(b.id) is not None


@pytest.mark.asyncio
async def test_archive_filter_enter_shows_the_first_result_without_restoring(storage):
    from ccc.tui.archive import ArchiveScreen

    a = storage.create_queue("alpha", "/tmp/a", "sa")
    b = storage.create_queue("beta", "/tmp/b", "sb")
    storage.set_queue_archived(a.id, True)
    storage.set_queue_archived(b.id, True)
    app = _host(ArchiveScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list", DataTable).focus()
        await pilot.press("f", *"bet", "enter")
        await pilot.pause()
        assert [n.strip() for n in _names(app.screen.query_one("#conversation-queue-list", DataTable))] == ["beta"]
        assert app.screen._current_queue_id == b.id
        assert storage.get_queue(b.id).archived
        assert app.screen.query_one("#conversation-queue-list", DataTable).has_focus
