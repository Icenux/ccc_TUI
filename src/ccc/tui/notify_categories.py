"""What Telegram / Discord are told (#146): one window per service with a
tick for each category of message (ccc.notify.CATEGORIES). Opened from the
service's section in Settings; saved at once with Ctrl+S."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Static

from ccc import keymap
from ccc.i18n import _
from ccc.notify import CATEGORIES, category_states, set_category_states
from ccc.tui.layout import CccModal, MODAL_CSS

SERVICE_NAMES = {"telegram": "Telegram", "discord": "Discord"}


class NotifyCategoriesModal(CccModal):
    """Dismisses with True when saved, None when cancelled."""

    DEFAULT_CSS = MODAL_CSS.format(cls="NotifyCategoriesModal") + """
    NotifyCategoriesModal > Container {
        width: 100;
        max-width: 100%;
        max-height: 100%;
        padding: 0 2;
    }
    NotifyCategoriesModal VerticalScroll {
        height: auto;
        max-height: 14;
    }
    /* One line per kind; focus is shown by the label, not a frame. */
    NotifyCategoriesModal Checkbox {
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }
    NotifyCategoriesModal Checkbox:focus {
        border: none;
        background: $boost;
    }
    NotifyCategoriesModal .modal-hint {
        color: $text-muted;
    }
    /* Unticked: no faint X that reads as ticked; ticked: bright X. */
    NotifyCategoriesModal Checkbox > .toggle--button {
        color: $panel;
    }
    NotifyCategoriesModal Checkbox.-on > .toggle--button {
        color: $text-success;
        text-style: bold;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "save", _("Save"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, storage, channel: str):
        super().__init__()
        self.storage = storage
        self.channel = channel

    def compose(self):
        states = category_states(self.storage, self.channel)
        rows = []
        for key, label, hint, _default in CATEGORIES:
            rows.append(Checkbox(Text.assemble((_(label), "bold"), ("  " + _(hint), "dim")), states[key],
                                 id=f"notify-{key}"))
        box = Container(
            Static(_("Which messages {service} gets:").format(service=SERVICE_NAMES[self.channel]), classes="modal-label"),
            VerticalScroll(*rows),
            Static(_("Each service has its own choice."), classes="modal-hint"),
            Horizontal(
                Button(_("Cancel"), id="notify-cancel", classes="modal-cancel"),
                Button(_("Save"), id="notify-save", classes="modal-submit"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("{service} notifications").format(service=SERVICE_NAMES[self.channel])
        yield box

    def on_mount(self) -> None:
        self.query_one(Checkbox).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        (self.action_save if event.button.id == "notify-save" else self.action_cancel)()

    def action_save(self) -> None:
        set_category_states(self.storage, self.channel,
                            {key: self.query_one(f"#notify-{key}", Checkbox).value for key, *_rest in CATEGORIES})
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)
