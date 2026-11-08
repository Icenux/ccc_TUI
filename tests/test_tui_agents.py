import pytest
from textual.widgets import DataTable, Static

from ccc.storage import Storage
from ccc.tui.agents import AgentsModal
from ccc.tui.app import CCContinueApp


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))


def _write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


@pytest.mark.asyncio
async def test_window_lists_agents_and_marks_a_broken_one(tmp_path):
    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    _write(project / ".claude" / "agents", "ok.md", "---\nname: ok\ndescription: Does one thing\ntools: Read\nmodel: haiku\n---\nPrompt line")
    _write(project / ".claude" / "agents", "bad.md", "---\nname: bad\n---\nx")
    db_path = tmp_path / "history.db"
    Storage(db_path).create_queue("shop", str(project), "s1")
    app = CCContinueApp(str(db_path))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("c")  # Conversations: the "shop" conversation is selected there
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, AgentsModal)
        table = modal.query_one("#agents-table", DataTable)
        rows = [[str(c) for c in table.get_row_at(n)] for n in range(table.row_count)]
        assert [r[0].strip() for r in rows] == ["bad  ⚠", "ok"]
        assert rows[1][1:] == ["project", "haiku", "Does one thing"]
        detail = lambda: str(modal.query_one("#agents-detail", Static).render())  # noqa: E731
        assert "Claude Code skips this file: no description" in detail()
        await pilot.press("down")
        await pilot.pause()
        assert "Tools: Read" in detail() and "Prompt line" in detail()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, AgentsModal)


@pytest.mark.asyncio
async def test_new_conversation_dialog_runs_the_whole_conversation_as_an_agent(tmp_path):
    from ccc import launch_options as lo
    from ccc.tui.new_conversation import NewConversationModal
    from textual.widgets import Checkbox, Input

    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    _write(project / ".claude" / "agents", "reviewer.md", "---\nname: reviewer\ndescription: Reviews\n---\nx")
    _write(project / ".claude" / "agents", "broken.md", "---\nname: broken\n---\nx")
    app = CCContinueApp(str(tmp_path / "history.db"))
    async with app.run_test(size=(150, 70)) as pilot:
        await pilot.pause()
        app.push_screen(NewConversationModal(Storage(tmp_path / "h.db"), browse_root=tmp_path))
        await pilot.pause()
        dialog = app.screen
        dialog.query_one("#launch-path", Input).value = str(project)
        await pilot.pause()
        assert not dialog.query_one("#agent-list").display  # hidden until the mode is ticked
        dialog.query_one(f"#opt-{lo.AGENT_SELECT}", Checkbox).value = True
        await pilot.pause()
        boxes = list(dialog.query("#agent-list Checkbox"))
        assert [b.agent_name for b in boxes] == ["reviewer"]  # the broken file is not offered
        assert dialog.query_one("#agent-list").size.height >= 2  # the list is as tall as its content, not squeezed
        boxes[0].value = True
        await pilot.pause()
        assert lo.persistent_flags(dialog.selection)[-2:] == ["--agent", "reviewer"]
        dialog.query_one(f"#opt-{lo.AGENT_SELECT}", Checkbox).value = False
        await pilot.pause()
        assert lo.agent_names(dialog.selection) == []
