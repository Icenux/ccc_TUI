"""The help window (? on any screen): the user guide in ccc.help plus
every keyboard shortcut."""
from __future__ import annotations

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, OptionList
from textual.widgets.option_list import Option

from ccc import help as help_topics
from ccc import keymap
from ccc.i18n import N_, _
from ccc.tui.layout import MODAL_CSS

# (section, [(ref, description)]): ref is a keymap action id (its key is
# the user's) or, for keys of lists and text fields that aren't ccc's
# bindings, the key itself. Tests check every screen's bindings are listed.
KEY_HELP: list[tuple[str, list[tuple[str, str]]]] = [
    (N_("Everywhere"), [
        ("views.dashboard", N_("Dashboard")),
        ("views.conversations", N_("Conversations")),
        ("views.archive", N_("Archive (archived conversations)")),
        ("views.sessions", N_("Live sessions")),
        ("views.analytics", N_("Analytics")),
        ("views.new_conversation", N_("New conversation")),
        ("views.help", N_("This help")),
        ("views.routines", N_("Routines: prompts on a schedule")),
        ("views.agents", N_("Agents: helpers Claude Code can call")),
        ("views.update", N_("Update ccc — only when a new version is there")),
        ("app.settings", N_("Settings (language, Telegram, queues, presets, shortcuts, backup)")),
        ("app.screenshot", N_("Screenshot of ccc (an SVG in the export folder, see Settings)")),
        ("app.quit", N_("Close ccc (it keeps running; `ccc` opens it again)")),
        ("app.jump_waiting", N_("Open the next conversation that needs you (also from Claude Code)")),
        ("app.external_editor", N_("In a text box: write its text in an external editor (chosen in Settings), "
                                   "it comes back into the box")),
        ("screen.back", N_("Back")),
        ("window.cancel", N_("Close the window / cancel")),
    ]),
    (N_("Dashboard"), [
        ("enter", N_("Open the queue detail")),
        ("dashboard.quit", N_("Close ccc (same as Ctrl+Q)")),
    ]),
    (N_("Live sessions"), [
        ("enter", N_("Send the selected session a prompt")),
        ("sessions.refresh", N_("Reload the session list")),
        ("screen.add_prompt", N_("Add a prompt to a project's queue")),
    ]),
    (N_("Conversations"), [
        ("enter", N_("Open the conversation's Claude Code CLI (in the list) / send the message (in the box)")),
        ("screen.retry", N_("Blocked: run the failed task again / resume one paused at a limit")),
        ("conversations.tasks", N_("Tasks editor (queue, schedule, pause, auto-continue)")),
        ("conversations.approve", N_("Approve a tool call Claude is waiting on")),
        ("conversations.archive", N_("Archive (moves it to Archive, h)")),
        ("conversations.focus_claude", N_("Switch between ccc and Claude Code (while it's open beside ccc)")),
        ("conversations.close_claude",
         N_("Close Claude Code beside ccc (it keeps running; Enter in the list opens it)")),
        ("conversations.search", N_("Search all conversations")),
        ("conversations.filter", N_("Filter the list: name or folder; ! working, ? approval, x error, * new (Esc cancels)")),
        ("conversations.rename", N_("Rename the selected conversation")),
        ("conversations.group", N_("Groups: put the conversation into one of your own; on a group's heading, rename / delete it")),
        ("conversations.next_group", N_("Jump to the next group's heading (Enter or a click folds / unfolds it)")),
        ("conversations.prev_group", N_("Jump to the previous group's heading")),
        ("conversations.export", N_("Export the conversation as Markdown")),
        ("screen.review", N_("Review its changes with a fresh reviewer (once or until it passes)")),
        ("screen.goal", N_("Goal loop: rounds of work, check and review until the goal is met")),
        ("screen.workflow", N_("Workflow: plan, approve, build, check, parallel reviews, arbiter")),
        ("ctrl+j", N_("New line in the message box (Shift+Enter where supported)")),
        ("conversations.preset1", N_("Preset prompt 1–9 (⏎ sends it, ✎ puts it into the message box; set in Settings)")),
        *[(f"conversations.preset{n}", N_("Preset prompt {n}")) for n in range(2, 10)],
    ]),
    (N_("Archive"), [
        ("conversations.archive", N_("Restore the conversation")),
        ("enter", N_("Restore it and open it in Conversations")),
        ("archive.delete", N_("Delete the conversation from ccc (asks first; Claude Code's transcript stays)")),
        ("conversations.search", N_("Search all conversations")),
        ("conversations.filter", N_("Filter the list: name or folder; ! working, ? approval, x error, * new (Esc cancels)")),
        ("conversations.rename", N_("Rename the selected conversation")),
        ("conversations.group", N_("Groups: put the conversation into one of your own; on a group's heading, rename / delete it")),
        ("conversations.next_group", N_("Jump to the next group's heading (Enter or a click folds / unfolds it)")),
        ("conversations.prev_group", N_("Jump to the previous group's heading")),
        ("conversations.export", N_("Export the conversation as Markdown")),
    ]),
    (N_("Tasks editor (t)"), [
        ("tasks.add", N_("Add a task")),
        ("tasks.edit", N_("Edit the task (also Enter)")),
        ("tasks.remove", N_("Remove the task")),
        ("tasks.up", N_("Move up")),
        ("tasks.down", N_("Move down")),
        ("tasks.pause", N_("Pause / resume the queue")),
        ("tasks.auto", N_("Toggle auto-continue")),
        ("window.save", N_("Save in the task editor")),
    ]),
    (N_("Routines (o)"), [
        ("routines.add", N_("Add a routine")),
        ("routines.edit", N_("Edit the routine (also Enter)")),
        ("routines.toggle", N_("Turn it on / off")),
        ("routines.run", N_("Run it now (its next time stays)")),
        ("routines.delete", N_("Delete it (tasks it queued stay)")),
        ("window.save", N_("Save in the routine editor")),
    ]),
    (N_("Agents (A)"), [
        ("agents.add", N_("Add an agent from a template (name, where it is kept, preview of the file)")),
        ("window.save", N_("Create the agent in the new-agent window")),
    ]),
    (N_("Review (v)"), [
        ("window.save", N_("Start the review")),
        ("window.cancel", N_("Close")),
    ]),
    (N_("Goal (g)"), [
        ("window.save", N_("Start the goal loop")),
        ("window.cancel", N_("Close")),
    ]),
    (N_("Workflow (w)"), [
        ("window.save", N_("Start the workflow")),
        ("window.cancel", N_("Close")),
    ]),
    (N_("Approval window (p)"), [
        ("answer.yes", N_("Allow")),
        ("approval.always", N_("Always allow this tool in this conversation")),
        ("answer.no", N_("Deny")),
    ]),
    (N_("Confirmation (e.g. deleting)"), [
        ("answer.yes", N_("Yes")),
        ("confirm.enter", N_("Yes")),
        ("answer.no", N_("No")),
    ]),
    (N_("Analytics"), [
        ("analytics.prev", N_("Previous calendar month")),
        ("analytics.next", N_("Next calendar month")),
    ]),
    (N_("Queue detail"), [
        ("enter", N_("Show the run's commit diff")),
        ("diff.comment", N_("In the diff: comment on the line under the cursor")),
        ("diff.send", N_("In the diff: send the comments to Claude as one prompt")),
        ("queue_detail.revert", N_("Revert the selected run's commit (a new commit; asks first)")),
        ("screen.add_prompt", N_("Add a prompt (this queue's path prefilled)")),
        ("screen.review", N_("Review its changes with a fresh reviewer (once or until it passes)")),
        ("screen.goal", N_("Goal loop: rounds of work, check and review until the goal is met")),
        ("screen.workflow", N_("Workflow: plan, approve, build, check, parallel reviews, arbiter")),
        ("screen.retry", N_("Blocked: run the failed task again / resume one paused at a limit")),
    ]),
    (N_("Preset prompt editor (Settings → Add / Edit)"), [
        ("window.save", N_("Save")),
        ("window.cancel", N_("Cancel")),
    ]),
    (N_("Settings (Ctrl+P)"), [
        ("window.save", N_("Save")),
        ("window.cancel", N_("Cancel (nothing is saved)")),
    ]),
]


