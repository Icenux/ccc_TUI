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
