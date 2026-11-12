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
        dialog.action_toggle_more(True)  # every option is behind "+ option" (#250)
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


@pytest.mark.asyncio
async def test_window_creates_an_agent_from_a_template(tmp_path):
    from ccc.tui.agents import AgentCreateModal
    from textual.widgets import Select, TextArea

    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    db_path = tmp_path / "history.db"
    Storage(db_path).create_queue("shop", str(project), "s1")
    app = CCContinueApp(str(db_path))
    async with app.run_test(size=(140, 55)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        assert "no agents yet" in str(app.screen.query_one("#agents-table", DataTable).get_row_at(0)[0])
        await pilot.press("a")
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, AgentCreateModal)
        dialog.query_one("#agent-template", Select).value = "code-reviewer"
        await pilot.pause()
        preview = dialog.query_one("#agent-preview", TextArea).text
        assert preview.startswith("---\nname: code-reviewer\n") and "tools: Read, Grep, Glob, Bash" in preview
        dialog.query_one("#agent-template", Select).value = "test-runner"
        await pilot.pause()
        assert dialog.query_one("#agent-name").value == "test-runner"  # the name follows the template
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert (project / ".claude" / "agents" / "test-runner.md").is_file()
        modal = app.screen
        assert modal.__class__.__name__ == "AgentsModal"
        assert [a.name for a in modal._agents] == ["test-runner"]
        assert modal.selected().name == "test-runner"


def _project(tmp_path):
    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    db_path = tmp_path / "history.db"
    Storage(db_path).create_queue("shop", str(project), "s1")
    return project, CCContinueApp(str(db_path))


async def _open_new_agent(app, pilot):
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    await pilot.press("A")
    await pilot.pause()
    await pilot.press("a")
    await pilot.pause()
    return app.screen


@pytest.mark.asyncio
async def test_describe_it_myself_with_the_outline_prompt(tmp_path):
    from ccc import agents
    from textual.widgets import Input, Select, TextArea

    project, app = _project(tmp_path)
    async with app.run_test(size=(140, 70)) as pilot:
        dialog = await _open_new_agent(app, pilot)
        assert dialog.query_one("#agent-custom").display and not dialog.query_one("#agent-preview").display
        await pilot.press("ctrl+s")  # nothing described yet: nothing is written
        await pilot.pause()
        assert not (project / ".claude" / "agents").exists()
        dialog.query_one("#agent-describe", Input).value = "Checks that texts exist in EN and CS. Use after text changes."
        dialog.query_one("#agent-effort", Select).value = "fast"
        dialog.query_one("#agent-name", Input).value = "text-checker"
        await pilot.pause()
        prompt = dialog.query_one("#agent-prompt", TextArea).text
        assert "Checks that texts exist" in prompt and "never change a file" in prompt  # the outline follows the description
        await pilot.press("ctrl+s")
        await pilot.pause()
    [a] = agents.list_agents(str(project))
    assert (a.name, a.tools, a.model) == ("text-checker", ("Read", "Grep", "Glob"), "haiku")
    assert a.description.startswith("Checks that texts exist") and "never change a file" in a.prompt


@pytest.mark.asyncio
async def test_claude_writes_the_prompt_and_the_user_may_still_change_it(tmp_path, monkeypatch):
    from ccc import agent_wizard, agents
    from textual.widgets import Button, Input, Select, Static, TextArea

    seen = {}

    async def fake(job, tools, run_fn=None):
        seen["args"] = (job, tools)
        return "You check texts.\n\nReport what is missing."

    monkeypatch.setattr(agent_wizard, "generate_prompt", fake)
    project, app = _project(tmp_path)
    async with app.run_test(size=(140, 70)) as pilot:
        dialog = await _open_new_agent(app, pilot)
        dialog.query_one("#agent-describe", Input).value = "Checks texts"
        dialog.query_one("#agent-access", Select).value = "run"
        await pilot.pause()
        dialog.query_one("#agent-generate", Button).press()
        for _n in range(20):
            await pilot.pause(0.1)
            if "Written by Claude" in str(dialog.query_one("#agent-status", Static).render()):
                break
        assert seen["args"] == ("Checks texts", ("Read", "Grep", "Glob", "Bash"))
        assert dialog.query_one("#agent-prompt", TextArea).text.startswith("You check texts.")
        dialog.query_one("#agent-describe", Input).value = "Checks texts and docs"  # must not overwrite Claude's text
        await pilot.pause()
        assert dialog.query_one("#agent-prompt", TextArea).text.startswith("You check texts.")
        await pilot.press("ctrl+s")
        await pilot.pause()
    [a] = agents.list_agents(str(project))
    assert a.prompt.startswith("You check texts.") and a.tools == ("Read", "Grep", "Glob", "Bash")


