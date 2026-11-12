"""The Update window: the new release on GitHub with its notes, and the
button that applies it (ccc.updates) — fetch, install, restart."""
from __future__ import annotations

from rich.text import Text
from textual import work
from textual.containers import Container, Horizontal
from textual.widgets import Button, Static

from ccc import keymap, updates
from ccc.i18n import _
from ccc.storage import Storage
from ccc.tui.layout import CccModal, MODAL_CSS


class UpdateModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="UpdateModal") + """
    UpdateModal > Container {
    }
    UpdateModal #update-info {
        margin-bottom: 1;
    }
    UpdateModal .modal-buttons {
        height: 3;
    }
    UpdateModal .modal-buttons Button {
        margin-right: 1;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close"))]

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self._busy = False

    def compose(self):
        box = Container(
            Static(id="update-info"),
            Horizontal(
                Button(_("Update now"), id="update-apply", classes="modal-submit"),
                Button(_("Check again"), id="update-check"),
                Button(_("Close"), id="update-close", classes="modal-cancel"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("Update ccc")
        yield box

    def on_mount(self) -> None:
        self._show()

    def _show(self, message: Text | None = None) -> None:
        state = updates.load(self.storage)
        reason = updates.refusal(self.storage) if state.available else None
        text = Text()
        if state.error:
            text.append(_("Can't look for the update: {error}").format(error=state.error) + "\n", style="red")
        elif state.available:
            text.append(_("ccc {version} is out (installed: {installed}):").format(
                version=state.version, installed=state.installed) + "\n", style="bold")
            for line in state.notes_lines()[:updates.NOTES_LINES]:
                text.append(f"  {line}\n")
            if len(state.notes_lines()) > updates.NOTES_LINES:
                text.append("  …\n", style="dim")
        elif state.checked_at:
            text.append(_("ccc is up to date.") + "\n", style="green")
        else:
            text.append(_("Not looked yet.") + "\n", style="dim")
        if state.checked_at:
            text.append(_("Looked {when}").format(when=state.checked_at.replace("T", " ")) + "\n", style="dim")
        if reason:
            text.append("\n" + reason + "\n", style="yellow")
        if message is not None:
            text.append("\n").append_text(message)
        self.query_one("#update-info", Static).update(text)
        self.query_one("#update-apply", Button).disabled = not state.available or bool(reason) or self._busy
        self.query_one("#update-check", Button).disabled = self._busy

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"update-apply": self._apply, "update-check": self._check, "update-close": self.action_close}.get(
            event.button.id or "", lambda: None)()

    def _apply(self) -> None:
        self._busy = True
        self._show(Text(_("Updating…"), style="dim"))
        self._apply_worker()

    def _check(self) -> None:
        self._busy = True
        self._show(Text(_("Looking…"), style="dim"))
        self._check_worker()

    @work(thread=True, exclusive=True, group="update")
    def _apply_worker(self) -> None:
        ok, message = updates.apply(self.storage)
        self.app.call_from_thread(self._done, Text(message, style="green" if ok else "red"))

    @work(thread=True, exclusive=True, group="update")
    def _check_worker(self) -> None:
        updates.check(self.storage)
        self.app.call_from_thread(self._done, None)

    def _done(self, message: Text | None) -> None:
        self._busy = False
        self._show(message)

    def action_close(self) -> None:
        self.dismiss(None)
