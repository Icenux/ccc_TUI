"""A terminal inside ccc: runs a command in a pseudo-terminal and draws its
screen as a widget, so Claude Code can sit in a frame ccc draws itself
(proof of concept for issue #133 — instead of a tmux pane next to ccc).

The command's output goes through pyte (a terminal emulator); the widget
renders pyte's screen line by line, and sends the keys, pastes and the size
back to the pty — the mouse too (when the command asks for it; the wheel
then scrolls what the command shows, e.g. a tmux client's history), a
selection of your own (drag; Shift+drag when the command takes the mouse)
that goes to the clipboard on release, and the clipboard sequence (OSC 52)
the command prints. Scrollback is the command's: this is a window onto
a terminal that redraws in place (a tmux client)."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import termios

import pyte
from rich.color import ANSI_COLOR_NAMES
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

# pyte names → Rich's; pyte calls yellow "brown" and writes bright ones "brightred".
_NAMES = {"brown": "yellow"}
# Textual key names → what a terminal sends.
_KEYS = {
    "enter": "\r", "tab": "\t", "escape": "\x1b", "backspace": "\x7f", "space": " ",
    "shift+tab": "\x1b[Z", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "home": "\x1b[H", "end": "\x1b[F", "pageup": "\x1b[5~", "pagedown": "\x1b[6~",
    "delete": "\x1b[3~", "insert": "\x1b[2~",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS", "f5": "\x1b[15~",
    "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~", "f9": "\x1b[20~", "f10": "\x1b[21~",
    "f11": "\x1b[23~", "f12": "\x1b[24~",
    "ctrl+up": "\x1b[1;5A", "ctrl+down": "\x1b[1;5B", "ctrl+right": "\x1b[1;5C", "ctrl+left": "\x1b[1;5D",
    "shift+up": "\x1b[1;2A", "shift+down": "\x1b[1;2B", "shift+right": "\x1b[1;2C", "shift+left": "\x1b[1;2D",
    "alt+enter": "\x1b\r", "shift+enter": "\x1b\r", "alt+backspace": "\x1b\x7f",
}


class Screen(pyte.Screen):
    """pyte's screen plus what a tmux client uses and pyte lacks: REP
    (CSI n b — repeat the last character) and SU/SD (CSI n S / T — scroll
    the region). Without them characters go missing and lines are drawn
    twice or shifted (#278)."""

    _last_char = ""

    def draw(self, data: str) -> None:
        super().draw(data)
        if data:
            self._last_char = data[-1]

    def repeat(self, count: int = 0) -> None:
        if self._last_char:
            super().draw(self._last_char * (count or 1))

    def _scroll(self, count: int, up: bool) -> None:
        top, bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        x, y = self.cursor.x, self.cursor.y
        self.cursor.y = bottom if up else top
        for _ in range(min(count or 1, self.lines)):
            (self.index if up else self.reverse_index)()
        self.cursor.x, self.cursor.y = x, y

    def scroll_up(self, count: int = 0) -> None:
        self._scroll(count, up=True)

    def scroll_down(self, count: int = 0) -> None:
        self._scroll(count, up=False)


class ByteStream(pyte.ByteStream):
    csi = {**pyte.ByteStream.csi, "b": "repeat", "S": "scroll_up", "T": "scroll_down"}
    events = pyte.ByteStream.events | {"repeat", "scroll_up", "scroll_down"}


# CSI with a "private" prefix (> = <): pyte reads CSI > 4;2 m (xterm's modifyOtherKeys)
# as "underline" and CSI > 1 u (the kitty keyboard protocol) as "restore cursor".
_PRIVATE_CSI = re.compile(rb"\x1b\[[>=<][0-9;:]*[ -/]*[@-~]")
_PARTIAL_CSI = re.compile(rb"\x1b(\[[>=<0-9;:?]*[ -/]*)?$")


_OSC52 = re.compile(rb"\x1b\]52;[a-z]*;([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)")
_MODES = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")
# More than this goes nowhere near a clipboard (a runaway output).
MAX_COPY_BYTES = 1_000_000
MOUSE_MODES = {1000, 1002, 1003}


def color(value: str) -> str | None:
    """pyte's colour (a name, "default" or "rrggbb") as a Rich colour; None
    (the terminal's own) for anything that isn't one — the output is the
    command's, and a bad name must not break drawing."""
    if value == "default":
        return None
    if len(value) == 6 and all(c in "0123456789abcdef" for c in value):
        return f"#{value}"
    value = _NAMES.get(value, value)
    if value.startswith("bright"):
        value = "bright_" + _NAMES.get(value[6:], value[6:])
    return value if value in ANSI_COLOR_NAMES else None


def safe_paste(text: str) -> str:
    """Pasted text without control characters (but tab and line ends), so
    it can't close the bracketed paste and type commands into the program."""
    return "".join(c for c in text if c in "\t\n\r" or (c.isprintable() and c != "\x7f"))


def encode_key(key: str, character: str | None, application_cursor: bool = False) -> str | None:
    """The bytes a terminal sends for a Textual key; None if there are none.
    `application_cursor`: the command switched the arrows to ESC O x (DECCKM)."""
    if application_cursor and key in ("up", "down", "right", "left", "home", "end"):
        return "\x1bO" + _KEYS[key][-1]
    if key in _KEYS:
        return _KEYS[key]
    if key.startswith("ctrl+") and len(key) == 6 and key[-1].isalpha():
        return chr(ord(key[-1]) - 96)
    if key == "ctrl+@" or key == "ctrl+space":
        return "\x00"
    if key.startswith("alt+") and len(key) == 5:
        return "\x1b" + key[-1]
    if character and character.isprintable():
        return character
    return None


class TerminalPane(Widget, can_focus=True):
    """Runs `command` in a pty and shows it. While focused every key goes
    to the command, except `release_key`, which posts `Released` (the way
    back to ccc)."""

    DEFAULT_CSS = """
    TerminalPane {
        width: 1fr;
        height: 1fr;
    }
    """

    class Released(Message):
        """The release key was pressed."""

    class Exited(Message):
        """The command ended."""

        def __init__(self, returncode: int | None) -> None:
            super().__init__()
            self.returncode = returncode

    def __init__(self, command: list[str], *, release_key: str = "ctrl+right_square_bracket",
                 passthrough: frozenset[str] | set[str] = frozenset(),
                 env: dict[str, str] | None = None,
                 copy_sequence: str = "", cancel_sequence: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.command = command
        self.release_key = release_key
        # Keys that are ccc's, not the command's: they go on to the screen's bindings.
        self.passthrough = frozenset(passthrough)
        self.modes: set[int] = set()  # DEC private modes the command has switched on
        self._selection: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._selecting = False
        # The command's own selection (a tmux client's copy mode, made with
        # the mouse we hand on): copy_sequence copies it (Ctrl+Shift+C
        # without a selection of ours), cancel_sequence leaves it before the
        # next key typed or pasted, so that goes to the program again.
        self.copy_sequence = copy_sequence
        self.cancel_sequence = cancel_sequence
        self._command_selection = False
        self.env = env
        self.screen_ = Screen(80, 24)
        self._stream = ByteStream(self.screen_)
        self._fd: int | None = None
        self._process: subprocess.Popen | None = None
        self._bracketed = False
        self._pending = b""  # the start of an escape sequence cut by a read
        self._dirty = False

    # -- the process --------------------------------------------------

    def start(self) -> None:
        """Start the command at the widget's size (once it has one)."""
        if self._process is not None:
            return
        columns, rows = max(2, self.size.width), max(1, self.size.height)
        master, slave = pty.openpty()
        self._set_size(master, rows, columns)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", **(self.env or {})}
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        try:
            self._process = subprocess.Popen(
                self.command, stdin=slave, stdout=slave, stderr=slave, env=env,
                start_new_session=True,
                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        finally:
            os.close(slave)
        self._fd = master
        os.set_blocking(master, False)
        self.screen_.resize(rows, columns)
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(master, self._readable)

    def stop(self) -> None:
        fd, process = self._fd, self._process
        self._fd = self._process = None
        if fd is not None:
            try:
                self._loop.remove_reader(fd)
            except Exception:  # the loop may be gone already
                pass
            os.close(fd)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)

    def _readable(self) -> None:
        assert self._fd is not None
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            code = self._process.poll() if self._process else None
            self.stop()
            self.post_message(self.Exited(code))
            return
        data = self._pending + data
        partial = _PARTIAL_CSI.search(data)
        self._pending = data[partial.start():] if partial else b""
        data = _PRIVATE_CSI.sub(b"", data[:partial.start()] if partial else data)
        for match in _MODES.finditer(data):
            for number in match.group(1).split(b";"):
                if number.isdigit():
                    (self.modes.add if match.group(2) == b"h" else self.modes.discard)(int(number))
        self._bracketed = 2004 in self.modes
        for match in _OSC52.finditer(data):
            self._copy_from_command(match.group(1))
        self._stream.feed(data)
        self._request_refresh()

    def _copy_from_command(self, encoded: bytes) -> None:
        """The command asked for the clipboard (OSC 52): a tmux client does
        that for tmux's own copies (selecting in Claude's history)."""
        if not encoded or len(encoded) > MAX_COPY_BYTES:
            return
        try:
            text = base64.b64decode(encoded, validate=True).decode("utf-8", "replace")
        except ValueError:
            return
        self.app.copy_to_clipboard(text)

    def _request_refresh(self) -> None:
        # Many reads can arrive within a frame: one repaint is enough.
        if not self._dirty:
            self._dirty = True
            self.set_timer(1 / 60, self._repaint)

    def _repaint(self) -> None:
        self._dirty = False
        self.refresh()

    @staticmethod
    def _set_size(fd: int, rows: int, columns: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def write(self, text: str) -> None:
        """Send text to the command as if typed."""
        if self._fd is not None:
            try:
                os.write(self._fd, text.encode())
            except OSError:
                pass

    # -- the widget ---------------------------------------------------

    def on_mount(self) -> None:
        self.call_after_refresh(self.start)

    def on_unmount(self) -> None:
        self.stop()

    def on_resize(self, event: events.Resize) -> None:
        columns, rows = max(2, event.size.width), max(1, event.size.height)
        if (columns, rows) != (self.screen_.columns, self.screen_.lines):
            self.screen_.resize(rows, columns)
            if self._fd is not None:
                self._set_size(self._fd, rows, columns)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        screen = self.screen_
        width = self.size.width
        if y >= screen.lines:
            return Strip.blank(width)
        line = screen.buffer[y]
        cursor_x = screen.cursor.x if (self.has_focus and not screen.cursor.hidden and y == screen.cursor.y) else -1
        segments: list[Segment] = []
        run, run_style = "", None
        for x in range(min(width, screen.columns)):
            char = line[x]
            fg, bg = color(char.fg), color(char.bg)
            reverse = bool(char.reverse) ^ (x == cursor_x) ^ self._is_selected(x, y)
            style = Style(color=fg, bgcolor=bg, bold=char.bold or None, italic=char.italics or None,
                          underline=char.underscore or None, strike=char.strikethrough or None,
                          reverse=reverse or None)
            if style != run_style and run:
                segments.append(Segment(run, run_style))
                run = ""
            run_style = style
            run += char.data or " "
        if run:
            segments.append(Segment(run, run_style))
        return Strip(segments).adjust_cell_length(width)

    def text(self) -> str:
        """The screen as plain text (for tests)."""
        return "\n".join(line.rstrip() for line in self.screen_.display)

    # -- input --------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if event.key == self.release_key:
            event.stop()
            event.prevent_default()
            self.post_message(self.Released())
            return
        if event.key == "ctrl+shift+c":
            # Copies the selection; without one it does nothing (never
            # Ctrl+C = interrupt to the command).
            event.stop()
            event.prevent_default()
            text = self.selected_text()
            if text.strip():
                self.app.copy_to_clipboard(text)
            elif self.copy_sequence:
                self._command_selection = False
                self.write(self.copy_sequence)
            return
        if event.key in self.passthrough:
            return  # ccc's own key: the screen's bindings take it
        data = encode_key(event.key, event.character, application_cursor=1 in self.modes)
        if data is not None:
            event.stop()
            event.prevent_default()
            self._selection = None
            self._leave_command_selection()
            self.write(data)

    def _leave_command_selection(self) -> None:
        if self._command_selection and self.cancel_sequence:
            self.write(self.cancel_sequence)
        self._command_selection = False

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        self._leave_command_selection()
        text = safe_paste(event.text)
        self.write(f"\x1b[200~{text}\x1b[201~" if self._bracketed else text.replace("\n", "\r"))

    def on_focus(self) -> None:
        if 1004 in self.modes:
            self.write("\x1b[I")
        self.refresh()

    def on_blur(self) -> None:
        if 1004 in self.modes:
            self.write("\x1b[O")
        self.refresh()

    # -- the mouse ----------------------------------------------------

    def _mouse_taken(self, event) -> bool:
        """The command wants mouse events (and Shift isn't held to select)."""
        return bool(self.modes & MOUSE_MODES) and not event.shift

    def _send_mouse(self, button: int, event, release: bool = False, motion: bool = False) -> None:
        code = button + (4 if event.shift else 0) + (8 if event.meta else 0) + (16 if event.ctrl else 0)
        code += 32 if motion else 0
        x, y = event.x + 1, event.y + 1
        if 1006 in self.modes:
            self.write(f"\x1b[<{code};{x};{y}{'m' if release else 'M'}")
        elif x < 224 and y < 224:  # the old encoding runs out of numbers
            self.write(f"\x1b[M{chr(32 + (3 if release else code))}{chr(32 + x)}{chr(32 + y)}")

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._mouse_taken(event):
            event.stop()
            self._send_mouse(64, event)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._mouse_taken(event):
            event.stop()
            self._send_mouse(65, event)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        self.focus()
        if self._mouse_taken(event):
            self._send_mouse(event.button - 1, event)
            return
        if event.button == 1:
            self._selection = ((event.x, event.y), (event.x, event.y))
            self._selecting = True
            self.capture_mouse()
            self.refresh()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._selecting and self._selection is not None:
            event.stop()
            x = min(max(event.x, 0), self.size.width - 1)
            y = min(max(event.y, 0), self.size.height - 1)
            self._selection = (self._selection[0], (x, y))
            self.refresh()
        elif self.modes & {1002, 1003} and event.button and not event.shift:
            event.stop()
            self._send_mouse(event.button - 1, event, motion=True)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        event.stop()
        if self._selecting:
            self._selecting = False
            self.release_mouse()
            text = self.selected_text()
            if text.strip():
                self.app.copy_to_clipboard(text)
            else:
                self._selection = None
            self.refresh()
        elif self._mouse_taken(event):
            self._send_mouse(event.button - 1, event, release=True)
            if event.button == 1:
                self._command_selection = True

    def _bounds(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if self._selection is None:
            return None
        a, b = self._selection
        return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)

    def _is_selected(self, x: int, y: int) -> bool:
        bounds = self._bounds()
        if bounds is None:
            return False
        (x1, y1), (x2, y2) = bounds
        return (y1, x1) <= (y, x) <= (y2, x2)

    def selected_text(self) -> str:
        """What the selection covers, line ends trimmed."""
        bounds = self._bounds()
        if bounds is None:
            return ""
        (x1, y1), (x2, y2) = bounds
        lines = []
        for y in range(y1, y2 + 1):
            row = self.screen_.display[y] if y < self.screen_.lines else ""
            start = x1 if y == y1 else 0
            end = x2 + 1 if y == y2 else len(row)
            lines.append(row[start:end].rstrip())
        return "\n".join(lines)
