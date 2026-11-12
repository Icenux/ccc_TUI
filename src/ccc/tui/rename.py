"""The rename window (F2 in Conversations / Archive): gives the selected
conversation a new name in ccc."""
from __future__ import annotations

from textual.containers import Container
from textual.widgets import Input, Static

from ccc import keymap
from ccc.i18n import _
from ccc.tui.layout import CccModal, MODAL_CSS


class RenameModal(CccModal):
    """Dismisses True when the name changed."""

    DEFAULT_CSS = MODAL_CSS.format(cls="RenameModal")
    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, storage, queue_id: int):
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id

    def compose(self):
        box = Container(
            Static(_("Name"), classes="modal-label"),
            Input(value=self.storage.get_queue(self.queue_id).name, id="rename-name"),
        )
        box.border_title = _("Rename conversation")
        yield box

    def on_mount(self) -> None:
        self.query_one("#rename-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = " ".join(event.value.split())
        if not name:
            return
        changed = name != self.storage.get_queue(self.queue_id).name
        if changed:
            self.storage.rename_queue(self.queue_id, name)
        self.dismiss(changed)

    def action_cancel(self) -> None:
        self.dismiss(False)
