"""The terminal widget: runs a command in a pty and draws its screen."""
import asyncio
import sys

import pytest
from textual.app import App

from ccc.tui.terminal_pane import ByteStream, Screen, TerminalPane, color, encode_key, safe_paste


class Host(App):
    def __init__(self, command):
        super().__init__()
        self.command = command
        self.events = []

    def compose(self):
        yield TerminalPane(self.command, id="term")

    def on_terminal_pane_exited(self, message):
        self.events.append(("exited", message.returncode))

    def on_terminal_pane_released(self, message):
        self.events.append(("released",))


async def wait_for(pane, text, seconds=5):
    for _ in range(int(seconds * 50)):
        if text in pane.text():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{text!r} not on screen:\n{pane.text()}")


def test_colours_and_keys():
    assert color("default") is None and color("brown") == "yellow"
    assert color("brightgreen") == "bright_green" and color("5fd7af") == "#5fd7af"
    assert encode_key("ctrl+c", None) == "\x03" and encode_key("up", None) == "\x1b[A"
    assert encode_key("a", "a") == "a" and encode_key("alt+x", None) == "\x1bx"
    assert encode_key("ctrl+right_square_bracket", None) is None


@pytest.mark.asyncio
async def test_output_is_drawn_with_colours():
    app = Host(["sh", "-c", r"printf '\033[31mred\033[0m plain\n\033[38;2;1;2;3mtrue\033[0m'; sleep 5"])
    async with app.run_test(size=(40, 6)) as pilot:
        pane = app.query_one(TerminalPane)
        await wait_for(pane, "red plain")
        await wait_for(pane, "true")
        await pilot.pause(0.2)
        first = pane.render_line(0)
        styles = {seg.text: seg.style for seg in first if seg.text.strip()}
        assert styles["red"].color.name == "red"
        assert pane.render_line(1).text.startswith("true")


@pytest.mark.asyncio
async def test_keys_and_paste_reach_the_command_and_it_is_resized():
    app = Host(["sh", "-c", "stty -echo; while read l; do echo \"got:$l size:$(stty size)\"; done"])
    async with app.run_test(size=(50, 8)) as pilot:
        pane = app.query_one(TerminalPane)
        pane.focus()
        await pilot.pause(0.3)
        await pilot.press("h", "i", "enter")
        await wait_for(pane, "got:hi size:8 50")
        await pilot.resize_terminal(60, 10)
        await pilot.pause(0.3)
        await pilot.press("x", "enter")
        await wait_for(pane, "got:x size:10 60")


@pytest.mark.asyncio
async def test_release_key_and_exit_are_reported():
    app = Host(["sh", "-c", "sleep 0.5"])
    async with app.run_test(size=(40, 6)) as pilot:
        pane = app.query_one(TerminalPane)
        pane.focus()
        await pilot.pause(0.2)
        await pilot.press("ctrl+right_square_bracket")
        await pilot.pause(0.2)
        assert ("released",) in app.events
        for _ in range(100):
            if any(e[0] == "exited" for e in app.events):
                break
            await asyncio.sleep(0.05)
        assert ("exited", 0) in app.events


@pytest.mark.asyncio
async def test_private_csi_sequences_do_not_change_the_text_style():
    # Claude Code sends CSI > 4;2 m at the start; pyte would turn it into underline.
    app = Host(["sh", "-c", r"printf '\033[>4;2m\033[>1ux'; printf 'ab\033'; sleep 0.2; printf '[>4;2mcd'; sleep 5"])
    async with app.run_test(size=(30, 4)) as pilot:
        pane = app.query_one(TerminalPane)
        await wait_for(pane, "abcd")
        await pilot.pause(0.1)
        assert all(seg.style.underline is None for seg in pane.render_line(0))


def reader(extra=""):
    """A command that echoes what it receives, readably, a line per chunk, after `extra`."""
    code = (
        "import os,sys,termios,tty;tty.setraw(0);"
        + extra +
        "sys.stdout.write('ready\\r\\n');sys.stdout.flush()\n"
        "while True:\n"
        " d=os.read(0,100)\n"
        " sys.stdout.write('rx:'+repr(d.decode())+'\\r\\n');sys.stdout.flush()\n"
    )
    return [sys.executable, "-c", code]


@pytest.mark.asyncio
async def test_application_cursor_keys_and_focus_events_follow_the_commands_modes():
    app = Host(reader("sys.stdout.write('\\x1b[?1h\\x1b[?1004h');"))
    async with app.run_test(size=(60, 8)) as pilot:
        pane = app.query_one(TerminalPane)
        await wait_for(pane, "ready")
        pane.focus()
        await pilot.pause(0.2)
        await pilot.press("up")
        await wait_for(pane, "\\x1bOA")


