from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Collapsible, DataTable, Input, Markdown, Static

from ccc import groups, keymap, list_filter
from ccc.i18n import _
from ccc.launch_options import describe_flags
from ccc.models import DeliveryMode, Queue, QueueStatus
from ccc.orchestrator import send_message
from ccc.prompt_presets import load_prompt_presets
from ccc.storage import Storage
from ccc.transcript import TranscriptEvent, find_transcript_path, parse_transcript_events
from ccc.tui import cli_pane
from ccc.tui.claude_pane import ClaudePane
from ccc.tui.layout import (
    ARCHIVE_BUTTON,
    FILTER_BUTTON,
    RENAME_BUTTON,
    NEW_CONVERSATION_BUTTON,
    RETRY_BUTTON,
    ConfirmModal,
    SidebarScreen,
    queue_label,
    queue_marker,
)
from ccc.tui.message_box import MessageBox
from ccc.tui.pixel_text import PixelBanner
from ccc.tui.prompt_presets import ALT_DIGIT_CHARS, ALT_DIGIT_TEXT, ALT_ZERO_CHAR, bar_item

GIT_STATUS_SECONDS = 15.0


# Newest transcript events mounted when a conversation is opened; older
# ones are summarised in one line (Markdown + tool widgets cost ~15ms each).
MAX_RENDERED_EVENTS = 60
# Of those, the newest are mounted first — the conversation shows at once —
# and the older ones follow in chunks, so the keys stay responsive.
FIRST_RENDERED_EVENTS = 12
RENDER_CHUNK_EVENTS = 8
# Conversations whose rendered transcript is kept (hidden) after you switch
# away: switching back is then instant instead of mounting it all again.
VIEWS_KEPT = 6

# Marker order in a group's heading (layout.queue_marker icons).
HEADING_MARKERS = ("?", "●", "✖", "‖", "◆")


class _View:
    """One conversation's rendered transcript pane: a scroll container that
    stays mounted (hidden) while another conversation is shown."""

    def __init__(self, container: VerticalScroll) -> None:
        self.container = container
        self.queued: Vertical | None = None  # None: only the "No conversation yet" line
        # tool_use_id -> the result widget of a rendered tool call, so a
        # result arriving later can be filled in.
        self.tool_results: dict[str, Static] = {}
        # (queue, event count, pending item ids) as last drawn — update when it changes.
        self.key: tuple | None = None
        self.filler = None  # the worker mounting the older events


