"""Preset prompts in the TUI: the editor window used from Settings and the
labels shown in the bar under a conversation's message box."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container
from textual.widgets import Checkbox, Input, Static, TextArea

from ccc import keymap
from ccc.i18n import _
from ccc.prompt_presets import PromptPreset
from ccc.tui.layout import CccModal, MODAL_CSS
from ccc.tui.theme import ACCENT

# Most terminals (and tmux, Termux) send Alt+digit as Esc+digit, which
# Textual turns into the character macOS Option+digit types ("\x1b1" -> "¡");
# only terminals with the kitty keyboard protocol report a real "alt+1".
ALT_DIGIT_CHARS = {
    1: "inverted_exclamation_mark", 2: "trade_mark_sign", 3: "pound_sign", 4: "cent_sign", 5: "infinity",
    6: "section_sign", 7: "pilcrow_sign", 8: "bullet", 9: "ª",
}
# The characters themselves, typed when there's no preset on that number.
ALT_DIGIT_TEXT = {1: "¡", 2: "™", 3: "£", 4: "¢", 5: "∞", 6: "§", 7: "¶", 8: "•", 9: "ª"}

# Alt+0 (switch ccc ↔ Claude Code) arrives the same way, as "º".
ALT_ZERO_CHAR = "º"

SEND_MARK = "⏎"
INSERT_MARK = "✎"


def mode_mark(preset: PromptPreset) -> str:
    return SEND_MARK if preset.send_now else INSERT_MARK


def mode_label(preset: PromptPreset) -> str:
    return f"{SEND_MARK} " + _("send") if preset.send_now else f"{INSERT_MARK} " + _("insert")


def send_now_label(send_now: bool) -> str:
    """The editor checkbox's text: says what the preset does in its current state."""
    return _("The text is sent to the conversation") if send_now else _("The text is put into the text field")


def preset_key(number: int, keys: dict[str, str] | None = None) -> str:
    """Preset `number`'s key as shown (Alt+1 by default)."""
    return keymap.pretty((keys or keymap.defaults())[f"conversations.preset{number}"])


def bar_item(number: int, preset: PromptPreset, keys: dict[str, str] | None = None) -> Text:
    """A preset in the bar under a conversation: its key in the colour of the keys in the status bar."""
    return Text.assemble((f"{preset_key(number, keys)} ", f"bold {ACCENT}"), (mode_mark(preset) + " ", "yellow"), preset.name)


class PromptPresetModal(CccModal):
    """Name, prompt text and mode of one preset. Dismisses with a
    PromptPreset, or None when cancelled."""

    DEFAULT_CSS = MODAL_CSS.format(cls="PromptPresetModal") + """
    PromptPresetModal TextArea {
        height: 10;
    }
    PromptPresetModal .modal-hint {
        color: $text-muted;
    }
    /* Unticked: no faint X that reads as ticked; ticked: bright X. */
    PromptPresetModal Checkbox > .toggle--button {
        color: $panel;
    }
    PromptPresetModal Checkbox.-on > .toggle--button {
        color: $text-success;
        text-style: bold;
    }
    PromptPresetModal #preset-error {
        color: red;
    }
    """

    BINDINGS = [
        # priority: TextArea would otherwise swallow these keys.
        keymap.binding("window.save", "save", _("Save"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, preset: PromptPreset | None = None, title: str | None = None):
        super().__init__()
        self.preset = preset or PromptPreset("", "", False)
        self.box_title = title or _("New preset prompt")

    def compose(self):
        box = Container(
            Static(_("Name (shown in the bar under the message box)"), classes="modal-label"),
            Input(self.preset.name, placeholder=_("e.g. Tests"), id="preset-name"),
            Static(_("Prompt"), classes="modal-label"),
            TextArea(self.preset.text, id="preset-text"),
            Checkbox(send_now_label(self.preset.send_now), self.preset.send_now, id="preset-send-now"),
            Static("", id="preset-error"),
            Static(_("Ctrl+S save  ·  Esc cancel"), classes="modal-hint"),
        )
        box.border_title = self.box_title
        yield box

    def on_mount(self) -> None:
        self.query_one("#preset-name", Input).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "preset-send-now":
            event.checkbox.label = send_now_label(event.value)

    def action_save(self) -> None:
        name = self.query_one("#preset-name", Input).value.strip()
        text = self.query_one("#preset-text", TextArea).text
        if not name or not text.strip():
            self.query_one("#preset-error", Static).update("Fill in both the name and the prompt.")
            return
        self.dismiss(PromptPreset(name, text, self.query_one("#preset-send-now", Checkbox).value))

    def action_cancel(self) -> None:
        self.dismiss(None)
