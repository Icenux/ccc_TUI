import pytest

from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.queue_detail import QueueDetailScreen


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])


@pytest.mark.asyncio
async def test_add_prompt_modal_queues_item(tmp_path):
    db_path = str(tmp_path / "history.db")
    project_dir = tmp_path / "proj-b"
    project_dir.mkdir()
    app = CCContinueApp(db_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")  # "+" belongs to Live sessions / queue detail
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        path_input = app.screen.query_one("#add-prompt-path")
        text_area = app.screen.query_one("#add-prompt-text")
        path_input.value = str(project_dir)
        text_area.text = "queued via modal"
        await pilot.click("#add-prompt-submit")
        await pilot.pause()

        queue = app.storage.get_queue_by_path(str(project_dir))
        assert queue is not None
        item = app.storage.next_pending_item(queue.id)
        assert item.prompt_text == "queued via modal"


@pytest.mark.asyncio
async def test_add_prompt_modal_shows_error_for_missing_directory(tmp_path):
    db_path = str(tmp_path / "history.db")
    app = CCContinueApp(db_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")  # "+" belongs to Live sessions / queue detail
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        path_input = app.screen.query_one("#add-prompt-path")
        text_area = app.screen.query_one("#add-prompt-text")
        path_input.value = str(tmp_path / "does-not-exist")
        text_area.text = "ahoj"
        await pilot.click("#add-prompt-submit")
        await pilot.pause()

        # modal stays open with an error, nothing got queued
        error = app.screen.query_one("#add-prompt-error")
        assert "not an existing directory" in str(error.render())
        assert app.storage.get_queue_by_path(str(tmp_path / "does-not-exist")) is None


@pytest.mark.asyncio
async def test_queue_detail_shows_the_queue(tmp_path):
    db_path = str(tmp_path / "history.db")
    storage = Storage(db_path)
    queue = storage.create_queue("proj-c", "/tmp/proj-c", "sess-1")

    from textual.app import App as _App

    class _HostApp(_App):
        async def on_mount(self):
            await self.push_screen(QueueDetailScreen(storage, queue.id))

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.screen.query_one("#queue-detail-title")
        assert "proj-c" in str(header.render())


@pytest.mark.asyncio
async def test_queue_detail_shows_now_changes_usage_and_errors(tmp_path):
    from datetime import datetime

    from textual.app import App as _App
    from textual.widgets import Static

    from ccc.models import PromptRun, RunStatus

    storage = Storage(str(tmp_path / "history.db"))
    folder = tmp_path / "plain"
    folder.mkdir()
    queue = storage.create_queue("proj-d", str(folder), "sess-d", launch_flags=["--permission-mode", "acceptEdits"])
    now = datetime.now()
    storage.insert_prompt_run(PromptRun(id=0, queue_id=queue.id, queue_item_id=None, prompt_text="p", started_at=now,
                                        finished_at=now, status=RunStatus.ERROR, error_text="boom", input_tokens=7))

    class _HostApp(_App):
        async def on_mount(self):
            await self.push_screen(QueueDetailScreen(storage, queue.id))

    app = _HostApp()
    async with app.run_test(size=(140, 50)) as pilot:
        for _ in range(40):
            await pilot.pause(0.1)
            if "boom" in app.screen.query_one("#overview-errors", Static).render().plain:
                break
        text = {name: app.screen.query_one(f"#overview-{name}", Static).render().plain
                for name in ("now", "changes", "usage", "errors")}
        assert "Idle" in text["now"] and "Queued messages: 0" in text["now"]
        assert "Skip all permission checks" in text["changes"]
        assert "Today: 7 tokens" in text["usage"]
        assert "boom" in text["errors"]
