from __future__ import annotations

import asyncio
import logging

from textual.containers import Container, Horizontal, VerticalScroll
from textual.content import Content
from textual.widgets import Button, Input, Static, TextArea

from ccc import keymap, overview
from ccc.i18n import _
from ccc.launch_options import describe_flags
from ccc.models import DeliveryMode, QueueStatus
from ccc.orchestrator import enqueue_prompt
from ccc.storage import Storage
from ccc.tui.layout import (
    CccModal,
    ADD_PROMPT_BUTTON,
    MODAL_CSS,
    RETRY_BUTTON,
    SidebarScreen,
    panel,
    styled_status,
)
from ccc.tui.overview_text import changes_lines, errors_lines, joined, now_lines, usage_lines

OVERVIEW_SECONDS = 3.0
logger = logging.getLogger(__name__)


def delivery_text(mode: DeliveryMode) -> str:
    return {
        DeliveryMode.HEADLESS: _("headless"),
        DeliveryMode.INTERACTIVE: _("interactive"),
        DeliveryMode.LIVE_TERMINAL: _("live_terminal"),
        DeliveryMode.REMOTE_CONTROL: _("remote_control"),
    }.get(mode, mode.value)


class AddPromptModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="AddPromptModal")

    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, storage: Storage, default_path: str = ""):
        super().__init__()
        self.storage = storage
        self.default_path = default_path

    def compose(self):
        box = Container(
            Static(_("Project path"), classes="modal-label"),
            Input(value=self.default_path, placeholder=_("/path/to/project"), id="add-prompt-path"),
            Static(_("Prompt"), classes="modal-label"),
            TextArea(id="add-prompt-text"),
            Static("", id="add-prompt-error", classes="modal-error"),
            Horizontal(
                Button(_("Add"), id="add-prompt-submit", classes="modal-submit"),
                Button(_("Cancel"), id="add-prompt-cancel", classes="modal-cancel"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("New prompt")
        yield box

    def on_mount(self) -> None:
        # Path is usually prefilled from the selected queue — start in the
        # prompt box then, so you can just type.
        if self.default_path:
            self.query_one("#add-prompt-text", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-prompt-submit":
            path = self.query_one("#add-prompt-path", Input).value.strip()
            prompt = self.query_one("#add-prompt-text", TextArea).text.strip()
            if not path or not prompt:
                self.query_one("#add-prompt-error", Static).update(_("Both path and prompt are required."))
                return
            try:
                enqueue_prompt(self.storage, path, prompt)
            except ValueError as exc:
                self.query_one("#add-prompt-error", Static).update(Content(str(exc)))
                return
            self.dismiss()
        elif event.button.id == "add-prompt-cancel":
            self.dismiss()

    def action_cancel(self) -> None:
        self.dismiss()


class QueueDetailScreen(SidebarScreen):
    """Detail of one queue, opened from the dashboard; shares the
    dashboard's sidebar highlight since it's a drill-down of it."""

    VIEW = "dashboard"
    IS_DETAIL = True
    SUB_TITLE = _("Queue detail")

    BINDINGS = [
        keymap.binding("screen.back", "back", _("Back")),
        keymap.binding("screen.retry", "retry_queue", _("Retry blocked"), show=False),
        keymap.binding("screen.add_prompt", "add_prompt", _("Add prompt"), show=False),
        keymap.binding("screen.review", "review", _("Review")),
        keymap.binding("screen.goal", "goal", _("Goal")),
        keymap.binding("screen.workflow", "workflow", _("Workflow")),
    ]

    def __init__(self, storage: Storage, queue_id: int):
        super().__init__(storage)
        self.queue_id = queue_id

    def sidebar_buttons(self):
        return [ADD_PROMPT_BUTTON, RETRY_BUTTON]

    def retry_target(self):
        return self.storage.find_queue(self.queue_id)

    def compose_main(self):
        queue = self.storage.get_queue(self.queue_id)
        yield panel(
            Static(Content.assemble((queue.name, "bold"), "  ", (queue.project_path, "dim")), id="queue-detail-title"),
            Static(id="queue-detail-info"),
            title=_("Queue"),
        )
        yield panel(Static(id="overview-now"), title=_("Now"))
        yield panel(VerticalScroll(Static(id="overview-changes")), title=_("Changes"), fill=True)
        yield panel(Static(id="overview-usage"), title=_("Usage"))
        yield panel(Static(id="overview-errors"), title=_("Errors"))

    def on_mount(self) -> None:
        self._refresh_info()
        self._tail = overview.TranscriptTail()
        self._loading = False
        self.run_worker(self._refresh_overview(), exclusive=True, group="overview")
        self.set_interval(OVERVIEW_SECONDS, self._refresh_overview_soon)

    def _refresh_overview_soon(self) -> None:
        if not self._loading:
            self.run_worker(self._refresh_overview(), exclusive=True, group="overview")

    async def _refresh_overview(self) -> None:
        """Git, the transcript and the token history take a moment: off the UI thread."""
        self._loading = True
        try:
            queue = self.storage.find_queue(self.queue_id)
            if queue is None:
                return
            data = await asyncio.to_thread(
                lambda: overview.build(self.storage, queue, self._tail.last(queue.session_id)))
            self.query_one("#overview-now", Static).update(joined(now_lines(data.now)))
            self.query_one("#overview-changes", Static).update(joined(changes_lines(data.changes)))
            self.query_one("#overview-usage", Static).update(joined(usage_lines(data.usage)))
            self.query_one("#overview-errors", Static).update(joined(errors_lines(data.errors)))
            self._refresh_info()
        except Exception:  # the screen was closed meanwhile, or the folder vanished: the next tick retries
            logger.debug("overview refresh failed", exc_info=True)
        finally:
            self._loading = False

    def _refresh_info(self) -> None:
        queue = self.storage.get_queue(self.queue_id)
        # Only the first line is markup (fixed enum/number values); the rest
        # carries arbitrary text (errors, session ids) and stays plain.
        auto = _("auto-continue on") if queue.auto_continue else _("auto-continue off")
        lines = [
            Content.from_markup(
                _("Status: {status}   ·   Pending: [b]{pending}[/b]   ·   Delivery: {delivery}").format(
                    status=styled_status(queue.status),
                    pending=self.storage.count_pending_items(queue.id),
                    delivery=delivery_text(queue.delivery_mode),
                )
            ),
            Content(
                _("Launch: {flags}").format(flags=describe_flags(queue.launch_flags)) + f"  ·  {auto}"
                + (_("  ·  queue paused") if queue.paused else "")
            ),
            Content.assemble((_("Session: ") + queue.session_id, "dim")),
        ]
        if queue.delivery_mode == DeliveryMode.REMOTE_CONTROL:
            lines.append(Content.assemble((_("Remote control: "), "bold"), (queue.remote_control_url or _("starting…"), "cyan")))
        if queue.first_run_flags:
            lines.append(Content.assemble((_("First run: ") + " ".join(queue.first_run_flags), "yellow")))
        if queue.next_retry_at:
            lines.append(Content.assemble((_("Next retry: ") + f"{queue.next_retry_at:%Y-%m-%d %H:%M}", "yellow")))
        if queue.last_error:
            lines.append(Content.assemble((_("Last error: ") + queue.last_error[:200], "red")))
        self.query_one("#queue-detail-info", Static).update(Content("\n").join(lines))

    def default_project_path(self) -> str:
        return self.storage.get_queue(self.queue_id).project_path

    def action_retry_queue(self) -> None:
        if self.storage.retry_queue(self.queue_id):
            self._refresh_info()
            self.sync_retry_button()
