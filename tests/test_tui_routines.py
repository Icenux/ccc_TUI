import pytest
from textual.widgets import DataTable, Input, Select, Static, TextArea

from ccc import routines
from ccc.models import RoutineTarget
from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.routines import RoutineEditModal
from ccc.tui.tasks_page import TasksRoutinesScreen


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path):
    db_path = tmp_path / "history.db"
    storage = Storage(db_path)
    queue = storage.create_queue("shop", str(tmp_path), "s1")
    return CCContinueApp(str(db_path)), queue


def _cells(screen):
    table = screen.query_one("#tr-routines-table", DataTable)
    return [[str(c) for c in table.get_row_at(n)] for n in range(table.row_count)]


@pytest.mark.asyncio
async def test_add_a_routine_from_the_window(tmp_path):
    app, queue = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TasksRoutinesScreen)
        await pilot.press("tab")  # to the routines table
        await pilot.press("plus")
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, RoutineEditModal)
        editor.query_one("#routine-name", Input).value = "Nightly review"
        editor.query_one("#routine-queue", Select).value = queue.id
        editor.query_one("#routine-kind", Select).value = routines.WEEKDAYS
        editor.query_one("#routine-value", Input).value = "18:30"
        await pilot.pause()
        assert "Mon–Fri at 18:30 · next run" in str(editor.query_one("#routine-next", Static).render())
        editor.query_one("#routine-prompt", TextArea).text = "Review today's changes"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, TasksRoutinesScreen)
        [routine] = app.storage.list_routines()
        assert (routine.name, routine.cron, routine.queue_id, routine.target) == \
            ("Nightly review", "30 18 * * 1-5", queue.id, RoutineTarget.SAME)
        assert _cells(app.screen)[0][2:5] == ["Nightly review", "shop", "Mon–Fri at 18:30"]
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_a_bad_schedule_is_shown_and_not_saved(tmp_path):
    app, queue = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.push_screen(RoutineEditModal(app.storage, queue_id=queue.id))
        await pilot.pause()
        editor = app.screen
        editor.query_one("#routine-kind", Select).value = routines.MINUTES
        editor.query_one("#routine-value", Input).value = "7"
        editor.query_one("#routine-prompt", TextArea).text = "check"
        await pilot.pause()
        assert "doesn't divide 60" in str(editor.query_one("#routine-next", Static).render())
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen is editor
        assert app.storage.list_routines() == []


@pytest.mark.asyncio
async def test_toggle_and_run_now(tmp_path):
    app, queue = _app(tmp_path)
    routine = routines.add(app.storage, queue.id, "hourly", "check", routines.HOURS, "1")
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("space")
        await pilot.pause()
        assert not app.storage.get_routine(routine.id).enabled
        await pilot.press("r")
        await pilot.pause()
        assert app.storage.routine_items_active(routine.id) == 1