def section_of(action_id: str) -> str:
    """The help section an action is listed in first (untranslated)."""
    for section, entries in KEY_HELP:
        if any(ref == action_id for ref, _description in entries):
            return section
    return ""


def pretty_key(ref: str, keys: dict[str, str] | None = None) -> str:
    """The key of a KEY_HELP ref: an action's (the user's `keys`, default
    keys without them) or the fixed key itself."""
    if ref in keymap.BY_ID:
        return keymap.pretty((keys or keymap.defaults())[ref])
    return keymap.pretty(ref)


def keys_markdown(keys: dict[str, str] | None = None) -> str:
    """KEY_HELP as the "Keyboard shortcuts" help topic, with the user's
    `keys` (help/keys.md is this with the defaults, kept in sync by a
    test)."""
    lines = [
        "# " + _("Keyboard shortcuts"),
        "",
        _("Keys without Ctrl or Alt don't work while you're typing in a text field;\n"
          "Ctrl and Alt keys work everywhere. The bar at the bottom of the window\n"
          "lists the keys of the screen you're on — click one to press it."),
        "",
        _("Every shortcut can be changed in Settings → Keyboard shortcuts (or with\n"
          "`ccc keys`); ccc checks the new key is free. The other pages of this guide\n"
          "name the default keys."),
    ]
    for section, entries in KEY_HELP:
        lines += ["", f"## {_(section)}", "", "| " + _("Key") + " | |", "|---|---|"]
        lines += [f"| `{pretty_key(ref, keys)}` | {_(description).replace('{n}', ref[-1]).replace('|', '\\|')} |"
                  for ref, description in entries]
    return "\n".join(lines) + "\n"