@pytest.mark.asyncio
async def test_a_failed_generation_keeps_the_outline_and_says_why(tmp_path, monkeypatch):
    from ccc import agent_wizard
    from textual.widgets import Button, Input, Static, TextArea

    async def fail(job, tools, run_fn=None):
        raise ValueError("usage limit reached")

    monkeypatch.setattr(agent_wizard, "generate_prompt", fail)
    _project_dir, app = _project(tmp_path)
    async with app.run_test(size=(140, 70)) as pilot:
        dialog = await _open_new_agent(app, pilot)
        dialog.query_one("#agent-describe", Input).value = "Checks texts"
        await pilot.pause()
        before = dialog.query_one("#agent-prompt", TextArea).text
        dialog.query_one("#agent-generate", Button).press()
        for _n in range(20):
            await pilot.pause(0.1)
            if "usage limit" in str(dialog.query_one("#agent-status", Static).render()):
                break
        assert "usage limit reached" in str(dialog.query_one("#agent-status", Static).render())
        assert dialog.query_one("#agent-prompt", TextArea).text == before
        assert not dialog.query_one("#agent-generate", Button).disabled  # may try again


@pytest.mark.asyncio
async def test_edit_an_agent_in_the_window(tmp_path):
    from textual.widgets import Input, Select, TextArea
    from ccc import agents
    from ccc.tui.agents import AgentEditModal

    project, app = _project(tmp_path)
    _write(project / ".claude" / "agents", "x.md",
           "---\nname: x\ndescription: Old\ntools: Read\nmodel: haiku\nmemory: project\n---\nOld prompt")
    async with app.run_test(size=(140, 55)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, AgentEditModal)
        assert dialog.query_one("#agent-edit-description", Input).value == "Old"
        dialog.query_one("#agent-edit-description", Input).value = "New description"
        dialog.query_one("#agent-edit-tools", Input).value = "Read, Grep"
        dialog.query_one("#agent-edit-model", Select).value = "sonnet"
        dialog.query_one("#agent-edit-prompt", TextArea).text = "New prompt"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AgentsModal"
    [a] = agents.list_agents(str(project))
    assert (a.description, a.tools, a.model, a.prompt.strip()) == ("New description", ("Read", "Grep"), "sonnet", "New prompt")
    assert "memory: project" in a.path.read_text()


@pytest.mark.asyncio
async def test_an_invalid_edit_shows_why_and_changes_nothing(tmp_path):
    from textual.widgets import Input, Static

    project, app = _project(tmp_path)
    _write(project / ".claude" / "agents", "x.md", "---\nname: x\ndescription: Old\n---\nPrompt")
    before = (project / ".claude" / "agents" / "x.md").read_text()
    async with app.run_test(size=(140, 55)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("enter")  # Enter edits the selected agent
        await pilot.pause()
        dialog = app.screen
        dialog.query_one("#agent-edit-description", Input).value = "   "
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "needs a description" in str(dialog.query_one("#agent-edit-error", Static).render())
        assert app.screen is dialog
    assert (project / ".claude" / "agents" / "x.md").read_text() == before


@pytest.mark.asyncio
async def test_delete_asks_first_and_keeps_the_file_in_ccc_s_folder(tmp_path, monkeypatch):
    from ccc import agents

    monkeypatch.setenv("CCC_AGENT_TRASH", str(tmp_path / "trash"))
    project, app = _project(tmp_path)
    _write(project / ".claude" / "agents", "x.md", "---\nname: x\ndescription: d\n---\nPrompt")
    async with app.run_test(size=(140, 55)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("n")  # No
        await pilot.pause()
        assert (project / ".claude" / "agents" / "x.md").exists()
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not (project / ".claude" / "agents" / "x.md").exists()
        assert agents.list_agents(str(project)) == []
        assert len(list((tmp_path / "trash").iterdir())) == 1
