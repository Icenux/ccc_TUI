"""Search the conversations — the active ones, the archive, or both (you
choose, #304); Enter on a hit opens that conversation."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Input, Select, Static

from ccc import keymap
from ccc.i18n import N_, _
from ccc.search import ACTIVE, ALL_KINDS, ARCHIVED, EVERYWHERE, search_conversations
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
    SearchModal #search-row {
        height: auto;
    }
    SearchModal #search-input {
        width: 1fr;
    }
    SearchModal #search-where {
        width: 28;
        margin-left: 1;
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

    WHERE = ((N_("In conversations"), ACTIVE), (N_("In the archive"), ARCHIVED), (N_("Everywhere"), EVERYWHERE))
    WHO = {"human": N_("You"), "assistant_text": "Claude", "tool_call": N_("Tool"), "tool_result": N_("Output")}

    def __init__(self, storage: Storage, where: str = ACTIVE):
        super().__init__()
        self.storage = storage
        self.where = where
        self._hit_queue_ids: list[int] = []

    def compose(self):
        box = Container(
            Horizontal(
                Input(placeholder=_("Search… (Enter)"), id="search-input"),
                Select([(_(label), value) for label, value in self.WHERE], value=self.where,
                       allow_blank=False, id="search-where"),
                id="search-row",
            ),
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

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "search-where" and event.value != self.where:
            self.where = event.value
            if self.query_one("#search-input", Input).value.strip():
                self.run_search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.run_search()

    def run_search(self) -> None:
        text = self.query_one("#search-input", Input).value
        hits = search_conversations(self.storage, text, where=self.where, kinds=ALL_KINDS)
        table = self.query_one("#search-results", DataTable)
        table.clear()
        self._hit_queue_ids = [h.queue_id for h in hits]
        for hit in hits:
            name = Text(hit.queue_name)
            if hit.archived and self.where == EVERYWHERE:
                name.append(" " + _("(archived)"), style="dim")
            table.add_row(
                name,
                hit.timestamp.astimezone().strftime("%Y-%m-%d %H:%M"),
                _(self.WHO.get(hit.kind, hit.kind)),
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
