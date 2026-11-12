import json

import pytest
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from ccc.tui import footer_strip


def test_tmux_keys():
    assert footer_strip.tmux_key("ctrl+q") == "C-q"
    assert footer_strip.tmux_key("question_mark") == "?"
    assert footer_strip.tmux_key("slash") == "/"
    assert footer_strip.tmux_key("t") == "t"
    assert footer_strip.tmux_key("ctrl+down") == "C-Down"
    assert footer_strip.tmux_key("alt+0") == "M-0"
    assert footer_strip.tmux_key("ctrl+right_square_bracket") == "C-]"


@pytest.mark.asyncio
async def test_footer_items_are_what_the_footer_shows():
    class S(Screen):
        BINDINGS = [Binding("t", "tasks", "Tasks"), Binding("x", "hidden", "Hidden", show=False)]

        def action_tasks(self):
            pass

        def action_hidden(self):
            pass

    class A(App):
        BINDINGS = [Binding("ctrl+q", "quit", "Close")]

        def on_mount(self):
            self.push_screen(S())

    app = A()
    async with app.run_test() as pilot:
        await pilot.pause()
        items = footer_strip.footer_items(app)
    assert ["t", "Tasks", "t"] in items
    assert any(i[1] == "Close" and i[2] == "C-q" for i in items)
    assert not any(i[1] == "Hidden" for i in items)


def test_decode_ignores_garbage():
    assert footer_strip.decode("not json") == []
    assert footer_strip.decode(json.dumps([["t", "Tasks", "t"], "x"])) == [["t", "Tasks", "t"]]


@pytest.mark.asyncio
async def test_strip_shows_published_keys_and_clicks_press_them(monkeypatch):
    sent = []
    value = footer_strip.encode([["?", "Keys", "?"], ["t", "Tasks", "t"]])

    def tmux(*args):
        sent.append(args)
        import subprocess
        return subprocess.CompletedProcess(args, 0, value if args[0] == "show-options" else "", "")

    monkeypatch.setattr(footer_strip, "_tmux", tmux)
    app = footer_strip.FooterStrip("%0")
    async with app.run_test(size=(100, 1)) as pilot:
        await pilot.pause()
        items = list(app.query(".strip-item"))
        assert [str(i.render()) for i in items] == ["? keys", "t tasks"]  # lower case, as in the design (#250)
        await pilot.click(items[1])
        await pilot.pause()
    assert ("send-keys", "-t", "%0", "t") in sent and ("select-pane", "-t", "%0") in sent


@pytest.mark.asyncio
async def test_click_goes_through_the_client_like_a_typed_key(monkeypatch):
    sent = []
    value = footer_strip.encode([["Alt+0", "Claude ↔ ccc", "M-0"]])

    def tmux(*args):
        sent.append(args)
        import subprocess
        out = {"show-options": value, "display-message": "/dev/pts/3\n"}.get(args[0], "")
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(footer_strip, "_tmux", tmux)
    app = footer_strip.FooterStrip("%0")
    async with app.run_test(size=(100, 1)) as pilot:
        await pilot.pause()
        await pilot.click(list(app.query(".strip-item"))[0])
        await pilot.pause()
    # Through tmux's key tables: its Alt+0 binding switches to Claude.
    assert ("send-keys", "-K", "-c", "/dev/pts/3", "M-0") in sent


def test_wrap_splits_keys_into_rows_that_fit():
    items = [["a", "Alpha", "a"], ["b", "Beta", "b"], ["c", "Gamma", "c"]]
    assert footer_strip.wrap(items, 100) == [items]
    assert footer_strip.wrap(items, 17) == [items[:2], items[2:]]
    assert footer_strip.wrap([], 10) == [[]]


@pytest.mark.asyncio
async def test_hover_goes_away_when_the_mouse_stops_reporting(monkeypatch):
    import subprocess

    value = footer_strip.encode([["?", "Keys", "?"], ["t", "Tasks", "t"]])
    monkeypatch.setattr(footer_strip, "_tmux", lambda *a: subprocess.CompletedProcess(a, 0, value, ""))
    monkeypatch.setattr(footer_strip, "HOVER_IDLE_SECONDS", 0.2)
    app = footer_strip.FooterStrip("%0")
    async with app.run_test(size=(100, 1)) as pilot:
        await pilot.pause()
        item = list(app.query(".strip-item"))[1]
        await pilot.hover(item)
        await pilot.pause()
        assert item.has_class("-hover")
        await pilot.pause(0.6)
        assert not item.has_class("-hover")


@pytest.mark.asyncio
async def test_strip_starts_with_the_screens_name(monkeypatch):
    import subprocess

    value = footer_strip.encode([["?", "Help", "?"]], "Dashboard")
    assert footer_strip.decode(value) == [["?", "Help", "?"]]
    assert footer_strip.decode_title(value) == "Dashboard"
    monkeypatch.setattr(footer_strip, "_tmux", lambda *a: subprocess.CompletedProcess(a, 0, value, ""))
    app = footer_strip.FooterStrip("%0")
    async with app.run_test(size=(100, 1)) as pilot:
        await pilot.pause()
        assert str(app.query_one(".strip-title").render()) == "DASHBOARD"
