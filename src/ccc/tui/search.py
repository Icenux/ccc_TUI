"""Search all conversations; Enter on a hit opens that conversation."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container
from textual.widgets import DataTable, Input, Static

from ccc import keymap
from ccc.i18n import _
from ccc.search import search_conversations
from ccc.storage import Storage
from ccc.tui.layout import CccModal, MODAL_CSS


class SearchModal(CccModal):
    """Dismisses with the queue id of the chosen hit, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="SearchModal") + """
    SearchModal > Container {
        width: 90%;
        max-width: 140;
        height: 80%;
    }
    SearchModal #search-results {
        height: 1fr;
    }
    SearchModal #search-results > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    SearchModal #search-status {
        color: $text-muted;
        margin: 1 0;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close"))]

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self._hit_queue_ids: list[int] = []

    def compose(self):
        box = Container(
            Input(placeholder=_("Search all conversations… (Enter)"), id="search-input"),
            Static("", id="search-status"),
            DataTable(id="search-results"),
        )
        box.border_title = _("Search")
        yield box

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.cursor_type = "row"
        table.add_columns(_("Conversation"), _("When"), _("Who"), _("Match"))
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hits = search_conversations(self.storage, event.value)
        table = self.query_one("#search-results", DataTable)
        table.clear()
        self._hit_queue_ids = [h.queue_id for h in hits]
        for hit in hits:
            table.add_row(
                Text(hit.queue_name),
                hit.timestamp.astimezone().strftime("%Y-%m-%d %H:%M"),
                _("You") if hit.kind == "human" else "Claude",
                Text(hit.snippet),
            )
        status = _("{n} matches — ↓ then Enter opens one").format(n=len(hits)) if hits else _("No matches.")
        self.query_one("#search-status", Static).update(status)
        if hits:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.cursor_row < len(self._hit_queue_ids):
            self.dismiss(self._hit_queue_ids[event.cursor_row])

    def action_close(self) -> None:
        self.dismiss(None)
