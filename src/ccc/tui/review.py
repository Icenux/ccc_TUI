"""The review window (v in Conversations / queue detail): ask a fresh
reviewer to check what the conversation changed — once, or until it
passes — and read its verdicts and reports. The daemon runs the review
(ccc.reviews); this window asks for it and shows the results as they come."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Input, Static, TextArea

from ccc import keymap, reviews
from ccc.i18n import _
from ccc.models import Review, ReviewStatus, Verdict
from ccc.schedule import format_when
from ccc.tui.layout import CccModal, MODAL_CSS

REFRESH_SECONDS = 2.0
VERDICT_STYLES = {Verdict.PASS: "bold green", Verdict.FAIL: "bold red", Verdict.PARTIAL: "bold yellow"}


def result_text(review: Review) -> Text:
    if review.status == ReviewStatus.DONE and review.verdict is not None:
        return Text(review.verdict.value, style=VERDICT_STYLES[review.verdict])
    style = "red" if review.status == ReviewStatus.ERROR else "yellow" if review.status in reviews.ACTIVE else "dim"
    return Text(_(reviews.STATUS_LABELS[review.status]), style=style)


class ReviewModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="ReviewModal") + """
    ReviewModal > Container {
        width: 95%;
        max-width: 140;
        height: 95%;
    }
    ReviewModal TextArea {
        height: 4;
    }
    ReviewModal #review-options {
        height: 3;
    }
    ReviewModal #review-options Checkbox {
        width: auto;
    }
    ReviewModal #review-rounds {
        width: 12;
        margin-bottom: 0;
    }
    ReviewModal #review-table {
        height: 8;
        margin-bottom: 1;
    }
    ReviewModal #review-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    ReviewModal #review-report-box {
        height: 1fr;
        border: solid $ccc-line;
        padding: 0 1;
    }
    ReviewModal .review-actions {
        height: 3;
        margin-top: 1;
    }
    ReviewModal .review-actions Button {
        height: 3;
        margin-right: 1;
    }
    ReviewModal .modal-hint {
        color: $text-muted;
        width: 1fr;
        padding-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "start", _("Start review"), priority=True),
        keymap.binding("window.cancel", "close", _("Close"), priority=True),
    ]

    def __init__(self, storage, queue_id: int):
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id
        self._ids: list[int] = []

    def compose(self):
        queue = self.storage.get_queue(self.queue_id)
        box = Container(
            Static(_("A reviewer with a clean context reads what this conversation changed since the last review "
                     "and gives one verdict: PASS, FAIL or PARTIAL. It can't change anything."), classes="modal-hint"),
            TextArea(id="review-instructions"),
            Horizontal(
                Checkbox(_("Until it passes: send the findings back, review again after the fix — at most"),
                         id="review-until-pass"),
                Input(str(reviews.DEFAULT_ROUNDS), id="review-rounds", type="integer"),
                Static(_("rounds"), classes="modal-hint"),
                id="review-options",
            ),
            DataTable(id="review-table", cursor_type="row"),
            VerticalScroll(Static("", id="review-report"), id="review-report-box"),
            Horizontal(
                Button(_("Start review"), id="review-start"),
                Button(_("Send findings to the conversation"), id="review-apply"),
                Button(_("Stop"), id="review-stop", classes="review-danger"),
                Static(_("Ctrl+S start  ·  Esc close"), classes="modal-hint"),
                classes="review-actions",
            ),
        )
        box.border_title = _("Review — {name}").format(name=queue.name)
        yield box

    def on_mount(self) -> None:
        for button in self.query(".review-actions Button"):
            button.can_focus = False
        self.query_one("#review-instructions", TextArea).placeholder = _(
            "Optional: what to check first, e.g. the error paths, not the happy path")
        table = self.query_one("#review-table", DataTable)
        table.add_columns(_("When"), _("Round"), _("Result"), _("What"))
        self._refresh()
        self.set_interval(REFRESH_SECONDS, self._refresh)
        self.query_one("#review-instructions", TextArea).focus()

    def _refresh(self) -> None:
        table = self.query_one("#review-table", DataTable)
        previous = self.selected()
        table.clear()
        items = self.storage.list_reviews(self.queue_id)
        self._ids = [r.id for r in items]
        now = datetime.now()
        for r in items:
            rounds = f"{r.round}/{r.max_rounds}" if r.until_pass or r.round > 1 else "1"
            table.add_row(format_when(r.finished_at or r.created_at, now), rounds, result_text(r),
                          Text((r.scope or r.instructions or "").splitlines()[0][:70] if (r.scope or r.instructions)
                               else ""))
        if not items:
            table.add_row("", "", "", Text(_("no reviews yet"), style="dim"))
        elif previous is not None and previous.id in self._ids:
            table.move_cursor(row=self._ids.index(previous.id))
        self._show_report()
        busy = reviews.active(self.storage, self.queue_id) is not None
        self.query_one("#review-start", Button).disabled = busy
        self.query_one("#review-stop", Button).disabled = not busy
        r = self.selected()
        self.query_one("#review-apply", Button).disabled = not (
            r is not None and r.verdict in (Verdict.FAIL, Verdict.PARTIAL) and r.fix_item_id is None)

    def selected(self) -> Review | None:
        table = self.query_one("#review-table", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._ids):
            return None
        return self.storage.find_review(self._ids[row])

    def _show_report(self) -> None:
        r = self.selected()
        report = self.query_one("#review-report", Static)
        if r is None:
            report.update("")
            return
        parts = []
        if r.scope:
            parts.append(Text(_("Reviewed: {scope}").format(scope=r.scope), style="dim"))
        if r.status in reviews.ACTIVE:
            waiting = _("waits until the conversation is idle") if r.status == ReviewStatus.PENDING else ""
            if r.not_before:
                waiting = _("waits for the usage limit until {at}").format(at=format_when(r.not_before, datetime.now()))
            parts.append(Text(waiting or _("the reviewer is reading…"), style="yellow"))
        if r.error:
            parts.append(Text(r.error, style="red"))
        if r.report:
            parts.append(Text(r.report))
        if r.fix_item_id:
            parts.append(Text(_("Findings were sent to the conversation as a task."), style="green"))
        report.update(Text("\n\n").join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_report()
        r = self.selected()
        self.query_one("#review-apply", Button).disabled = not (
            r is not None and r.verdict in (Verdict.FAIL, Verdict.PARTIAL) and r.fix_item_id is None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"review-start": self.action_start, "review-apply": self.action_apply,
         "review-stop": self.action_stop}.get(event.button.id, lambda: None)()

    def action_start(self) -> None:
        until_pass = self.query_one("#review-until-pass", Checkbox).value
        rounds_text = self.query_one("#review-rounds", Input).value
        rounds = int(rounds_text) if rounds_text.isdigit() else reviews.DEFAULT_ROUNDS
        try:
            reviews.request(self.storage, self.queue_id, self.query_one("#review-instructions", TextArea).text,
                            until_pass, rounds)
        except ValueError as error:
            self.notify(str(error), severity="warning")
            return
        self.notify(_("Review asked for — it starts as soon as the conversation is idle."))
        self._refresh()
        self.query_one("#review-table", DataTable).move_cursor(row=0)

    def action_apply(self) -> None:
        r = self.selected()
        if r is None or r.verdict not in (Verdict.FAIL, Verdict.PARTIAL) or r.fix_item_id is not None:
            return
        reviews.apply(self.storage, r)
        self.notify(_("Findings queued as a task in the conversation."))
        self._refresh()

    def action_stop(self) -> None:
        if reviews.stop(self.storage, self.queue_id):
            self.notify(_("Review stopped."))
        self._refresh()

    def action_close(self) -> None:
        self.dismiss()
