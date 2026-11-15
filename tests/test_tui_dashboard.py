import pytest

from ccc.cli import add_command
from ccc.tui.app import CCContinueApp


@pytest.mark.asyncio
async def test_dashboard_lists_queues(tmp_path):
    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-a"
    project_dir.mkdir()
    add_command(db_path, str(project_dir), "do a thing")

    app = CCContinueApp(db_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#dashboard-table")
        rendered_rows = [str(cell) for row_key in table.rows for cell in table.get_row(row_key)]
        assert any("proj-a" in cell for cell in rendered_rows)
        assert any("1" in cell for cell in rendered_rows)  # pending count


@pytest.mark.asyncio
async def test_pressing_enter_opens_queue_detail(tmp_path):
    # DataTable owns the "enter" key itself (select_cursor) — this locks in
    # that opening the detail screen listens for its RowSelected message
    # rather than a screen-level "enter" binding, which never fires.
    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-a"
    project_dir.mkdir()
    add_command(db_path, str(project_dir), "do a thing")

    app = CCContinueApp(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#dashboard-table")
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()

        from ccc.tui.queue_detail import QueueDetailScreen

        assert isinstance(app.screen, QueueDetailScreen)


@pytest.mark.asyncio
async def test_pressing_c_opens_conversations_screen(tmp_path):
    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-a"
    project_dir.mkdir()
    add_command(db_path, str(project_dir), "do a thing")

    app = CCContinueApp(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()

        from ccc.tui.conversations import ConversationsScreen

        assert isinstance(app.screen, ConversationsScreen)


@pytest.mark.asyncio
async def test_dashboard_has_no_retry(tmp_path):
    # Only an overview: retrying is done in Conversations / queue detail.
    from ccc.models import QueueStatus
    from ccc.storage import Storage

    db_path = str(tmp_path / "history.db")
    storage = Storage(db_path)
    stuck = storage.create_queue("stuck", str(tmp_path), "s-stuck")
    storage.update_queue_status(stuck.id, QueueStatus.BLOCKED_ERROR, last_error="boom")

    app = CCContinueApp(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.screen.query("#sidebar-retry")
        await pilot.press("r")
        await pilot.pause()
        assert storage.get_queue(stuck.id).status == QueueStatus.BLOCKED_ERROR


@pytest.mark.asyncio
async def test_retry_button_only_for_a_blocked_conversation(tmp_path):
    from ccc.models import QueueStatus
    from ccc.storage import Storage
    from ccc.tui.conversations import ConversationsScreen

    db_path = str(tmp_path / "history.db")
    storage = Storage(db_path)
    ok = storage.create_queue("ok", str(tmp_path), "s-ok")
    stuck = storage.create_queue("stuck", str(tmp_path), "s-stuck")
    storage.update_queue_status(stuck.id, QueueStatus.BLOCKED_ERROR, last_error="boom")

    app = CCContinueApp(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ConversationsScreen(storage, select_queue_id=ok.id))
        await pilot.pause()
        button = app.screen.query_one("#sidebar-retry")
        assert not button.display
        app.screen.open_conversation(stuck.id)
        await pilot.pause()
        assert button.display
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("r")  # retried: nothing left to retry
        await pilot.pause()
        assert storage.get_queue(stuck.id).status == QueueStatus.IDLE
        assert not button.display

        storage.update_queue_status(stuck.id, QueueStatus.PAUSED_LIMIT)
        await pilot.pause(2.5)  # the periodic refresh
        assert button.display


@pytest.mark.asyncio
async def test_no_retry_button_without_conversations(tmp_path):
    from ccc.storage import Storage
    from ccc.tui.conversations import ConversationsScreen

    storage = Storage(tmp_path / "history.db")
    app = CCContinueApp(str(tmp_path / "history.db"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ConversationsScreen(storage))
        await pilot.pause()
        assert not app.screen.query_one("#sidebar-retry").display


@pytest.mark.asyncio
async def test_getting_started_until_the_first_conversation(tmp_path):
    from ccc.storage import Storage

    db_path = str(tmp_path / "history.db")
    app = CCContinueApp(db_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        welcome = app.screen.query_one("#dashboard-welcome")
        assert welcome.display
        Storage(db_path).create_queue("first", str(tmp_path), "s-1")
        app.screen.refresh_table()
        await pilot.pause()
        assert not welcome.display


def test_shortened_path_always_keeps_the_last_folder():
    from ccc.tui.dashboard import _shorten_path

    assert _shorten_path("/home/you/shop", 48) == "/home/you/shop"
    assert _shorten_path("/home/you/projects/work/shop-api", 20) == "…/work/shop-api"
    assert _shorten_path("/home/you/projects/work/shop-api", 14) == "…/shop-api"
    assert _shorten_path("/a/b/a-very-long-folder-name", 10) == "…lder-name"  # only then the last one is cut


def _setup(tmp_path, monkeypatch):
    from ccc import gitops
    from ccc.models import PermissionRequest  # noqa: F401
    from ccc.storage import Storage

    db_path = str(tmp_path / "history.db")
    project = tmp_path / "shop-api"
    project.mkdir()
    add_command(db_path, str(project), "do a thing")
    storage = Storage(db_path)
    queue = storage.list_queues()[0]
    monkeypatch.setattr("ccc.gitops.work_status",
                        lambda cwd: gitops.WorkStatus(12, 3, 2, 0, "origin/main", "feature-x"))
    return db_path, storage, queue


def _cells(app):
    table = app.screen.query_one("#dashboard-table")
    headers = [str(column.label) for column in table.columns.values()]
    rows = [[str(cell) for cell in table.get_row(key)] for key in table.rows]
    return headers, rows


@pytest.mark.asyncio
async def test_dashboard_shows_git_and_what_waits_for_you(tmp_path, monkeypatch):
    from ccc.models import QueueStatus
    from textual.widgets import Static

    db_path, storage, queue = _setup(tmp_path, monkeypatch)
    storage.update_queue_status(queue.id, QueueStatus.BLOCKED_ERROR)  # an error needs you
    app = CCContinueApp(db_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.5)
        app.screen.refresh_table()
        headers, rows = _cells(app)
        # The cursor's mark, then the design's columns (#250); the path is under the name.
        assert headers == [" ", "PROJECT", "BRANCH", "QUEUED", "CTX", "STATUS", "GOAL", "TOKENS"]
        row = rows[0]
        assert row[1].endswith("shop-api") and "\n" in row[1]
        assert row[2] == "feature-x +12−3 ↑2" and row[5] == "BLOCKED"
        assert "1 waiting for you" in app.screen.query_one("#dashboard-tokens", Static).render().plain


@pytest.mark.asyncio
async def test_columns_choice_does_not_depend_on_window_width(tmp_path, monkeypatch):
    from ccc import dashboard_columns

    db_path, storage, _queue = _setup(tmp_path, monkeypatch)
    app = CCContinueApp(db_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.5)
        assert len(_cells(app)[0]) == 8  # the default is everything, even in a narrow window
        dashboard_columns.save(storage, [dashboard_columns.GIT, dashboard_columns.PATH])
        app.screen.refresh_table()
        headers, rows = _cells(app)
        assert headers == [" ", "PROJECT", "BRANCH"]
        assert rows[0][1].endswith("shop-api")
    app = CCContinueApp(db_path)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause(0.5)
        assert _cells(app)[0] == [" ", "PROJECT", "BRANCH"]


def test_columns_choice_defaults_and_saves_in_table_order(tmp_path):
    from ccc import dashboard_columns as dc
    from ccc.storage import Storage

    storage = Storage(str(tmp_path / "h.db"))
    assert dc.shown(storage) == list(dc.IDS)
    dc.save(storage, [dc.STATUS, dc.PATH])
    assert dc.shown(storage) == [dc.PATH, dc.STATUS]
    dc.save(storage, [])  # chosen: none — only the name
    assert dc.shown(storage) == []


@pytest.mark.asyncio
async def test_dashboard_goal_column_shows_the_running_goal_then_its_result(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from ccc import goals
    from ccc.models import GoalStatus

    db_path, storage, queue = _setup(tmp_path, monkeypatch)
    app = CCContinueApp(db_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.5)
        app.screen.refresh_table()
        assert _cells(app)[1][0][6] == "–"  # no goal yet
        goal = goals.start(storage, queue.id, "tokens expire", max_rounds=5)
        storage.update_goal(goal.id, round=3)
        app.screen.refresh_table()
        assert _cells(app)[1][0][6] == "◎ 3/5"
        storage.update_goal(goal.id, status=GoalStatus.PASSED, finished_at=datetime.now())
        app.screen.refresh_table()
        assert _cells(app)[1][0][6] == "✅ met 3/5"
        storage.update_goal(goal.id, finished_at=datetime.now() - timedelta(days=2))
        app.screen.refresh_table()
        assert _cells(app)[1][0][6] == "–"  # a day later it leaves the Dashboard


@pytest.mark.asyncio
async def test_x_archives_the_selected_conversation(tmp_path):
    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-a"
    project_dir.mkdir()
    add_command(db_path, str(project_dir), "do a thing")

    app = CCContinueApp(db_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#dashboard-table").move_cursor(row=0)
        await pilot.press("x")
        await pilot.pause()
        assert not any(q.archived for q in __import__("ccc.storage").storage.Storage(db_path).list_queues())  # asks first
        await pilot.press("y")
        await pilot.pause()
        assert all(q.archived for q in app.screen.storage.list_queues())
        assert app.screen.query_one("#dashboard-table").row_count == 0


@pytest.mark.asyncio
async def test_long_press_menu_deletes_after_confirming(tmp_path):
    from ccc.tui.dashboard import ConversationMenu
    from ccc.tui.layout import ConfirmModal

    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-a"
    project_dir.mkdir()
    add_command(db_path, str(project_dir), "do a thing")

    app = CCContinueApp(db_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen._long_press(0)
        await pilot.pause()
        assert isinstance(app.screen, ConversationMenu)
        await pilot.click("#menu-delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")
        await pilot.pause()
        assert app.screen.storage.list_queues() == []
