import pytest
from textual.widgets import Checkbox, DataTable, Input, TabbedContent, TextArea

from ccc.prompt_presets import PromptPreset, load_prompt_presets, save_prompt_presets
from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.message_box import MessageBox
from ccc.tui.prompt_presets import PromptPresetModal
from ccc.tui.settings import SettingsModal


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path, presets=()):
    storage = Storage(tmp_path / "history.db")
    queue = storage.create_queue("proj", str(tmp_path), "sess-1")
    save_prompt_presets(storage, list(presets))
    return CCContinueApp(str(tmp_path / "history.db")), queue.id


async def _conversations(pilot, app):
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, ConversationsScreen)
    return app.screen


def _pending_texts(storage, queue_id):
    return [i.prompt_text for i in storage.list_pending_items(queue_id)]


PRESETS = [PromptPreset("Go on", "Pokračuj.", True), PromptPreset("File", "Zkontroluj soubor: ", False)]


@pytest.mark.asyncio
async def test_bar_shows_presets_with_mode(tmp_path):
    app, _ = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        bar = screen.query_one("#prompt-presets-bar")
        assert bar.display
        labels = [str(w.render()) for w in bar.query(".prompt-preset-item")]
        assert labels == ["Alt+1 ⏎ Go on", "Alt+2 ✎ File"]


@pytest.mark.asyncio
async def test_bar_hidden_without_presets(tmp_path):
    app, _ = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        assert not screen.query_one("#prompt-presets-bar").display


@pytest.mark.asyncio
async def test_insert_preset_goes_to_cursor_and_is_not_sent(tmp_path):
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        box = screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press("a", "b")
        box.move_cursor((0, 1))
        await pilot.press("alt+2")
        await pilot.pause()
        assert box.text == "aZkontroluj soubor: b"
        assert _pending_texts(app.storage, qid) == []


@pytest.mark.asyncio
async def test_alt_digit_as_sent_by_tmux_and_termux_works(tmp_path):
    # Esc+1 arrives as "¡" (Textual's mapping), not as "alt+1".
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        screen.query_one("#message-input", MessageBox).focus()
        await pilot.press("¡")
        await pilot.pause()
        assert _pending_texts(app.storage, qid) == ["Pokračuj."]
        assert screen.query_one("#message-input", MessageBox).text == ""


@pytest.mark.asyncio
async def test_send_preset_queues_it_and_keeps_the_draft(tmp_path):
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        box = screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press("x", "y")
        await pilot.press("alt+1")
        await pilot.pause()
        assert _pending_texts(app.storage, qid) == ["Pokračuj."]
        assert box.text == "xy"


@pytest.mark.asyncio
async def test_click_on_bar_item_works_like_the_key(tmp_path):
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        await _conversations(pilot, app)
        await pilot.click("#prompt-preset-1")
        await pilot.pause()
        assert _pending_texts(app.storage, qid) == ["Pokračuj."]


@pytest.mark.asyncio
async def test_missing_preset_only_notifies(tmp_path):
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        await _conversations(pilot, app)
        await pilot.press("alt+5")
        await pilot.pause()
        assert _pending_texts(app.storage, qid) == []
        assert any("Alt+5" in str(n.message) for n in app._notifications)


async def _fill_editor(pilot, app, name, text, send_now):
    modal = app.screen
    assert isinstance(modal, PromptPresetModal)
    modal.query_one("#preset-name", Input).value = name
    modal.query_one("#preset-text", TextArea).text = text
    modal.query_one("#preset-send-now", Checkbox).value = send_now
    await pilot.press("ctrl+s")
    await pilot.pause()