class ConversationList(DataTable):
    """The sidebar list. One click on a group's heading folds / unfolds it
    — a plain DataTable selects only a row clicked a second time."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.heading_rows: set[int] = set()

    async def _on_click(self, event) -> None:
        # Textual runs this before DataTable's own handler: with the cursor
        # already on the row, that one selects it.
        row = event.style.meta.get("row")
        if row in self.heading_rows:
            self.move_cursor(row=row)


class ConversationsScreen(SidebarScreen):
    VIEW = "conversations"
    SUB_TITLE = _("Conversations")
    # Which conversations the list shows: the active ones here, the
    # archived ones in the Archive view (tui.archive).
    ARCHIVED = False
    # Big pixel text when there's nothing to show (see _shows_nothing).
    BANNER_TEXT = _("NO CONVERSATION")

    BINDINGS = [
        # Back = Dashboard (d) and Retry has its sidebar button.
        keymap.binding("screen.back", "back", _("Back"), show=False),
        keymap.binding("screen.retry", "retry_queue", _("Retry / resume"), show=False),
        keymap.binding("conversations.tasks", "tasks", _("Tasks")),
        # Its button is in the sidebar (ARCHIVE_BUTTON), not in the footer.
        keymap.binding("conversations.archive", "toggle_archive", _("Archive"), show=False),
        # No Ctrl+D (delete): it exits Claude Code (#31).
        keymap.binding("conversations.search", "search", _("Search")),
        # Its button is above the list (FILTER_BUTTON), not in the footer.
        keymap.binding("conversations.filter", "filter", _("Filter"), show=False),
        # Its button is next to the filter (RENAME_BUTTON), not in the footer.
        keymap.binding("conversations.rename", "rename", _("Rename"), show=False),
        keymap.binding("conversations.group", "group", _("Group")),
        keymap.binding("conversations.next_group", "next_group", _("Next group"), show=False),
        keymap.binding("conversations.prev_group", "prev_group", _("Previous group"), show=False),
        keymap.binding("conversations.export", "export", _("Export conversation")),
        keymap.binding("screen.review", "review", _("Review")),
        keymap.binding("screen.goal", "goal", _("Goal")),
        keymap.binding("screen.workflow", "workflow", _("Workflow")),
        keymap.binding("conversations.approve", "approve", _("Approve")),
        # While Claude Code is open beside ccc — listed in the key strip;
        # pressed in the Claude pane they're tmux's (cli_pane.key_bindings,
        # attach_cli's Ctrl+]), clicked in the strip they land here.
        # key_display: only for the default key — a moved one shows itself
        # (CCContinueApp.get_key_display).
        keymap.binding("conversations.focus_claude", "focus_claude", "Claude ↔ ccc", key_display="Alt+0"),
        keymap.binding("conversations.close_claude", "close_claude", _("Close Claude"), key_display="Ctrl+]"),
        # Preset prompts (Settings); priority so they work while typing.
        *[keymap.binding(f"conversations.preset{n}", f"prompt_preset({n})", _("Preset {n}").format(n=n), show=False,
                         priority=True) for n in range(1, 10)],
        # Alt+digit as some terminals send it (a character). No id: a preset
        # moved to another key leaves these behind, harmlessly.
        *[Binding(ALT_DIGIT_CHARS[n], f"prompt_preset({n}, True)", _("Preset {n}").format(n=n), show=False, priority=True)
          for n in range(1, 10)],
        # Alt+0 the same way: Esc+0 arrives as "º". Only while Claude Code is
        # open (check_action), so a real "º" is still typed otherwise.
        Binding(ALT_ZERO_CHAR, "focus_claude", "Claude ↔ ccc", show=False, priority=True),
    ]

    # Sidebar frame/buttons come from SidebarScreen (layout.SHARED_CSS);
    # only the transcript message boxes are specific to this screen.
    DEFAULT_CSS = """
    ConversationsScreen #conversation-filter {
        display: none;
        margin: 0 1 1 1;
    }
    ConversationsScreen.-filter-open #conversation-filter {
        display: block;
    }
    ConversationsScreen #conversation-filter-empty {
        padding: 0 1;
        color: $text-muted;
    }
    ConversationsScreen #conversation-header {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border-bottom: solid $ccc-frame;
    }
    ConversationsScreen.-cli-open #main {
        display: none;
    }
    /* An archived interactive conversation has no Claude Code to show. */
    ConversationsScreen #no-conversation {
        display: none;
        width: 1fr;
        height: 1fr;
        background: black;
        color: green;
        text-style: bold;
        content-align: center middle;
        border: round $ccc-frame;
        border-title-color: $ccc-title;
        border-title-style: bold;
    }
    ConversationsScreen.-no-conversation #main {
        background: black;
    }
    ConversationsScreen.-no-conversation #main > * {
        display: none;
    }
    ConversationsScreen.-no-conversation #main > #no-conversation {
        display: block;
    }
    ConversationsScreen #conversation-transcript {
        width: 1fr;
        height: 1fr;
    }
    ConversationsScreen .transcript-view {
        height: 1fr;
    }
    ConversationsScreen #conversation-status {
        height: auto;
        max-height: 3;
        padding: 0 1;
    }
    ConversationsScreen #prompt-presets-bar {
        height: auto;
        padding: 0 1;
    }
    ConversationsScreen #prompt-presets-bar.-empty {
        display: none;
    }
    ConversationsScreen #prompt-presets-bar .prompt-preset-item {
        width: auto;
        margin-right: 3;
    }
    ConversationsScreen #prompt-presets-legend {
        width: 1fr;
        content-align-horizontal: right;
        color: $text-muted;
    }
    ConversationsScreen #prompt-presets-bar .prompt-preset-item:hover {
        background: $boost;
    }
    ConversationsScreen .queued-messages {
        height: auto;
    }
    ConversationsScreen .message-box {
        border: round $ccc-human;
        padding: 0 1;
        margin-bottom: 1;
    }
    ConversationsScreen .message-box-human {
        border: round $ccc-human;
    }
    ConversationsScreen .message-box-assistant {
        border: round $ccc-claude;
    }
    ConversationsScreen .message-box-queued {
        border: round $ccc-line;
        color: $text-muted;
    }
    ConversationsScreen .message-header {
        color: $accent;
    }
    ConversationsScreen .tool-call-line {
        color: $text-muted;
    }
    ConversationsScreen .message-box {
        height: auto;
    }
    ConversationsScreen .message-box Markdown {
        margin: 0;
        padding: 0;
    }
    ConversationsScreen Collapsible.tool-call {
        border: none;
        padding: 0;
        margin: 0 0 1 0;
        background: transparent;
    }
    ConversationsScreen Collapsible.tool-call CollapsibleTitle {
        color: $text-muted;
        padding: 0;
    }
    ConversationsScreen .tool-input {
        color: $text-muted;
        padding: 0 2;
    }
    ConversationsScreen .tool-result {
        padding: 0 2;
        border-left: tall $panel-lighten-2;
        margin-left: 2;
    }
    """

    def __init__(self, storage: Storage, select_queue_id: int | None = None):
        super().__init__(storage)
        # Per-queue cache: queue_id -> {"path": Path|None, "offset": int,
        # "events": list[TranscriptEvent]} so switching back to an
        # already-viewed queue doesn't re-parse its transcript from zero.
        self._cache: dict[int, dict] = {}
        self._current_queue_id: int | None = select_queue_id
        # Opened for a specific (usually just-started) conversation: put the
        # cursor straight into the message box.
        self._focus_input_on_mount = select_queue_id is not None
        # The sidebar table's rows in order: a queue id, or a group key (str)
        # for a group's heading (ccc.groups). _queue_ids are the conversations
        # among them; _listed_ids also those in folded groups, and
        # _group_of their group's key.
        self._rows: list[int | str] = []
        self._queue_ids: list[int] = []
        self._listed_ids: list[int] = []
        self._group_of: dict[int, str] = {}
        # The heading the cursor is on: it stays there over the refresh.
        self._cursor_group: str | None = None
        # Plain text of the list rows as last drawn — rebuild when it changes
        # (new/removed conversation, status marker, pending count).
        self._list_signature: list[str] = []
        self._row_keys: list = []
        self._column_key = None
        # The rendered transcripts kept (_View), least recently shown first,
        # and the conversation whose one is on screen.
        self._views: dict[int, _View] = {}
        self._shown: int | None = None
        # queue_id -> gitops.WorkStatus of its folder, refreshed in a thread
        # (GIT_STATUS_SECONDS) so the 2 s refresh never waits for git.
        self._git_status: dict[int, object] = {}
        # Interactive conversations show their Claude Code CLI in a tmux
        # pane next to ccc (cli_pane): which conversation it was opened for,
        # and one whose pane you closed (Ctrl+]) — not reopened until Enter.
        self._cli_open_for: int | None = None
        self._cli_closed_for: int | None = None
        # Embedded (CCC_EMBEDDED_TERMINAL): the ClaudePane this screen mounted last — remembered
        # at once, because mounting and removing finish later (_sync_embedded_pane).
        self._embedded_pane: ClaudePane | None = None
        # The list filter (f, list_filter), and the conversation opened from
        # its results: that one stays listed until the filter changes — a
        # "*" match stops being new the moment you look at it.
        self._filter = list_filter.ListFilter()
        self._filter_kept: int | None = None

    def sidebar_buttons(self):
        return [NEW_CONVERSATION_BUTTON, ARCHIVE_BUTTON, RETRY_BUTTON]

    def retry_target(self):
        return self.storage.find_queue(self._current_queue_id) if self._current_queue_id is not None else None

    def top_sidebar_buttons(self):
        return [FILTER_BUTTON, RENAME_BUTTON]

    def compose_sidebar_section(self):
        yield Horizontal(self._make_button(FILTER_BUTTON), self._make_button(RENAME_BUTTON), id="sidebar-strip-row")
        # Disabled while hidden: the screen's first focus would land in it.
        yield Input(placeholder=_("name, folder · ! ? x *"), id="conversation-filter", disabled=True)
        yield ConversationList(id="conversation-queue-list", classes="sidebar-list")
        yield Static(_("Nothing matches"), id="conversation-filter-empty")
        # No Claude Code key hints here: the menu keeps its size in every
        # view — they are in the bar under Claude Code (cli_bar).

    def compose_main(self):
        yield Static(id="conversation-header")
        yield Vertical(id="conversation-transcript")
        yield Static(id="conversation-status")
        yield MessageBox(placeholder=_("Message Claude… (Enter send · Ctrl+J new line · Ctrl+E editor · Esc back)"), id="message-input")
        yield Horizontal(id="prompt-presets-bar")
        yield PixelBanner(self.BANNER_TEXT, id="no-conversation")

    async def on_mount(self) -> None:
        self.query_one("#no-conversation").border_title = _("Archive") if self.ARCHIVED else _("Conversations")
        table = self.query_one("#conversation-queue-list", DataTable)
        table.cursor_type = "row"
        table.show_header = False
        self._rebuild_queue_list()
        if self._current_queue_id not in self._listed_ids:
            self._current_queue_id = self._listed_ids[0] if self._listed_ids else None
        elif self._focus_input_on_mount:
            self._unfold_group_of(self._current_queue_id)  # opened for it
        if self._current_queue_id is not None:
            # Don't rely solely on RowHighlighted firing for the initial
            # cursor position (focus timing can vary once Header/Footer
            # siblings are present) — select the queue explicitly so the
            # right panel is deterministically populated on open.
            self._move_cursor_to_current()
            await self._show_queue(self._current_queue_id)
        else:
            self._sync_cli_pane()
        # set_interval accepts an async callback directly (confirmed live
        # with a standalone sandbox script — no run_worker wrapper needed).
        self.set_interval(2.0, self._refresh_current)
        if not self.ARCHIVED:
            self._refresh_git_status()
            self.set_interval(GIT_STATUS_SECONDS, self._refresh_git_status)
        await self._refresh_prompt_presets_bar()
        if self._focus_input_on_mount:
            if not self._focus_cli():
                self.query_one("#message-input", MessageBox).focus()

    def _rebuild_queue_list(self) -> None:
        table = self.query_one("#conversation-queue-list", DataTable)
        # Recreate the column too: after a plain clear() the auto width
        # stays at the header's ("Queue") and every name gets cut to 5 chars.
        table.clear(columns=True)
        self._column_key = table.add_column(_("Queue"))
        rows = self._list_rows()
        self._rows = [row for row, _label in rows]
        self._queue_ids = [row for row in self._rows if isinstance(row, int)]
        table.heading_rows = {i for i, row in enumerate(self._rows) if isinstance(row, str)}
        self._list_signature = [label.plain for _id, label in rows]
        self._row_keys = [table.add_row(label) for _id, label in rows]
        self.query_one("#conversation-filter-empty", Static).display = self._filter.active and not rows

    def _update_list_labels(self, rows: list[tuple[int | str, Text]]) -> None:
        table = self.query_one("#conversation-queue-list", DataTable)
        for i, (_id, label) in enumerate(rows):
            if label.plain != self._list_signature[i]:
                table.update_cell(self._row_keys[i], self._column_key, label, update_width=True)
        self._list_signature = [label.plain for _id, label in rows]

    def _list_rows(self) -> list[tuple[int | str, Text]]:
        """The list's rows: group headings (ccc.groups) and the
        conversations under them — none under a folded heading (the filter
        shows every group unfolded)."""
        unseen = self.storage.unseen_queue_ids()
        waiting = self.storage.queues_waiting_for_approval()
        labels: dict[int, Text] = {}
        markers: dict[int, tuple[str, str]] = {}
        queues = []
        for q in self.storage.list_queues():
            if q.archived != self.ARCHIVED:
                continue
            is_unseen = q.id in unseen and q.id != self._current_queue_id
            if not self._filter.matches(q, is_unseen, q.id in waiting) and q.id != self._filter_kept:
                continue
            queues.append(q)
            markers[q.id] = queue_marker(q, is_unseen, q.id in waiting)
            label = queue_label(q, is_unseen, self.storage.count_pending_items(q.id), needs_approval=q.id in waiting)
            status = self._git_status.get(q.id)
            if status is not None and status.short():
                label.append(f" {status.short()}", style="cyan")
            labels[q.id] = label
        folded = set() if self._filter.active else groups.collapsed(self.storage)
        rows: list[tuple[int | str, Text]] = []
        self._listed_ids, self._group_of = [], {}
        for group, members in groups.grouped(queues, self.storage.conversation_groups(), groups.mode(self.storage)):
            self._listed_ids += [q.id for q in members]
            if group is not None:
                self._group_of.update((q.id, group.key) for q in members)
                rows.append((group.key, self._heading(group.title, len(members), group.key in folded,
                                                      [markers[q.id] for q in members])))
                if group.key in folded:
                    continue
            rows += [(q.id, Text.assemble("  ", labels[q.id]) if group is not None else labels[q.id]) for q in members]
        return rows

    @staticmethod
    def _heading(title: str, count: int, folded: bool, markers: list[tuple[str, str]]) -> Text:
        """▾ shop (3) · ?1 ●2 — what its conversations are doing, also
        while it's folded."""
        label = Text.assemble(("▸ " if folded else "▾ ", "bold"), (title, "bold"), (f" ({count})", "dim"))
        counts: dict[tuple[str, str], int] = {}
        for marker in markers:
            if marker[0] in HEADING_MARKERS:
                counts[marker] = counts.get(marker, 0) + 1
        if counts:
            label.append(" ·", style="dim")
            for (icon, style), n in sorted(counts.items(), key=lambda item: HEADING_MARKERS.index(item[0][0])):
                label.append(f" {icon}{n}", style=style)
        return label

    def _refresh_git_status(self) -> None:
        """Lines changed and commits ahead/behind for each conversation's
        folder — only where Claude may run commands (ccc runs git only
        there, see gitops)."""
        from ccc import gitops

        queues = [q for q in self.storage.list_queues()
                  if not q.archived and gitops.claude_can_run_commands(q)]

        def work() -> None:
            by_path: dict[str, object] = {}
            result = {}
            for q in queues:
                if q.project_path not in by_path:
                    by_path[q.project_path] = gitops.work_status(q.project_path)
                result[q.id] = by_path[q.project_path]
            self.app.call_from_thread(self._git_status_ready, result)

        self.run_worker(work, thread=True, group="git-status", exclusive=True)

    def _git_status_ready(self, result: dict) -> None:
        self._git_status = result

    def _move_cursor_to_current(self) -> None:
        """Onto the open conversation — or its folded group's heading."""
        self._cursor_group = None
        self._restore_cursor()

    def _restore_cursor(self) -> None:
        """After a rebuild: back onto the heading the cursor was on, else
        the open conversation (or its folded group's heading)."""
        target = self._cursor_group if self._cursor_group in self._rows else self._current_queue_id
        if target not in self._rows:
            target = self._group_of.get(self._current_queue_id)
        if target in self._rows:
            table = self.query_one("#conversation-queue-list", DataTable)
            table.move_cursor(row=self._rows.index(target))

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # RowHighlighted fires on arrow-key movement (confirmed live: no
        # Enter needed), unlike RowSelected which only fires on
        # Enter/click — this is what makes switching immediate, per the
        # design. One from before a rebuild (the cursor moved on since) is
        # left alone.
        if event.cursor_row >= len(self._rows) or event.cursor_row != event.data_table.cursor_row:
            return
        row = self._rows[event.cursor_row]
        if isinstance(row, str):
            self._cursor_group = row  # a heading: the conversation stays
            return
        self._cursor_group = None
        if row != self._current_queue_id or self._shown is None:
            await self._show_queue(row)

    async def _show_queue(self, queue_id: int) -> None:
        from ccc import gitops

        self._current_queue_id = queue_id
        queue = self.storage.find_queue(queue_id)
        if queue is not None:
            # @ in the message box offers this conversation's files.
            self.query_one("#message-input", MessageBox).set_mention_source(
                queue.project_path, gitops.claude_can_run_commands(queue))
        if self._filter.active and queue_id in self._queue_ids:
            self._filter_kept = queue_id
        self.storage.mark_seen(queue_id)
        self._sync_cli_pane()
        self.sync_retry_button()
        self._load_transcript(queue_id)
        await self._render_transcript(queue_id)

    # --- Claude Code pane (interactive conversations) ----------------------

    def _cli_queue(self) -> Queue | None:
        """The selected conversation, when it runs as an interactive CLI."""
        if self._current_queue_id is None:
            return None
        queue = self.storage.find_queue(self._current_queue_id)
        return queue if queue is not None and queue.delivery_mode.managed and not queue.archived else None

    def _shows_nothing(self, queue: Queue | None) -> bool:
        """Nothing selected (the last one archived), or an archived
        interactive one: no Claude Code to show."""
        return queue is None or (queue.delivery_mode.managed and queue.archived)

    def _sync_cli_pane(self) -> None:
        """Open, switch or close the Claude Code pane to match the selected
        conversation. Closed while another window (dialog, Settings) is on
        top, so that one gets the whole width."""
        queue = self.storage.find_queue(self._current_queue_id) if self._current_queue_id is not None else None
        self.set_class(self._shows_nothing(queue), "-no-conversation")
        if cli_pane.embedded():
            self._sync_embedded_pane()
            return
        if not cli_pane.available():
            return
        queue = self._cli_queue()
        # app.screen, not is_current: that is also true under a dialog.
        want = queue is not None and self.app.screen is self and queue.id != self._cli_closed_for
        shown = cli_pane.find()
        if want and shown is None and self._cli_open_for == queue.id:
            # It was open and is gone: you closed it (Ctrl+]).
            self._cli_closed_for = queue.id
            want = False
        if want:
            if not self.has_class("-cli-open"):
                # Hide the transcript at once and repaint: opening the pane
                # takes a moment, and until then the old full-width look showed.
                self.add_class("-cli-open")
                self.call_after_refresh(self._sync_cli_pane)
                return
            want = cli_pane.open(queue.id, queue.name, self.storage.db_path) is not None
        elif shown is not None or cli_pane.find_bar() is not None:
            # Also when Claude's pane ended by itself (archived: its
            # session was stopped; Ctrl+]): the bar under it would stay
            # and take the whole column.
            cli_pane.close()
        self._cli_open_for = queue.id if want else None
        if want != self.has_class("-cli-open"):
            self.set_class(want, "-cli-open")
            self.refresh_bindings()  # Alt+0 / Ctrl+] in the key strip

    def claude_pane(self) -> ClaudePane | None:
        """The Claude Code frame ccc draws itself (CCC_EMBEDDED_TERMINAL), if open."""
        panes = self.query(ClaudePane)
        return panes.first() if panes else None

    def _sync_embedded_pane(self) -> None:
        """Embedded (cli_pane.embedded): the conversation's Claude Code is a
        ClaudePane in the place of the transcript, instead of a tmux pane
        beside ccc. Closed while a dialog / Settings / another view is on top.

        Called from many events, some of them before the previous call has
        finished: mounting and removing a pane are asynchronous, so a query
        still finds a pane being removed and doesn't find one just mounted.
        The pane is therefore remembered at once (self._embedded_pane), and
        there is never more than one for the selected conversation."""
        queue = self._cli_queue()
        want = queue is not None and self.app.screen is self and queue.id != self._cli_closed_for
        keep = self._embedded_pane
        if not (want and keep is not None and keep.queue_id == queue.id):
            keep = None
        for pane in self.query(ClaudePane):  # every other one goes, also a stray duplicate
            if pane is not keep and not getattr(pane, "_ccc_removing", False):
                pane._ccc_removing = True
                pane.remove()
        stale = self._embedded_pane
        if stale is not None and stale is not keep and not getattr(stale, "_ccc_removing", False):
            stale._ccc_removing = True  # may not be in the DOM yet: the query missed it
            stale.remove()
        if want and keep is None:
            self.add_class("-cli-open")
            self._embedded_pane = ClaudePane(queue.id, queue.name, self.storage)
            self.query_one("#main").parent.mount(self._embedded_pane)
        elif not want:
            self.remove_class("-cli-open")
            self._embedded_pane = None
        self._cli_open_for = queue.id if want else None

    def on_claude_pane_closed(self, message: ClaudePane.Closed) -> None:
        # Ctrl+] (or the session ended): closed until Enter in the list.
        message.stop()
        self._cli_closed_for = message.queue_id
        self._sync_cli_pane()
        self.query_one("#conversation-queue-list").focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("focus_claude", "close_claude"):
            return self.has_class("-cli-open")
        return super().check_action(action, parameters)

    def action_focus_claude(self) -> None:
        pane = self.claude_pane()
        if pane is not None:
            if pane.has_keyboard():
                self.query_one("#conversation-queue-list").focus()
            else:
                pane.focus_terminal()
            return
        cli_pane.focus()

    def action_close_claude(self) -> None:
        # Like Ctrl+] in the pane: closed until Enter in the list.
        self._cli_closed_for = self._cli_open_for
        self._sync_cli_pane()

    def _focus_cli(self) -> bool:
        """Put the keyboard into the selected conversation's Claude Code
        (the pane, or full screen outside tmux). False if it isn't one."""
        queue = self._cli_queue()
        if queue is None:
            return False
        self._cli_closed_for = None
        if cli_pane.embedded():
            self._sync_cli_pane()
            if (pane := self.claude_pane()) is not None:
                pane.focus_terminal()
        elif cli_pane.available():
            self._sync_cli_pane()
            cli_pane.focus()
        else:
            self._open_cli_full_screen(queue.id)
        return True

    def _open_cli_full_screen(self, queue_id: int) -> None:
        """Outside tmux there's no window to split: ccc steps aside and the
        CLI takes the terminal until Ctrl+]."""
        try:
            with self.app.suspend():
                subprocess.run([sys.executable, "-m", "ccc.attach_cli", str(queue_id)])
        except Exception as exc:  # SuspendNotSupported (e.g. textual serve)
            self.notify(_("Can't open Claude Code here ({error}).").format(error=type(exc).__name__), severity="error")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "conversation-queue-list":
            return
        if event.cursor_row < len(self._rows) and isinstance(self._rows[event.cursor_row], str):
            self._toggle_group(self._rows[event.cursor_row])
            return
        self._conversation_chosen()

    def _conversation_chosen(self) -> None:
        """Enter / a click on a conversation in the list."""
        if not self._focus_cli():
            self.query_one("#message-input", MessageBox).focus()

    def on_screen_suspend(self) -> None:
        # A dialog / Settings / another view on top: give it the window.
        self._close_own_cli_pane()
        self.remove_class("-cli-open")

    def on_unmount(self) -> None:
        self._close_own_cli_pane()

    def _close_own_cli_pane(self) -> None:
        """Close the pane only if this screen opened it: going from Archive
        to Conversations, Archive is unmounted after Conversations opened
        its pane — closing it then looked like Ctrl+], and it stayed shut."""
        if cli_pane.embedded():
            if (pane := self.claude_pane()) is not None:
                pane.remove()
        elif self._cli_open_for is not None and cli_pane.available():
            cli_pane.close()
        self._cli_open_for = None

    def _load_transcript(self, queue_id: int) -> bool:
        """Read new transcript lines into the cache. Returns True if any new
        events arrived. A queue whose transcript doesn't exist yet (a new
        conversation before its first run) is looked up again each call."""
        cached = self._cache.setdefault(queue_id, {"path": None, "offset": 0, "events": []})
        if cached["path"] is None:
            cached["path"] = find_transcript_path(self.storage.get_queue(queue_id).session_id)
            if cached["path"] is None:
                return False
        events, offset = parse_transcript_events(cached["path"], since_byte_offset=cached["offset"])
        cached["offset"] = offset
        cached["events"].extend(events)
        return bool(events)

    async def _render_transcript(self, queue_id: int) -> None:
        """Show a conversation's transcript pane (switching queues): the one
        kept from last time, brought up to date, else a new one. Only the
        newest MAX_RENDERED_EVENTS events are mounted — a long resumed
        session has thousands, and mounting each costs ~30ms."""
        queue = self.storage.get_queue(queue_id)
        pending = self.storage.list_pending_items(queue_id)
        events: list[TranscriptEvent] = self._cache[queue_id]["events"]
        self._update_header(queue)
        self.query_one("#conversation-status", Static).update(self._status_for(queue, pending))

        view = self._views.pop(queue_id, None)
        if view is not None and (view.queued is None or view.key[1] > len(events)):
            await self._drop(view)  # was empty (or the cache restarted)
            view = None
        # display, not a class: a class change restyles every widget inside
        # (~1 s for a long transcript), display only hides the container.
        for other in self._views.values():
            other.container.display = False
        if view is None:
            view = await self._mount_view(queue_id, events, pending)
        else:
            view.container.display = True
            if view.key != self._view_key(queue_id, events, pending):
                await self._append_new_events(view, events[view.key[1]:], pending)
                view.key = self._view_key(queue_id, events, pending)
        self._views[queue_id] = view
        self._shown = queue_id
        while len(self._views) > VIEWS_KEPT:
            await self._drop(self._views.pop(next(iter(self._views))))

    @staticmethod
    def _view_key(queue_id: int, events: list[TranscriptEvent], pending) -> tuple:
        return (queue_id, len(events), tuple(i.id for i in pending))

    @staticmethod
    async def _drop(view: _View) -> None:
        if view.filler is not None:
            view.filler.cancel()
        await view.container.remove()

    async def _drop_view(self, queue_id: int) -> None:
        view = self._views.pop(queue_id, None)
        if view is not None:
            await self._drop(view)
        if self._shown == queue_id:
            self._shown = None

    async def _mount_view(self, queue_id: int, events: list[TranscriptEvent], pending) -> _View:
        host = self.query_one("#conversation-transcript", Vertical)
        view = _View(VerticalScroll(classes="transcript-view"))
        view.key = self._view_key(queue_id, events, pending)
        if not events and not pending:
            await host.mount(view.container)
            await view.container.mount(Static(_("No conversation yet."), id="transcript-placeholder"))
            return view
        shown = [e for e in events if e.kind != "tool_result"]
        recent = shown[-MAX_RENDERED_EVENTS:]
        older, first = recent[:-FIRST_RENDERED_EVENTS], recent[-FIRST_RENDERED_EVENTS:]
        widgets = []
        if len(shown) > len(recent):
            widgets.append(Static(
                _("… {n} earlier messages not shown").format(n=len(shown) - len(recent)), classes="tool-call-line"))
        results = {e.tool_use_id: e.text for e in events if e.kind == "tool_result" and e.tool_use_id}
        first_widgets = [self._render_event(e, results, view.tool_results) for e in first]
        view.queued = Vertical(*self._queued_widgets(pending), classes="queued-messages")
        # One mount call = one layout pass, much faster than mounting
        # each widget separately.
        await host.mount(view.container)
        await view.container.mount(*widgets, *first_widgets, view.queued)
        view.container.scroll_end(animate=False)
        if older and first_widgets:
            view.filler = self.run_worker(self._mount_older(view, older, results, first_widgets[0]))
        return view

    async def _mount_older(self, view: _View, older: list[TranscriptEvent], results: dict[str, str], anchor) -> None:
        """The older events of a newly shown conversation, newest chunk
        first, each in front of the last."""
        container = view.container
        while older:
            chunk, older = older[-RENDER_CHUNK_EVENTS:], older[:-RENDER_CHUNK_EVENTS]
            widgets = [self._render_event(e, results, view.tool_results) for e in chunk]
            at_bottom = container.scroll_y >= container.max_scroll_y - 1
            await container.mount(*widgets, before=anchor)
            if at_bottom:
                container.scroll_end(animate=False)
            anchor = widgets[0]
            await asyncio.sleep(0)

    async def _append_new_events(self, view: _View, new_events: list[TranscriptEvent], pending) -> None:
        """Incremental update while a conversation is open: add only the new
        events (before the queued bubbles) and refresh the queued list,
        instead of re-mounting the whole transcript every 2 seconds."""
        container, queued_box = view.container, view.queued
        results = {e.tool_use_id: e.text for e in new_events if e.kind == "tool_result" and e.tool_use_id}
        # A result for a call that's already on screen: fill it in.
        for tool_use_id, text in results.items():
            widget = view.tool_results.get(tool_use_id)
            if widget is not None:
                widget.update(Content(text or _("(no output)")))
        shown = [e for e in new_events if e.kind != "tool_result"]
        if shown:
            await container.mount(*[self._render_event(e, results, view.tool_results) for e in shown], before=queued_box)
        await queued_box.remove_children()
        await queued_box.mount(*self._queued_widgets(pending))
        container.scroll_end(animate=False)

    @staticmethod
    def _queued_widgets(pending) -> list[Static]:
        return [
            Static(
                Content.assemble((_("You"), "bold"), "  ", (_("queued"), "dim"), "\n", item.prompt_text),
                classes="message-box message-box-queued",
            )
            for item in pending
        ]

    def _render_event(self, event: TranscriptEvent, results: dict[str, str], tool_results: dict[str, Static]):
        # event.text is arbitrary conversation content (human-typed or a
        # model's tool input/reply). It must never go through Textual's
        # markup parsing: escaping isn't enough — a tool preview cut mid-way
        # through something like `[data-section=\"quick…` crashed the
        # parser even after escape(). Content keeps it as plain text;
        # Claude's replies go through the Markdown widget instead.
        time_str = event.timestamp.astimezone().strftime("%H:%M")  # UTC in transcripts
        if event.kind == "tool_call":
            result = Static(
                Content(results.get(event.tool_use_id, _("(waiting for result…)")) or _("(no output)")),
                classes="tool-result",
            )
            if event.tool_use_id:
                tool_results[event.tool_use_id] = result
            return Collapsible(
                Static(Content(event.detail or event.text), classes="tool-input"),
                result,
                title=Content("⚙ " + event.text),
                collapsed=True,
                classes="tool-call",
            )
        header = Static(Content.assemble((_("You") if event.kind == "human" else "Claude", "bold"), "  ", (time_str, "dim")))
        if event.kind == "human":
            return Vertical(header, Static(Content(event.text)), classes="message-box message-box-human")
        return Vertical(header, Markdown(event.text), classes="message-box message-box-assistant")

    def _update_header(self, queue: Queue) -> None:
        parts = [
            (queue.name, "bold"), "  ", (queue.project_path, "dim"), "  ·  ", describe_flags(queue.launch_flags),
            "  ·  ", (_("auto-continue on"), "green") if queue.auto_continue else (_("auto-continue off"), "yellow"),
        ]
        status = self._git_status.get(queue.id)
        if status is not None and status.short():
            parts += ["\n", (_("git: "), "bold"), (status.short(), "cyan")]
            if status.base:
                parts += [("  " + _("(↑↓ against {base})").format(base=status.base), "dim")]
        if queue.archived:
            # Nothing runs for it: no remote control, no CLI to open.
            parts += ["\n", (_("archived: "), "bold"), (_("x restores it, Enter restores and opens it, Delete deletes it"), "cyan")]
        elif queue.delivery_mode == DeliveryMode.REMOTE_CONTROL:
            parts += ["\n", (_("remote control: "), "bold"), (queue.remote_control_url or _("starting…"), "cyan")]
        if queue.delivery_mode.managed and not queue.archived:
            parts += ["\n", ("Claude Code CLI: ", "bold"),
                      (_("Enter in the list opens it") + ("" if cli_pane.available() else _(" (full screen, Ctrl+] back)")),
                       "cyan")]
        if queue.first_run_flags:
            parts += ["  ·  ", (_("first run: ") + " ".join(queue.first_run_flags), "yellow")]
        self.query_one("#conversation-header", Static).update(Content.assemble(*parts))

    def _status_for(self, queue: Queue, pending) -> Content:
        requests = self.storage.pending_permission_requests(queue.id)
        if requests:
            status = Content.assemble(
                (_("? Waiting for your approval: {tool} — press p").format(tool=requests[0].tool_name), "bold yellow"),
                _("  ·  {n} queued").format(n=len(pending)) if pending else "",
            )
        else:
            status = self._status_text(queue, len(pending), pending[0].not_before if pending else None)
        return Content.assemble(status, *self._shared_folder(queue, pending))

    def _shared_folder(self, queue: Queue, pending) -> list:
        """Who else works in this folder (folders): a warning, and why a
        queued task waits."""
        from ccc import folders

        others = folders.neighbours(self.storage, queue)
        if not others:
            return []
        parts = [("\n" + _("⚠ Same folder as: {names} — tasks take turns; a worktree lets them run side by side").format(
            names=", ".join(q.name for q in others[:3]) + (" …" if len(others) > 3 else "")), "yellow")]
        running = next((q for q in others if q.status == QueueStatus.RUNNING), None)
        if pending and running is not None and queue.status != QueueStatus.RUNNING \
                and queue.delivery_mode == DeliveryMode.HEADLESS:
            parts.append(("\n" + _("Waits until {name} finishes its task.").format(name=running.name), "yellow"))
        return parts

    @staticmethod
    def _status_text(queue: Queue, pending: int, next_at: datetime | None = None) -> Content:
        if queue.status == QueueStatus.RUNNING:
            status = (_("● Claude is working…"), "bold green")
        elif queue.status == QueueStatus.WAITING_RETRY and queue.next_retry_at and (queue.last_error or "").startswith("usage reserve"):
            percent = queue.last_error.removeprefix("usage reserve ").strip()
            status = (_("‖ Holding for your usage reserve ({percent}) until {time}").format(
                percent=percent, time=f"{queue.next_retry_at:%H:%M}"), "bold yellow")
        elif queue.status == QueueStatus.WAITING_RETRY and queue.next_retry_at:
            status = (_("‖ Waiting for usage limit reset until {time}").format(time=f"{queue.next_retry_at:%H:%M}"), "bold yellow")
        elif queue.status == QueueStatus.PAUSED_LIMIT:
            if queue.next_retry_at and queue.next_retry_at > datetime.now():
                status = (_("‖ Paused at usage limit, resets {time}").format(time=f"{queue.next_retry_at:%H:%M}"), "bold magenta")
            else:
                status = (_("‖ Limit reset — press r to continue"), "bold magenta")
        elif queue.paused:
            status = (_("‖ Queue paused — t, p to resume"), "bold yellow")
        elif queue.status == QueueStatus.BLOCKED_ERROR:
            line = (queue.last_error or "").replace("\n", " ")[:120]
            status = (_("✖ Blocked: {error} — press r to retry").format(error=line), "bold red")
        else:
            status = (_("Ready"), "dim")
            if next_at is not None and next_at > datetime.now():
                when = f"{next_at:%H:%M}" if next_at.date() == datetime.now().date() else f"{next_at:%Y-%m-%d %H:%M}"
                status = (_("Next task at {time}").format(time=when), "cyan")
        return Content.assemble(status, _("  ·  {n} queued").format(n=pending) if pending else "")

    async def _refresh_current(self) -> None:
        if self._current_queue_id is not None and self.app.screen is self:
            # You're looking at it: replies to it aren't "new".
            self.storage.mark_seen(self._current_queue_id)
        rows = self._list_rows()
        if [row for row, _label in rows] != self._rows:
            # New/removed conversation or heading, or another order.
            self._rebuild_queue_list()
            self._restore_cursor()
        elif [label.plain for _id, label in rows] != self._list_signature:
            # Only a label changed (marker, pending count, git status, context
            # fill): update those cells. A rebuild resets the hover and scroll
            # of the list, which flickered under the mouse.
            self._update_list_labels(rows)
        self._sync_cli_pane()
        self.sync_retry_button()
        if self._current_queue_id is None:
            return
        queue_id = self._current_queue_id
        self._load_transcript(queue_id)
        queue = self.storage.get_queue(queue_id)
        pending = self.storage.list_pending_items(queue_id)
        events = self._cache[queue_id]["events"]
        self._update_header(queue)
        self.query_one("#conversation-status", Static).update(self._status_for(queue, pending))
        view = self._views.get(queue_id) if self._shown == queue_id else None
        if view is None:
            await self._render_transcript(queue_id)
            return
        key = self._view_key(queue_id, events, pending)
        if key == view.key:
            return
        if view.queued is None or view.key[1] > len(events):
            await self._render_transcript(queue_id)  # was empty: now it has a transcript
            return
        await self._append_new_events(view, events[view.key[1]:], pending)
        view.key = key

    async def on_message_box_submitted(self, event: MessageBox.Submitted) -> None:
        if await self._enqueue_text(event.text):
            event.box.text = ""

    async def _enqueue_text(self, text: str) -> bool:
        text = text.strip()
        if not text or self._current_queue_id is None:
            return False
        send_message(self.storage, self._current_queue_id, text)
        await self._refresh_current()
        return True

    # --- preset prompts (Alt+1 … Alt+9) ----------------------------------

    async def _refresh_prompt_presets_bar(self) -> None:
        bar = self.query_one("#prompt-presets-bar", Horizontal)
        await bar.remove_children()
        presets = load_prompt_presets(self.storage)
        keys = keymap.current(self.storage)
        await bar.mount_all(
            Static(bar_item(n, p, keys), id=f"prompt-preset-{n}", classes="prompt-preset-item")
            for n, p in enumerate(presets, start=1)
        )
        if presets:
            await bar.mount(Static(_("⏎ sends  ·  ✎ inserts"), id="prompt-presets-legend"))
        # A class, not bar.display: an inline display would beat the
        # -no-conversation rule hiding everything in #main.
        bar.set_class(not presets, "-empty")

    async def on_screen_resume(self) -> None:
        # Back from Settings (or any window): presets may have changed.
        await self._refresh_prompt_presets_bar()
        self._sync_cli_pane()

    async def on_click(self, event) -> None:
        widget = event.widget
        if widget is not None and widget.has_class("prompt-preset-item") and widget.id:
            await self.action_prompt_preset(int(widget.id.removeprefix("prompt-preset-")))

    async def action_prompt_preset(self, number: int, as_character: bool = False) -> None:
        if self._cli_queue() is not None and not self.query_one("#message-input", MessageBox).has_focus:
            # An interactive conversation: the preset goes into its Claude Code.
            from ccc.cli_input import apply_preset

            error = await asyncio.to_thread(apply_preset, self.storage, self._current_queue_id, number)
            if error:
                self.notify(error, severity="warning")
            return
        presets = load_prompt_presets(self.storage)
        box = self.query_one("#message-input", MessageBox)
        if number > len(presets) and as_character and box.has_focus:
            # No preset there: it was most likely a real "§" (Czech keyboard)
            # or "•" being typed — type it.
            box.insert(ALT_DIGIT_TEXT[number])
            return
        if number > len(presets):
            self.notify(_("No preset on Alt+{n} — add one in Settings (Ctrl+P).").format(n=number), severity="warning")
            return
        preset = presets[number - 1]
        if not preset.send_now:
            box.insert(preset.text)
            box.focus()
            return
        if self._current_queue_id is None:
            self.notify(_("Select a conversation first."), severity="warning")
            return
        await self._enqueue_text(preset.text)
        self.notify(preset.name, title=_("Sent"))

    def open_conversation(self, queue_id: int) -> None:
        if self.storage.get_queue(queue_id).archived != self.ARCHIVED:
            # Its list is the other view's (Conversations / Archive).
            view = "archive" if not self.ARCHIVED else "conversations"
            self._navigate(view, self._make_view(view, select_queue_id=queue_id))
            return
        # Already on this screen: just select it in place.
        self._rebuild_queue_list()
        if queue_id not in self._queue_ids and self._filter.active:
            self._close_filter()  # the filter hides it
        self._unfold_group_of(queue_id)
        self._current_queue_id = queue_id
        self._move_cursor_to_current()
        self.run_worker(self._open_and_focus(queue_id), exclusive=True, group="show-queue")

    async def _open_and_focus(self, queue_id: int) -> None:
        await self._show_queue(queue_id)
        if not self._focus_cli():
            self.query_one("#message-input", MessageBox).focus()

    def action_retry_queue(self) -> None:
        if self._current_queue_id is None:
            return
        # No in-process runtime to notify (this screen never executes
        # anything) — the daemon picks it up on its next poll.
        if self.storage.retry_queue(self._current_queue_id):
            self.run_worker(self._refresh_current(), exclusive=True, group="refresh")

    def action_tasks(self) -> None:
        from ccc.tui.tasks import TasksModal

        if self._current_queue_id is None:
            return

        def closed(_result) -> None:
            self.run_worker(self._refresh_current(), exclusive=True, group="refresh")

        self.app.push_screen(TasksModal(self.storage, self._current_queue_id), closed)

    def action_toggle_archive(self) -> None:
        if self._current_queue_id is None:
            return
        queue = self.storage.get_queue(self._current_queue_id)
        self.storage.set_queue_archived(queue.id, not queue.archived)
        # It moved to the other view's list.
        self._select_neighbour()
        self.run_worker(self._refresh_current(), exclusive=True, group="refresh")

    def action_delete_conversation(self) -> None:
        if self._current_queue_id is None:
            return
        queue = self.storage.get_queue(self._current_queue_id)
        if queue.status == QueueStatus.RUNNING:
            self.app.push_screen(
                ConfirmModal(
                    _("Can't delete"),
                    _("Claude is working on '{name}' right now — wait or pause it first (t, p).").format(name=queue.name),
                    can_confirm=False,
                )
            )
            return

        def answered(yes: bool) -> None:
            if not yes:
                return
            self.storage.delete_queue(queue.id)
            self._cache.pop(queue.id, None)
            self._select_neighbour()
            self.run_worker(self._after_delete(queue.id), exclusive=True, group="refresh")

        self.app.push_screen(
            ConfirmModal(
                _("Delete conversation"),
                _("Delete '{name}' with its queued tasks and run history from ccc?\n"
                  "Claude Code's own transcript stays, so it can still be resumed later.").format(name=queue.name),
            ),
            answered,
        )

    def _select_neighbour(self) -> None:
        """Pick the next (else previous) visible conversation after the
        current one disappears from the list."""
        ids = self._listed_ids
        current = self._current_queue_id
        if current in ids:
            i = ids.index(current)
            rest = ids[i + 1:] + list(reversed(ids[:i]))
        else:
            rest = ids
        self._current_queue_id = rest[0] if rest else None
        self._shown = None

    async def _after_delete(self, deleted_id: int) -> None:
        await self._drop_view(deleted_id)
        self._rebuild_queue_list()
        self._move_cursor_to_current()
        if self._current_queue_id is None:
            self._sync_cli_pane()  # the NO CONVERSATION banner
            self.query_one("#conversation-header", Static).update("")
            self.query_one("#conversation-status", Static).update("")
            return
        await self._show_queue(self._current_queue_id)

    def action_search(self) -> None:
        from ccc.tui.search import SearchModal

        def chosen(queue_id: int | None) -> None:
            if queue_id is not None:
                self.open_conversation(queue_id)  # in Archive when archived

        self.app.push_screen(SearchModal(self.storage), chosen)

    # --- groups (m, Ctrl+Down / Ctrl+Up; ccc.groups) -----------------------

    def _toggle_group(self, key: str) -> None:
        """Fold / unfold a heading (Enter or a click on it). The filter
        shows every group unfolded."""
        if self._filter.active:
            return
        groups.set_collapsed(self.storage, key, key not in groups.collapsed(self.storage))
        self._cursor_group = key
        self._rebuild_queue_list()
        self._restore_cursor()

    def _unfold_group_of(self, queue_id: int) -> None:
        """Unfold the group a conversation is hidden in (opening it)."""
        key = self._group_of.get(queue_id)
        if queue_id not in self._queue_ids and key in groups.collapsed(self.storage):
            groups.set_collapsed(self.storage, key, False)
            self._rebuild_queue_list()

    def action_next_group(self) -> None:
        self._jump_group(1)

    def action_prev_group(self) -> None:
        self._jump_group(-1)

    def _jump_group(self, step: int) -> None:
        """The cursor onto the next / previous heading (around the end)."""
        headings = [i for i, row in enumerate(self._rows) if isinstance(row, str)]
        if not headings:
            return
        table = self.query_one("#conversation-queue-list", DataTable)
        here = table.cursor_row
        if step > 0:
            target = next((i for i in headings if i > here), headings[0])
        else:
            target = next((i for i in reversed(headings) if i < here), headings[-1])
        table.move_cursor(row=target)
        table.focus()

    def action_group(self) -> None:
        """m: on a heading, rename / delete that group (an own one); on a
        conversation, put it into a group."""
        from ccc.tui.groups import GroupModal

        if self._cursor_group is not None and self._cursor_group in self._rows:
            name = groups.Group(self._cursor_group, "").own
            if name is None:
                self.notify(_("A project's group follows the folder — press m on a conversation to put it into a "
                              "group of your own."))
                return
            modal = GroupModal(self.storage, group=name)
        elif self._current_queue_id is not None:
            modal = GroupModal(self.storage, queue_id=self._current_queue_id)
        else:
            return

        def closed(changed: bool) -> None:
            if changed:
                self._rebuild_queue_list()
                self._restore_cursor()

        self.app.push_screen(modal, closed)

    def action_rename(self) -> None:
        """F2: rename the selected conversation."""
        from ccc.tui.rename import RenameModal

        if self._current_queue_id is None:
            return
        queue_id = self._current_queue_id

        def closed(changed: bool) -> None:
            if changed:
                self._rebuild_queue_list()
                self._restore_cursor()

        self.app.push_screen(RenameModal(self.storage, queue_id), closed)

    # --- list filter (f) -------------------------------------------------

    def action_filter(self) -> None:
        self.add_class("-filter-open")
        box = self.query_one("#conversation-filter", Input)
        box.disabled = False
        box.focus()

    def action_back(self) -> None:
        # Esc first cancels the filter, then goes back.
        if self.has_class("-filter-open"):
            self._close_filter()
            self.query_one("#conversation-queue-list", DataTable).focus()
            return
        super().action_back()

    def _close_filter(self) -> None:
        self.remove_class("-filter-open")
        box = self.query_one("#conversation-filter", Input)
        box.value = ""
        box.disabled = True
        self._filter = list_filter.ListFilter()
        self._filter_kept = None
        self._rebuild_queue_list()
        self._move_cursor_to_current()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "conversation-filter":
            return
        self._filter = list_filter.parse(event.value)
        self._filter_kept = None
        self._rebuild_queue_list()
        if self._queue_ids and self._current_queue_id not in self._queue_ids:
            await self._show_queue(self._queue_ids[0])
        self._move_cursor_to_current()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter opens the first result, as Enter on it in the list would
        # (Archive: shows it, doesn't restore it).
        if event.input.id != "conversation-filter" or not self._queue_ids:
            return
        self._current_queue_id = self._queue_ids[0]
        self._move_cursor_to_current()
        await self._open_and_focus(self._queue_ids[0])

    def action_export(self) -> None:
        from ccc.export_paths import CONVERSATIONS, export_dir
        from ccc.search import export_markdown
        from ccc.tui.exports import send_export

        if self._current_queue_id is None:
            return
        path = export_markdown(self.storage, self._current_queue_id, export_dir(self.storage, CONVERSATIONS))
        self.notify(_("Exported to {path}").format(path=path), title=_("Export conversation"))
        send_export(self.app, self.storage, path)

    def action_approve(self) -> None:
        from ccc.tui.permissions import PermissionModal

        if self._current_queue_id is None:
            return
        requests = self.storage.pending_permission_requests(self._current_queue_id)
        if not requests:
            self.notify(_("Nothing is waiting for approval in this conversation."))
            return

        def decided(_answered) -> None:
            self.run_worker(self._refresh_current(), exclusive=True, group="refresh")

        self.app.push_screen(PermissionModal(self.storage, requests[0]), decided)
