import json

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input, OptionList

from ccc.storage import Storage
from ccc.tui.new_conversation import NewConversationModal


def _host(modal, results):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(modal, results.append)
    return Host()


@pytest.mark.asyncio
async def test_start_creates_queue_with_ticked_flags(tmp_path):
    storage = Storage(tmp_path / "h.db")
    project = tmp_path / "proj"; project.mkdir()
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#launch-path", Input).value = str(project)
        app.screen.query_one("#opt-model_haiku", Checkbox).value = True
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    queue = storage.get_queue(results[0])
    assert queue.project_path == str(project)
    assert queue.launch_flags == ["--dangerously-skip-permissions", "--model", "haiku"]


@pytest.mark.asyncio
async def test_exclusive_checkboxes_untick_each_other(tmp_path):
    app = _host(NewConversationModal(Storage(tmp_path / "h.db"), browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#opt-model_opus", Checkbox).value = True
        await pilot.pause()
        app.screen.query_one("#opt-model_sonnet", Checkbox).value = True
        await pilot.pause()
        assert app.screen.query_one("#opt-model_opus", Checkbox).value is False
        assert app.screen.query_one("#opt-fork", Checkbox).disabled


@pytest.mark.asyncio
async def test_bad_path_shows_error_and_creates_nothing(tmp_path):
    storage = Storage(tmp_path / "h.db")
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#launch-path", Input).value = str(tmp_path / "missing")
        await pilot.click("#launch-start")
        await pilot.pause()
        assert "not an existing directory" in str(app.screen.query_one("#launch-error").render())
    assert results == [] and storage.list_queues() == []


@pytest.mark.asyncio
async def test_recent_directory_fills_path(tmp_path):
    storage = Storage(tmp_path / "h.db")
    storage.create_queue("a", str(tmp_path), "s1")
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        recent = app.screen.query_one("#recent-dirs", OptionList)
        recent.highlighted = 0
        recent.action_select()
        await pilot.pause()
        assert app.screen.query_one("#launch-path", Input).value == str(tmp_path)


@pytest.mark.asyncio
async def test_resume_lists_sessions_of_path(tmp_path, monkeypatch):
    from datetime import datetime
    from ccc.transcript import SessionSummary
    monkeypatch.setattr("ccc.tui.new_conversation.list_sessions_for_directory",
                        lambda p: [SessionSummary("sid-1", datetime(2026, 10, 1, 9, 0), "fix the bug", 4)])
    # Resuming works only for a session recorded in that folder (#160).
    monkeypatch.setattr("ccc.orchestrator.list_sessions_for_directory",
                        lambda p: [SessionSummary("sid-1", datetime(2026, 10, 1, 9, 0), "fix the bug", 4)])
    monkeypatch.setattr("ccc.orchestrator.session_recorded_in", lambda session_id, path: True)
    storage = Storage(tmp_path / "h.db")
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#launch-path", Input).value = str(tmp_path)
        app.screen.query_one("#opt-session_resume", Checkbox).value = True
        await pilot.pause()
        sessions = app.screen.query_one("#resume-sessions", OptionList)
        assert sessions.display and sessions.option_count == 1
        sessions.highlighted = 0
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).session_id == "sid-1"


@pytest.mark.asyncio
async def test_auto_continue_checkbox(tmp_path):
    storage = Storage(tmp_path / "h.db")
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        box = app.screen.query_one("#auto-continue", Checkbox)
        assert box.value is True
        box.value = False
        app.screen.query_one("#launch-path", Input).value = str(tmp_path)
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).auto_continue is False


@pytest.mark.asyncio
async def test_remote_control_checkbox_default_on(tmp_path):
    from ccc.models import DeliveryMode

    storage = Storage(tmp_path / "h.db")
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#remote-control", Checkbox).value is True
        app.screen.query_one("#launch-path", Input).value = str(tmp_path)
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).delivery_mode == DeliveryMode.REMOTE_CONTROL


@pytest.mark.asyncio
async def test_remote_control_unticked_gives_interactive_cli(tmp_path):
    # Every new conversation is the real Claude Code CLI (issue #12).
    from ccc.models import DeliveryMode

    storage = Storage(tmp_path / "h.db")
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#remote-control", Checkbox).value = False
        app.screen.query_one("#launch-path", Input).value = str(tmp_path)
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).delivery_mode == DeliveryMode.INTERACTIVE