@pytest.mark.asyncio
async def test_mouse_goes_to_the_command_when_it_asks_for_it():
    app = Host(reader("sys.stdout.write('\\x1b[?1000h\\x1b[?1006h');"))
    async with app.run_test(size=(60, 8)) as pilot:
        pane = app.query_one(TerminalPane)
        await wait_for(pane, "ready")
        await pilot.pause(0.2)
        await pilot.click(TerminalPane, offset=(4, 2))
        await wait_for(pane, "\\x1b[<0;5;3M")
        await wait_for(pane, "\\x1b[<0;5;3m")
        await pilot.mouse_down(TerminalPane, offset=(1, 1), shift=False)
        await pilot.hover(TerminalPane, offset=(1, 1))
        pane.post_message(__import__("textual.events", fromlist=["x"]).MouseScrollUp(
            pane, 3, 1, 0, 0, 0, False, False, False, screen_x=3, screen_y=1))
        await wait_for(pane, "\\x1b[<64;4;2M")


@pytest.mark.asyncio
async def test_dragging_selects_text_and_copies_it():
    app = Host(["sh", "-c", "printf 'hello world\\nsecond line'; sleep 5"])
    copied = []
    async with app.run_test(size=(40, 6)) as pilot:
        pane = app.query_one(TerminalPane)
        app.copy_to_clipboard = copied.append
        await wait_for(pane, "second line")
        await pilot.pause(0.2)
        await pilot.mouse_down(TerminalPane, offset=(6, 0))
        await pilot.hover(TerminalPane, offset=(5, 1))
        await pilot.mouse_up(TerminalPane, offset=(5, 1))
        await pilot.pause(0.1)
        assert copied == ["world\nsecond"]
        assert pane.render_line(0).text.startswith("hello world")
        assert any(seg.style.reverse for seg in pane.render_line(0))


@pytest.mark.asyncio
async def test_ctrl_shift_c_copies_the_selection_and_never_reaches_the_command():
    app = Host(["sh", "-c", "printf 'hello world'; sleep 5"])
    copied = []
    async with app.run_test(size=(40, 6)) as pilot:
        pane = app.query_one(TerminalPane)
        app.copy_to_clipboard = copied.append
        await wait_for(pane, "hello world")
        await pilot.pause(0.2)
        await pilot.press("ctrl+shift+c")  # no selection: nothing
        assert copied == []
        pane._selection = ((0, 0), (4, 0))
        await pilot.press("ctrl+shift+c")
        assert copied == ["hello"]


@pytest.mark.asyncio
async def test_clipboard_sequence_from_the_command_reaches_the_clipboard():
    app = Host(["sh", "-c", r"printf '\033]52;c;aGVsbG8=\007'; printf 'ok'; sleep 5"])
    copied = []
    async with app.run_test(size=(40, 6)) as pilot:
        app.copy_to_clipboard = copied.append
        await wait_for(app.query_one(TerminalPane), "ok")
        assert copied == ["hello"]


@pytest.mark.asyncio
async def test_ccc_keys_are_not_taken_by_the_terminal():
    class Keys(Host):
        BINDINGS = [("alt+1", "preset", "preset")]

        def compose(self):
            yield TerminalPane(self.command, passthrough={"alt+1"}, id="term")

        def action_preset(self):
            self.events.append(("preset",))

    app = Keys(reader())
    async with app.run_test(size=(40, 6)) as pilot:
        pane = app.query_one(TerminalPane)
        await wait_for(pane, "ready")
        pane.focus()
        await pilot.pause(0.2)
        await pilot.press("alt+1", "a")
        await wait_for(pane, "rx:'a'")
        assert ("preset",) in app.events and "alt" not in pane.text()


def test_unknown_colours_fall_back_and_paste_loses_control_characters():
    assert color("brightbrown") == "bright_yellow" and color("nonsense") is None
    assert color("brightnonsense") is None and color("zzzzzz") is None
    assert safe_paste("a\x1b[201~b\x1b]52;c;x\x07\tc\r\nd\x9bq") == "a[201~b]52;c;x\tc\r\ndq"


def test_repeat_and_scroll_sequences_of_a_tmux_client():
    """REP and SU/SD, which plain pyte ignores (#278)."""
    screen = Screen(10, 4)
    stream = ByteStream(screen)
    stream.feed(b"a\x1b[4b")
    assert screen.display[0].rstrip() == "aaaaa"
    screen = Screen(5, 4)
    stream = ByteStream(screen)
    stream.feed(b"1\r\n2\r\n3\r\n4\x1b[1;1H\x1b[2S")
    assert [line.strip() for line in screen.display] == ["3", "4", "", ""]
    assert (screen.cursor.y, screen.cursor.x) == (0, 0)
    stream.feed(b"\x1b[1T")
    assert [line.strip() for line in screen.display] == ["", "3", "4", ""]
