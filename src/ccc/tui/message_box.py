"""Multi-line message box under a conversation's transcript: Enter sends,
Alt+Enter sends it right away, past the queue; Ctrl+J (works in every terminal) or Shift+Enter (where the terminal
reports it) starts a new line, @ mentions a file (tui.mentions)."""
from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from ccc.tui.mentions import FileMentions

NEWLINE_KEYS = ("ctrl+j", "shift+enter")
# Send past the queue, straight into a running Claude Code (Alt+Enter works
# in every terminal, Ctrl+Enter only where the terminal reports it).
SEND_NOW_KEYS = ("alt+enter", "ctrl+enter")


def _alt_digit_keys() -> frozenset[str]:
    from ccc.tui.prompt_presets import ALT_DIGIT_CHARS

    return frozenset(ALT_DIGIT_CHARS.values())


class MessageBox(FileMentions, TextArea):
    DEFAULT_CSS = """
    MessageBox {
        height: auto;
        min-height: 3;
        max-height: 10;
    }
    """

    @dataclass
    class Submitted(Message):
        box: "MessageBox"
        text: str

    @dataclass
    class SubmittedNow(Message):
        box: "MessageBox"
        text: str

    def __init__(self, placeholder: str = "", **kwargs):
        super().__init__(soft_wrap=True, show_line_numbers=False, tab_behavior="focus", **kwargs)
        self.placeholder = placeholder

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        # Alt+1 … Alt+9 arrive as "¡", "™", … — leave them to the preset
        # prompt bindings instead of typing the character.
        if key in _alt_digit_keys():
            return False
        return super().check_consume_key(key, character)

    async def _on_key(self, event: events.Key) -> None:
        if self.mention_key(event):
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key in SEND_NOW_KEYS:
            event.stop()
            event.prevent_default()
            self.post_message(self.SubmittedNow(self, self.text))
            return
        if event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        await super()._on_key(event)