@pytest.mark.asyncio
async def test_dialog_prefills_last_choice_and_saves_it(tmp_path):
    from ccc.presets import LaunchChoice, load_last, save_last
    from ccc.launch_options import DEFAULT_SELECTION, toggle

    storage = Storage(tmp_path / "h.db")
    save_last(storage, LaunchChoice(toggle(DEFAULT_SELECTION, "model_opus", True), auto_continue=False, remote_control=False))
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#opt-model_opus", Checkbox).value is True
        assert app.screen.query_one("#auto-continue", Checkbox).value is False
        assert app.screen.query_one("#remote-control", Checkbox).value is False
        app.screen.query_one("#opt-effort_max", Checkbox).value = True
        app.screen.query_one("#launch-path", Input).value = str(tmp_path)
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    last = load_last(storage)
    assert {"model_opus", "effort_max"} <= last.selection


@pytest.mark.asyncio
async def test_preset_select_applies_and_save_as_preset(tmp_path):
    from textual.widgets import Select
    from ccc.presets import LaunchChoice, list_presets, save_preset
    from ccc.launch_options import DEFAULT_SELECTION, toggle

    storage = Storage(tmp_path / "h.db")
    save_preset(storage, "haiku-fast", LaunchChoice(toggle(DEFAULT_SELECTION, "model_haiku", True), True, False))
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#preset-select", Select).value = "haiku-fast"
        await pilot.pause()
        assert app.screen.query_one("#opt-model_haiku", Checkbox).value is True
        assert app.screen.query_one("#remote-control", Checkbox).value is False
        app.screen.query_one("#opt-effort_xhigh", Checkbox).value = True
        await pilot.pause()
        await pilot.click("#preset-save")
        await pilot.pause()
        app.screen.query_one("#preset-name", Input).value = "haiku-xhigh"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NewConversationModal)
    presets = list_presets(storage)
    assert "effort_xhigh" in presets["haiku-xhigh"].selection


@pytest.mark.asyncio
async def test_clicked_option_stays_readable(tmp_path):
    # Textual's ToggleButton:focus adds a tall border; on the dialog's
    # 1-row checkboxes it covered the row — a grey box, label gone.
    storage = Storage(tmp_path / "h.db")
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.click("#nc-more-button")  # every option is behind "+ option" (#250)
        await pilot.pause()
        app.screen.query_one("#opt-model_sonnet").scroll_visible(animate=False)
        await pilot.pause()
        await pilot.click("#opt-model_sonnet")
        await pilot.pause()
        box = app.screen.query_one("#opt-model_sonnet", Checkbox)
        assert box.has_focus and box.value
        assert box.styles.border_top[0] in ("", "none")
        assert box.content_region.height == 1


@pytest.mark.asyncio
async def test_clearing_the_preset_select_keeps_the_options(tmp_path, monkeypatch):
    # The empty choice is Select.NULL (Select.BLANK is just False in Textual 8):
    # clearing the select must return early, not look the value up as a preset.
    from textual.widgets import Select
    from ccc import presets
    from ccc.presets import LaunchChoice, save_preset
    from ccc.launch_options import DEFAULT_SELECTION, toggle

    storage = Storage(tmp_path / "h.db")
    save_preset(storage, "haiku-fast", LaunchChoice(toggle(DEFAULT_SELECTION, "model_haiku", True), True, False))
    looked_up = []

    class Recording(dict):
        def get(self, key, default=None):
            looked_up.append(key)
            return super().get(key, default)

    monkeypatch.setattr("ccc.tui.new_conversation.list_presets", lambda s: Recording(presets.list_presets(s)))
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        select = app.screen.query_one("#preset-select", Select)
        select.value = "haiku-fast"
        await pilot.pause()
        select.clear()
        await pilot.pause()
        assert select.value is Select.NULL
        assert looked_up == ["haiku-fast"]
        assert app.screen.query_one("#opt-model_haiku", Checkbox).value is True  # kept


def _claude_config(monkeypatch, tmp_path, servers):
    config = tmp_path / "claude-config"
    config.mkdir(exist_ok=True)
    (config / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))