@pytest.mark.asyncio
async def test_settings_add_edit_move_delete_saved_only_on_save(tmp_path):
    app, _ = _app(tmp_path)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        settings = app.screen
        assert isinstance(settings, SettingsModal)

        settings._add_prompt_preset()
        await pilot.pause()
        await _fill_editor(pilot, app, "One", "first", False)
        settings._add_prompt_preset()
        await pilot.pause()
        await _fill_editor(pilot, app, "Two", "second", True)
        assert [p.name for p in settings._prompt_presets] == ["One", "Two"]
        assert load_prompt_presets(app.storage) == []  # not saved yet

        settings._move_prompt_preset(-1)  # cursor is on "Two"
        assert [p.name for p in settings._prompt_presets] == ["Two", "One"]
        table = settings.query_one("#settings-prompt-presets", DataTable)
        assert table.cursor_row == 0
        settings._edit_prompt_preset()
        await pilot.pause()
        await _fill_editor(pilot, app, "Two!", "second", True)
        table.move_cursor(row=1)
        settings._delete_prompt_preset()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert load_prompt_presets(app.storage) == [PromptPreset("Two!", "second", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 140])
async def test_settings_preset_table_fits_the_row_with_ellipsis(tmp_path, width):
    # No horizontal scrolling or hover-to-reveal: fixed columns, the
    # prompt is cut with "…" to the space left (full text is in Edit).
    long = PromptPreset("A rather long preset name", "word " * 60 + "\nsecond line", True)
    app, _ = _app(tmp_path, [long])
    async with app.run_test(size=(width, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "settings-tab-presets"
        await pilot.pause()
        table = app.screen.query_one("#settings-prompt-presets", DataTable)
        assert table.max_scroll_x == 0
        key, _mode, name, prompt = (str(cell) for cell in table.get_row_at(0))
        assert key == "Alt+1"
        assert name.endswith("…") and len(name) == 16
        assert prompt.startswith("word word") and prompt.endswith("…")


@pytest.mark.asyncio
async def test_settings_cancel_discards_preset_changes(tmp_path):
    app, _ = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        app.screen._delete_prompt_preset()
        await pilot.press("escape")
        await pilot.pause()
        assert load_prompt_presets(app.storage) == PRESETS


@pytest.mark.asyncio
async def test_editor_requires_name_and_text(tmp_path):
    app, _ = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        results = []
        app.push_screen(PromptPresetModal(), results.append)
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, PromptPresetModal) and results == []


@pytest.mark.asyncio
async def test_bar_updates_after_settings_save(tmp_path):
    app, _ = _app(tmp_path)
    async with app.run_test(size=(140, 60)) as pilot:
        screen = await _conversations(pilot, app)
        await pilot.press("ctrl+p")
        await pilot.pause()
        app.screen._prompt_presets = [PromptPreset("New", "hi", False)]
        await pilot.press("ctrl+s")
        await pilot.pause()
        bar = screen.query_one("#prompt-presets-bar")
        assert bar.display and [str(w.render()) for w in bar.query(".prompt-preset-item")] == ["Alt+1 ✎ New"]


@pytest.mark.asyncio
async def test_section_sign_is_typed_when_no_preset_on_6(tmp_path):
    # "§" is a plain key on the Czech keyboard and also what Alt+6 becomes.
    app, qid = _app(tmp_path, PRESETS)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        box = screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press("§")
        await pilot.pause()
        assert box.text == "§"
        assert _pending_texts(app.storage, qid) == []


@pytest.mark.asyncio
async def test_alt_zero_character_switches_to_claude_only_while_it_is_open(tmp_path):
    # Esc+0 arrives as "º": with Claude Code open beside ccc it is Alt+0, not text.
    app, _ = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        screen = await _conversations(pilot, app)
        calls = []
        screen.action_focus_claude = lambda: calls.append(1)
        box = screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press("º")
        assert calls == [] and box.text == "º"  # closed: typed as usual
        screen._sync_cli_pane = lambda: None  # no real Claude Code in the test
        screen.add_class("-cli-open")
        await pilot.pause()
        await pilot.press("º")
        assert calls == [1] and box.text == "º"
