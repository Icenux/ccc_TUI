"""The group window (m in Conversations / Archive): put a conversation
into a group of your own — an existing one or a new one — or take it
out; on an own group's heading, rename or delete the group (ccc.groups)."""
from __future__ import annotations

from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from ccc import groups, keymap
from ccc.i18n import _
from ccc.tui.layout import CccModal, MODAL_CSS

# The OptionList entry that takes a conversation out of its own group.
NO_OWN_GROUP = "\0none"


class GroupModal(CccModal):
    """Dismisses True when something changed."""

    DEFAULT_CSS = MODAL_CSS.format(cls="GroupModal") + """
    GroupModal > Container {
        width: 64;
    }
    GroupModal #group-options {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }
    GroupModal .modal-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, storage, queue_id: int | None = None, group: str | None = None):
        """queue_id: put that conversation into a group; group: an own
        group's name to rename / delete."""
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id
        self.group = group

    def compose(self):
        if self.group is not None:
            box = Container(
                Static(_("Name"), classes="modal-label"),
                Input(value=self.group, id="group-name"),
                Static(_("Enter renames it (to an existing group's name: they merge). Deleting the group "
                         "deletes no conversation — they go back to their project's group (or No group)."),
                       classes="modal-hint"),
                Horizontal(Button(_("Delete group"), id="group-delete", variant="error"), classes="modal-buttons"),
            )
            box.border_title = _("Group '{name}'").format(name=self.group)
            yield box
            return
        queue = self.storage.get_queue(self.queue_id)
        current = self.storage.conversation_groups().get(self.queue_id)
        names = sorted(set(self.storage.conversation_groups().values()), key=str.casefold)
        out = (_("— none of its own (its project's group)") if groups.mode(self.storage) == groups.BY_PROJECT
               else _("— No group"))
        options = [Option(("✓ " if n == current else "  ") + n, id=n) for n in names]
        options.append(Option(("✓ " if current is None else "  ") + out, id=NO_OWN_GROUP))
        box = Container(
            Static(_("Put '{name}' into").format(name=queue.name), classes="modal-label"),
            OptionList(*options, id="group-options"),
            Static(_("or a new group"), classes="modal-label"),
            Input(placeholder=_("group name, Enter"), id="group-name"),
        )
        box.border_title = _("Group")
        yield box

    def on_mount(self) -> None:
        if self.group is not None or not self.storage.conversation_groups():
            self.query_one("#group-name", Input).focus()
        else:
            options = self.query_one("#group-options", OptionList)
            current = self.storage.conversation_groups().get(self.queue_id) or NO_OWN_GROUP
            options.highlighted = options.get_option_index(current)
            options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id
        self.storage.set_conversation_group(self.queue_id, None if name == NO_OWN_GROUP else name)
        self.dismiss(True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = " ".join(event.value.split())
        if not name:
            return
        if self.group is None:
            self.storage.set_conversation_group(self.queue_id, name)
        elif name != self.group:
            groups.rename(self.storage, self.group, name)
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "group-delete":
            groups.delete(self.storage, self.group)
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