@pytest.mark.asyncio
async def test_mcp_servers_of_the_folder_can_be_picked(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "h.db")
    project = tmp_path / "proj"; project.mkdir()
    _claude_config(monkeypatch, tmp_path, {"db": {"command": "x"}, "docs": {"command": "y"}})
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert not app.screen.query_one("#mcp-servers").display  # hidden until the mode is ticked
        app.screen.query_one("#launch-path", Input).value = str(project)
        app.screen.query_one("#opt-mcp_select", Checkbox).value = True
        await pilot.pause()
        boxes = {c.mcp_name: c for c in app.screen.query("#mcp-servers Checkbox")}
        assert sorted(boxes) == ["db", "docs"] and not any(c.value for c in boxes.values())
        boxes["docs"].value = True
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).launch_flags == ["--dangerously-skip-permissions", "--ccc-mcp=docs"]


@pytest.mark.asyncio
async def test_ticking_a_server_turns_the_mode_on_and_unticking_the_mode_clears_them(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "h.db")
    project = tmp_path / "proj"; project.mkdir()
    _claude_config(monkeypatch, tmp_path, {"db": {"command": "x"}})
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#launch-path", Input).value = str(project)
        app.screen.query_one("#opt-mcp_select", Checkbox).value = True
        await pilot.pause()
        server = app.screen.query_one("#mcp-servers Checkbox", Checkbox)
        server.value = True
        await pilot.pause()
        assert "mcp:db" in app.screen.selection
        app.screen.query_one("#opt-mcp_select", Checkbox).value = False
        await pilot.pause()
        assert not any(k.startswith("mcp") for k in app.screen.selection)
        assert not app.screen.query_one("#mcp-servers").display


@pytest.mark.asyncio
async def test_plugins_start_ticked_as_enabled_and_can_be_unticked(tmp_path, monkeypatch):
    import json
    storage = Storage(tmp_path / "h.db")
    project = tmp_path / "proj"; project.mkdir()
    cfg = tmp_path / "claude"; (cfg / "plugins").mkdir(parents=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"a@m": [{}], "b@m": [{}]}}))
    (cfg / "settings.json").write_text(json.dumps({"enabledPlugins": {"a@m": True, "b@m": True}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    results = []
    app = _host(NewConversationModal(storage, browse_root=tmp_path), results)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert not app.screen.query_one("#plugin-list").display
        app.screen.query_one("#launch-path", Input).value = str(project)
        app.screen.query_one("#opt-plugins_select", Checkbox).value = True
        await pilot.pause()
        boxes = {c.plugin_id: c for c in app.screen.query("#plugin-list Checkbox")}
        assert sorted(boxes) == ["a@m", "b@m"] and all(c.value for c in boxes.values())
        boxes["b@m"].value = False
        await pilot.pause()
        await pilot.click("#launch-start")
        await pilot.pause()
    assert storage.get_queue(results[0]).launch_flags == ["--dangerously-skip-permissions", "--ccc-plugins=a@m"]


@pytest.mark.asyncio
async def test_chosen_options_are_chips_and_a_chip_click_drops_one(tmp_path):
    # #250: the options as coloured chips with ×; "+ option" opens all of them.
    storage = Storage(tmp_path / "h.db")
    app = _host(NewConversationModal(storage, browse_root=tmp_path), [])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        dialog = app.screen
        assert not dialog.query_one("#nc-more").display
        dialog.query_one("#opt-model_opus", Checkbox).value = True
        await pilot.pause()
        dialog.query_one("#opt-effort_max", Checkbox).value = True
        await pilot.pause()
        chips = {chip.option_key: str(chip.render()) for chip in dialog.query(".nc-chip")}
        assert chips["model_opus"].startswith("Opus") and chips["effort_max"].startswith("effort max")
        await pilot.click(f"#{next(c.id for c in dialog.query('.nc-chip') if c.option_key == 'model_opus')}")
        await pilot.pause()
        assert "model_opus" not in dialog.selection
        assert "model_opus" not in {chip.option_key for chip in dialog.query(".nc-chip")}
        dialog.query_one("#nc-more-button").press()
        await pilot.pause()
        assert dialog.query_one("#nc-more").display and dialog.query_one("#dir-tree").display
        # The session field opens the options too, on the session's choice.
        dialog.action_toggle_more(False)
        dialog.query_one("#nc-session").press()
        await pilot.pause()
        assert dialog.has_class("-more") and app.focused.id == "opt-session_new"
