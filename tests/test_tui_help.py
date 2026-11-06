import pytest
from textual.app import App

from ccc.storage import Storage
from ccc.tui.prompt_presets import ALT_DIGIT_CHARS, ALT_ZERO_CHAR
from ccc.tui.dashboard import DashboardScreen
from ccc import keymap
from ccc.tui.help import KEY_HELP, HelpScreen, keys_markdown


def _ccc_bindings():
    """Every Binding in ccc's TUI classes."""
    import importlib
    import inspect
    import pkgutil

    import ccc.tui

    for info in pkgutil.iter_modules(ccc.tui.__path__):
        module = importlib.import_module(f"ccc.tui.{info.name}")
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__:
                yield from cls.__dict__.get("BINDINGS", [])


def test_every_binding_is_a_documented_keymap_action():
    # Every key can be changed (keymap ids) and is listed in the help;
    # only Alt+digit as some terminals send it (a character) rides along with Alt+N / Alt+0.
    documented = {ref for _section, entries in KEY_HELP for ref, _ in entries}
    for binding in _ccc_bindings():
        if binding.id is None:
            assert binding.key in (*ALT_DIGIT_CHARS.values(), ALT_ZERO_CHAR), binding
            continue
        assert binding.id in keymap.BY_ID, binding
        assert binding.key == keymap.BY_ID[binding.id].default, binding
        assert binding.id in documented, binding
    assert set(keymap.BY_ID) <= documented


def test_help_shows_the_users_keys():
    keys = {**keymap.defaults(), "views.analytics": "f5"}
    assert "| `F5` | Analytics |" in keys_markdown(keys)
    assert "| `a` | Analytics |" in keys_markdown()


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


@pytest.mark.asyncio
async def test_question_mark_opens_the_guide_on_this_screens_topic(tmp_path):
    app = _host(DashboardScreen(Storage(tmp_path / "h.db")))
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        assert app.screen.topic == "dashboard"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_guide_switches_topics_and_follows_links(tmp_path):
    from textual.widgets import Markdown, OptionList

    app = _host(HelpScreen("getting-started"))
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        help_screen = app.screen
        assert help_screen.query_one(OptionList).has_focus
        await pilot.press("down")
        await pilot.pause()
        assert help_screen.topic == "dashboard"
        markdown = help_screen.query_one("#help-text", Markdown)
        assert "Queue detail" in markdown.source
        assert "png" not in markdown.source  # screenshots are for GitHub

        markdown.post_message(Markdown.LinkClicked(markdown, "tasks.md"))
        await pilot.pause()
        assert help_screen.topic == "tasks"

        help_screen.query_one(OptionList).highlighted = len(help_screen.query_one(OptionList).options) - 1
        await pilot.pause()
        assert help_screen.topic == "keys"
        assert "Ctrl+J" in markdown.source and "Ctrl+]" in markdown.source