class HelpScreen(ModalScreen):
    """The user guide (ccc.help): topics on the left, the selected one on
    the right. Opens on the topic of the screen you came from."""

    DEFAULT_CSS = MODAL_CSS.format(cls="HelpScreen") + """
    HelpScreen > Container {
        width: 95%;
        max-width: 140;
        height: 95%;
        padding: 0 1;
    }
    HelpScreen Horizontal {
        height: 1fr;
    }
    HelpScreen #help-topics {
        width: 34;
        height: 1fr;
        border: none;
        padding: 0;
    }
    HelpScreen #help-scroll {
        width: 1fr;
        height: 1fr;
        padding: 0 1 0 2;
    }
    HelpScreen #help-text {
        margin: 0;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close")), keymap.binding("views.help", "close", _("Close"))]

    def __init__(self, topic: str = "getting-started"):
        super().__init__()
        slugs = [slug for slug, _title in help_topics.TOPICS]
        self.topic = topic if topic in slugs else slugs[0]

    def compose(self):
        topics = OptionList(*[Option(_(title), id=slug) for slug, title in help_topics.TOPICS], id="help-topics")
        box = Container(
            Horizontal(
                topics,
                VerticalScroll(Markdown(id="help-text", open_links=False), id="help-scroll"),
            )
        )
        box.border_title = _("Help  ·  ↑↓ topic  ·  Tab text / topics  ·  Esc close")
        yield box

    def on_mount(self) -> None:
        topics = self.query_one("#help-topics", OptionList)
        topics.highlighted = topics.get_option_index(self.topic)
        topics.focus()
        self.show_topic(self.topic)

    def show_topic(self, slug: str) -> None:
        self.topic = slug
        storage = getattr(self.app, "storage", None)
        keys = keymap.current(storage) if storage is not None else None
        text = keys_markdown(keys) if slug == "keys" else help_topics.for_terminal(help_topics.read(slug))
        self.query_one("#help-text", Markdown).update(text)
        self.query_one("#help-scroll", VerticalScroll).scroll_home(animate=False)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.topic:
            self.show_topic(event.option.id)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        # Links between topics are "tasks.md"; anything else goes to the browser.
        slug = event.href.split("#")[0].removesuffix(".md")
        topics = self.query_one("#help-topics", OptionList)
        if event.href.split("#")[0].endswith(".md") and slug in dict(help_topics.TOPICS):
            topics.highlighted = topics.get_option_index(slug)
        elif event.href.startswith(("http://", "https://")):
            self.app.open_url(event.href)

    def action_close(self) -> None:
        self.dismiss()
