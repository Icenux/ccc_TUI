"""Claude Code inside ccc's own frame (CCC_EMBEDDED_TERMINAL, issue #133):
the conversation's session shown by a TerminalPane in a rounded green block —
title, the conversations that wait for you, "typing here" while the keys go
to it — and the preset prompts under it. The same as the tmux pane next to
ccc (tui.cli_pane) and the bar under it (tui.cli_bar), drawn by ccc."""
from __future__ import annotations

import threading

from rich.text import Text
from textual import events
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Static

from ccc import clipboard_bridge, keymap, managed_sessions
from ccc.cli_input import apply_preset
from ccc.i18n import _
from ccc.prompt_presets import load_prompt_presets
from ccc.tui import cli_pane
from ccc.tui.prompt_presets import bar_item
from ccc.tui.terminal_pane import TerminalPane

REFRESH_SECONDS = 2.0
# ccc's keys that stay ccc's while the terminal has the keyboard (the ones
# tmux used to bind in the root table, cli_pane.key_bindings).
PASSTHROUGH_IDS = ("conversations.focus_claude", "app.settings", "app.screenshot", "app.jump_waiting",
                   "app.quit", *[f"conversations.preset{n}" for n in range(1, 10)])


class ClaudePane(Vertical):
    DEFAULT_CSS = """
    ClaudePane {
        width: 1fr;
        height: 1fr;
        border: round $ccc-frame;
        border-title-color: $ccc-title;
        border-title-style: bold;
        border-subtitle-color: yellow;
    }
    ClaudePane #claude-presets {
        height: 1;
        padding: 0 1;
        background: $panel;
        dock: bottom;
    }
    ClaudePane #claude-presets .bar-preset {
        width: auto;
        margin-right: 3;
    }
    ClaudePane #claude-presets .bar-preset:hover {
        background: $boost;
    }
    """

    class Closed(Message):
        """The close key was pressed, or the session ended."""

        def __init__(self, queue_id: int) -> None:
            super().__init__()
            self.queue_id = queue_id

    def __init__(self, queue_id: int, name: str, storage) -> None:
        super().__init__()
        self.queue_id = queue_id
        self.conversation = name
        self.storage = storage
        self.keys = keymap.current(storage)
        self._shown: list | None = None
        self._waiting = ""
        self._bridge: threading.Event | None = None

    def compose(self):
        yield TerminalPane(
            cli_pane.attach_command_list(self.queue_id, self.keys),
            release_key=self.keys["conversations.close_claude"],
            passthrough={self.keys[i] for i in PASSTHROUGH_IDS if i in self.keys},
            copy_sequence=managed_sessions.COPY_SEQUENCE,
            cancel_sequence=managed_sessions.CANCEL_SEQUENCE,
            id="claude-terminal")
        yield Horizontal(id="claude-presets")

    @property
    def terminal(self) -> TerminalPane:
        return self.query_one("#claude-terminal", TerminalPane)

    async def on_mount(self) -> None:
        self._set_title()
        await self.refresh_presets()
        self.set_interval(REFRESH_SECONDS, self.refresh_presets)
        # Copies made in Claude Code → your device's clipboard.
        self._bridge = clipboard_bridge.start(self.queue_id, on_copy=self.app.copy_to_clipboard_threadsafe)

    def on_unmount(self) -> None:
        if self._bridge is not None:
            self._bridge.set()

    def focus_terminal(self) -> None:
        self.terminal.focus()

    def has_keyboard(self) -> bool:
        return self.terminal.has_focus

    def set_waiting(self, text: str) -> None:
        """The conversations that need you (ccc.attention), shown in the frame's top line."""
        if text != self._waiting:
            self._waiting = text
            self._set_title()

    def waiting_room(self) -> int:
        """Columns the waiting conversations can use in the title."""
        return max(0, self.size.width - len(self._title_text()) - len(_("⌨ typing here")) - 10)

    def _title_text(self) -> str:
        return f"Claude Code · {self.conversation}" if self.conversation else "Claude Code"

    def _set_title(self) -> None:
        title = Text()
        if self.has_keyboard():
            title.append(f" {_('⌨ typing here')} ", style="bold black on #5faf5f")
        title.append(f" {self._title_text()} ")
        self.border_title = title
        self.border_subtitle = Text(f" {self._waiting} ") if self._waiting else ""

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._set_title()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self._set_title()

    def on_terminal_pane_released(self, message: TerminalPane.Released) -> None:
        message.stop()
        self.post_message(self.Closed(self.queue_id))

    def on_terminal_pane_exited(self, message: TerminalPane.Exited) -> None:
        message.stop()
        self.post_message(self.Closed(self.queue_id))

    async def refresh_presets(self) -> None:
        presets = load_prompt_presets(self.storage)
        if presets == self._shown:
            return
        self._shown = presets
        row = self.query_one("#claude-presets", Horizontal)
        await row.remove_children()
        if presets:
            await row.mount_all(
                Static(bar_item(n, p, self.keys), id=f"claude-preset-{n}", classes="bar-preset")
                for n, p in enumerate(presets, start=1))
        else:
            await row.mount(Static(Text(_("No preset prompts — add them in Settings (Ctrl+P)"), style="dim")))

    async def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None or not (widget.id or "").startswith("claude-preset-"):
            return
        event.stop()
        import asyncio

        error = await asyncio.to_thread(apply_preset, self.storage, self.queue_id,
                                        int(widget.id.removeprefix("claude-preset-")))
        if error:
            self.notify(error, severity="warning")
        self.focus_terminal()  # the bar is only clicked, never typed in
