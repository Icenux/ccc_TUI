"""The Tasks & Routines view (t): every task still waiting in any
conversation above the routines (#312)."""
import pytest
from textual.widgets import DataTable

from ccc import routines
from ccc.models import ItemStatus
from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.tasks import TaskEditModal, TasksModal
from ccc.tui.tasks_page import TasksRoutinesScreen


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path):
    storage = Storage(tmp_path / "history.db")
    shop = storage.create_queue("shop", str(tmp_path), "s1")
    blog = storage.create_queue("blog", str(tmp_path), "s2")
    return CCContinueApp(str(tmp_path / "history.db")), storage, shop, blog


def _rows(screen, table):
    table = screen.query_one(table, DataTable)
    return [[str(c) for c in table.get_row_at(n)] for n in range(table.row_count)]


@pytest.mark.asyncio
async def test_lists_waiting_tasks_of_every_conversation_and_not_finished_ones(tmp_path):
    app, storage, shop, blog = _app(tmp_path)
    storage.add_queue_item(shop.id, "write tests")
    done = storage.add_queue_item(shop.id, "old")
    storage.set_item_status(done.id, ItemStatus.DONE)
    storage.add_queue_item(blog.id, "publish")
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TasksRoutinesScreen)
        rows = _rows(app.screen, "#tr-tasks-table")
        assert [(r[1], r[2]) for r in rows] == [("shop", "write tests"), ("blog", "publish")]


@pytest.mark.asyncio
async def test_add_edit_delete_a_task(tmp_path):
    app, storage, shop, blog = _app(tmp_path)
    storage.add_queue_item(blog.id, "publish")
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("plus")  # on the blog's task: a task for blog
        await pilot.pause()
        assert isinstance(app.screen, TaskEditModal)
        app.screen.query_one("#task-text").text = "second"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert [i.prompt_text for i in storage.list_pending_items(blog.id)] == ["publish", "second"]
        await pilot.press("delete")
        await pilot.pause()
        assert [i.prompt_text for i in storage.list_pending_items(blog.id)] == ["publish"]  # the cursor was on the new one


@pytest.mark.asyncio
async def test_capital_t_opens_one_conversations_queue_and_routines_are_listed(tmp_path):
    app, storage, shop, blog = _app(tmp_path)
    storage.add_queue_item(blog.id, "publish")
    routines.add(storage, blog.id, "hourly", "check", routines.HOURS, "1")
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert [r[2] for r in _rows(app.screen, "#tr-routines-table")] == ["hourly"]
        await pilot.press("T")
        await pilot.pause()
        assert isinstance(app.screen, TasksModal)
        assert app.screen.queue_id == blog.id
