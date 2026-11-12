"""Devices of the API (phones): the list in Settings (DevicesPanel), the window with
the QR code that pairs a new one (ccc.pairing), and the activity window."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from rich.text import Text
from textual import work
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from ccc import api, keymap, pairing
from ccc.i18n import _
from ccc.models import REVOKED_BY_COMPUTER
from ccc.storage import Storage
from ccc.tui.layout import CccModal, MODAL_CSS


class PairModal(CccModal):
    """The QR code of a pairing, in a window of its own (it is tall: beside the
    device list it pushed the list and the buttons off a normal terminal, #131).
    Closes by itself once the phone has used the code; dismisses with the new
    device's name, else None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="PairModal") + """
    PairModal #pair-qr {
        width: auto;
        height: auto;
        margin-bottom: 1;
    }
    PairModal .modal-buttons {
        height: 3;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close"))]

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self._waiting: str | None = None  # the stored pairing code while the QR is shown
        self._expires: datetime | None = None
        self._known: set[int] = set()

    def compose(self):
        box = Container(
            Static(id="pair-qr"),
            Static(Text(_("Looking for the API's address…"), style="dim"), id="pair-info"),
            Horizontal(Button(_("Start the API"), id="pair-start-api", variant="primary"),
                       Button(_("Close"), id="pair-close", classes="modal-cancel"), classes="modal-buttons"),
        )
        box.border_title = _("Pair a new device")
        yield box

    def on_mount(self) -> None:
        self.query_one("#pair-start-api", Button).display = False  # only when the API does not answer
        self.set_interval(1.5, self._watch)
        self._make_code()

    def _info(self, text: Text | str) -> None:
        self.query_one("#pair-info", Static).update(text)

    @work(thread=True, exclusive=True, group="pair")
    def _make_code(self) -> None:
        server = pairing.server_url(self.storage)
        shown = None
        if server and not pairing.api_reachable(server):
            shown = server  # a QR for an API that does not answer would only send the phone off to fail
        elif server:
            code, expires = pairing.new_code(self.storage)
            shown = (pairing.qr_lines(pairing.payload(server, code)), server, expires,
                     self.storage.get_setting(pairing.CODE_KEY), {d.id for d in self.storage.list_devices()})
        self.app.call_from_thread(self._shown, shown)

    def _shown(self, shown) -> None:
        start = self.query_one("#pair-start-api", Button)
        start.display = isinstance(shown, str)
        start.disabled = False
        if isinstance(shown, str):
            self._waiting = None
            self.query_one("#pair-qr", Static).update("")
            self._info(Text(_("The API doesn't answer at {server} (is ccc-api running?). Start it and the QR appears.").format(
                server=shown), style="yellow"))
            return
        if shown is None:
            self._info(Text(_("ccc can't tell where the app reaches the API. Put it on the tailnet "
                              "(tailscale serve --bg 8737) or run: ccc api pair --server https://…"), style="yellow"))
            return
        lines, server, expires, stored, known = shown
        self._waiting, self._expires, self._known = stored, expires, known
        self.query_one("#pair-qr", Static).update(Text("\n".join(lines), style="black on white", no_wrap=True))
        self._info(Text(_("Scan it in the app (Connect → Scan QR). Good until {time}, once.").format(
            time=expires.strftime("%H:%M")) + "\n" + _("Address: {server}").format(server=server), style="green"))

    @work(thread=True, exclusive=True, group="pair-watch")
    def _watch(self) -> None:
        """Every 1.5 s: look at the settings in a thread — a locked database must not freeze the window (and its Close button)."""
        waiting = self._waiting
        if waiting is None:
            return
        try:
            current = self.storage.get_setting(pairing.CODE_KEY)
            devices = self.storage.list_devices() if current != waiting else None
        except sqlite3.OperationalError:
            return  # locked right now: next time
        self.app.call_from_thread(self._watched, waiting, current, devices)

    def _watched(self, waiting: str, current: str | None, devices) -> None:
        """Once the phone has used the code (it is gone from the settings), close; when it runs out unused, say so."""
        if self._waiting != waiting:
            return
        if current == waiting:
            if self._expires and datetime.now() > self._expires:
                self._waiting = None
                self.query_one("#pair-qr", Static).update("")
                self._info(Text(_("The code has run out. Pair again for a new one."), style="yellow"))
            return
        self._waiting = None
        new = [d for d in devices or [] if d.id not in self._known]
        self.dismiss(new[-1].name if new else None)

    @work(thread=True, exclusive=True, group="pair-start-api")
    def _start_api(self) -> None:
        ok = pairing.start_api()
        self.app.call_from_thread(self._api_started, ok)

    def _api_started(self, ok: bool) -> None:
        if ok:
            self._info(Text(_("Looking for the API's address…"), style="dim"))
            self._make_code()
        else:
            self.query_one("#pair-start-api", Button).disabled = False
            self._info(Text(_("The API could not be started. See: journalctl --user -u ccc-api"), style="red"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pair-start-api":
            event.button.disabled = True
            self._info(Text(_("Starting the API…"), style="dim"))
            self._start_api()
            return
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class ActivityModal(CccModal):
    """What the devices did (ccc.api_audit): changes, refusals and pairings, the newest first."""

    DEFAULT_CSS = MODAL_CSS.format(cls="ActivityModal") + """
    ActivityModal > Container {
        width: 120;
        max-width: 100%;
    }
    ActivityModal #activity-table {
        height: auto;
        max-height: 24;
        margin-bottom: 1;
    }
    ActivityModal .modal-buttons {
        height: 3;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close"))]

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage

    def compose(self):
        box = Container(
            DataTable(id="activity-table", cursor_type="row", zebra_stripes=True),
            Horizontal(Button(_("Close"), id="activity-close", classes="modal-cancel"), classes="modal-buttons"),
        )
        box.border_title = _("Device activity")
        yield box

    def on_mount(self) -> None:
        table = self.query_one("#activity-table", DataTable)
        table.add_columns(_("When"), _("Device"), _("Request"), _("Result"), _("Address"))
        for e in self.storage.list_audit(200):
            request = f"{e['method']} {e['path']}" + (f"  {e['detail']}" if e["detail"] else "")
            # Text: what a request carried is not markup.
            table.add_row(Text(e["at"][:19].replace("T", " ")), Text(e["device_name"] or "—"), Text(request),
                          Text(str(e["status"])), Text(e["ip"] or "—"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class DevicesPanel(Vertical):
    """The devices signed in to the API, right in Settings → Connection and updates
    (#186): the list, sign out, the history of the signed-out ones and
    the activity. Pairing a new one is a section of its own above it (PairModal)."""

    DEFAULT_CSS = """
    DevicesPanel {
        height: auto;
    }
    DevicesPanel #devices-table {
        height: auto;
        max-height: 14;
        margin-bottom: 1;
    }
    DevicesPanel #devices-info {
        height: auto;
        margin-bottom: 1;
    }
    DevicesPanel .devices-buttons {
        height: 3;
    }
    DevicesPanel .devices-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self._history = False  # showing the signed-out devices (read only) instead of the active ones

    def compose(self):
        yield DataTable(id="devices-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="devices-info")
        yield Horizontal(
            Button(_("Sign out"), id="devices-revoke"),
            Button(_("History"), id="devices-history"),
            Button(_("Activity"), id="devices-activity"),
            classes="devices-buttons",
        )

    def on_mount(self) -> None:
        self._columns()
        self.refresh_list()
        self.set_interval(3, self._poll)  # a phone signed out elsewhere disappears by itself

    def _columns(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        table.clear(columns=True)
        table.add_columns(_("Device"), _("Paired"), _("Last seen"), _("Address"), *([_("Signed out")] if self._history else []))

    @staticmethod
    def _when(moment: datetime | None) -> str:
        return moment.strftime("%Y-%m-%d %H:%M") if moment else _("never")

    def _read(self) -> list:
        if self._history:
            devices = [d for d in self.storage.list_devices(include_revoked=True) if d.revoked_at]
            devices.sort(key=lambda d: d.revoked_at, reverse=True)
            return devices
        return self.storage.list_devices()

    @work(thread=True, exclusive=True, group="devices-poll")
    def _poll(self) -> None:
        """The periodic reload reads in a thread: a database locked by the daemon must not freeze the TUI."""
        history = self._history
        try:
            devices = self._read()
        except sqlite3.OperationalError:
            return  # locked right now: next time
        self.app.call_from_thread(self._polled, history, devices)

    def _polled(self, history: bool, devices: list) -> None:
        if history == self._history:  # not switched meanwhile
            self._show(devices)

    def refresh_list(self, select: int | None = None) -> None:
        """Reload the list, keeping the selected device (or selecting `select`)."""
        self._show(self._read(), select)

    def _show(self, devices: list, select: int | None = None) -> None:
        table = self.query_one("#devices-table", DataTable)
        if select is None and table.row_count:
            select = int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        row = table.cursor_row
        table.clear()
        for device in devices:
            cells = [device.name, self._when(device.created_at), self._when(device.last_seen_at), device.last_ip or "—"]
            if self._history:
                cells.append(f"{self._when(device.revoked_at)}  {api.revoked_label(device, short=True)}")
            table.add_row(*cells, key=str(device.id))
        ids = [d.id for d in devices]
        if ids:
            table.move_cursor(row=ids.index(select) if select in ids else min(row, len(ids) - 1))
        self.query_one("#devices-revoke", Button).disabled = self._history or not devices
        if not devices:
            self._info(Text(_("No device has been signed out yet.") if self._history
                            else _("No device is signed in yet — pair one."), style="dim"))
        elif self._history:
            self._info(Text(_("History of the signed-out devices — read only."), style="dim"))

    def _info(self, text: Text | str) -> None:
        self.query_one("#devices-info", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handler = {"devices-revoke": self._revoke, "devices-history": self._toggle_history,
                   "devices-activity": lambda: self.app.push_screen(ActivityModal(self.storage))}.get(event.button.id or "")
        if handler:
            event.stop()
            handler()

    def _toggle_history(self) -> None:
        """Switch between the devices signed in and the history of the signed-out ones."""
        self._history = not self._history
        self.query_one("#devices-history", Button).label = _("Active") if self._history else _("History")
        self._columns()
        self._info("")
        self.refresh_list(select=-1)

    def paired(self, name: str | None) -> None:
        """A device has just been paired (PairModal): show it selected."""
        if self._history:
            self._toggle_history()
        self.refresh_list()
        if name:
            table = self.query_one("#devices-table", DataTable)
            for index, device in enumerate(self.storage.list_devices()):
                if device.name == name:
                    table.move_cursor(row=index)
            self._info(Text(_("Paired: {name}").format(name=name), style="green"))

    def _revoke(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        if not table.row_count:
            return
        device_id = int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        self.storage.revoke_device(device_id, REVOKED_BY_COMPUTER)
        self.refresh_list()
        if table.row_count:
            self._info(Text(_("Signed out."), style="green"))
