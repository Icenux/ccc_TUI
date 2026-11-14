"""Approve or deny a tool call Claude is waiting on (headless conversation
without "Bypass permissions", see ccc.permission_mcp)."""
from __future__ import annotations

import json

from rich.text import Text
from textual.containers import Container, VerticalScroll
from textual.widgets import Static

from ccc import keymap
from ccc.i18n import _
from ccc.models import PermissionRequest
from ccc.storage import Storage
from ccc.tui.layout import CccModal, MODAL_CSS


class PermissionModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="PermissionModal") + """
    PermissionModal > Container {
        width: 90%;
        max-width: 130;
        height: auto;
        max-height: 80%;
    }
    PermissionModal VerticalScroll {
        height: auto;
        max-height: 24;
        margin: 1 0;
    }
    PermissionModal .modal-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        keymap.binding("answer.yes", "decide('allow')", _("Allow")),
        keymap.binding("approval.always", "decide('always')", _("Always allow")),
        keymap.binding("answer.no", "decide('deny')", _("Deny")),
        keymap.binding("window.cancel", "close", _("Later")),
    ]

    def __init__(self, storage: Storage, request: PermissionRequest):
        super().__init__()
        self.storage = storage
        self.request = request

    def compose(self):
        pretty = json.dumps(self.request.input, indent=2, ensure_ascii=False)
        box = Container(
            Static(Text.assemble(_("Claude wants to use "), (self.request.tool_name, "bold yellow"), ":")),
            VerticalScroll(Static(Text(pretty), id="permission-input")),
            Static(
                _("y allow  ·  a always allow {tool} in this conversation  ·  n deny  ·  Esc later").format(
                    tool=self.request.tool_name),
                classes="modal-hint",
            ),
        )
        box.border_title = _("Approval needed")
        yield box

    def action_decide(self, choice: str) -> None:
        if choice == "always":
            self.storage.always_allow_tool(self.request.queue_id, self.request.tool_name)
        self.storage.decide_permission_request(self.request.id, allow=choice in ("allow", "always"))
        self.dismiss(True)

    def action_close(self) -> None:
        self.dismiss(False)
