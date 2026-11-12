from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from textual.app import App
from textual.widgets import TextArea

from ccc import keymap, tmux
from ccc.export_paths import SCREENSHOTS, export_dir
from ccc.i18n import _
from ccc.storage import Storage
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.exports import send_export
from ccc.tui import theme
from ccc.tui.layout import BUTTON_CSS
from ccc.tui import scrollbar

# How often the Claude Code pane's title bar learns what needs you.
WAITING_SECONDS = 2.0


class CCContinueApp(App):
    TITLE = "ccc"

    """A pure viewer/editor of the queue database. It never executes
    prompts itself — only `ccc-daemon` does that. This app can be
    opened and closed freely (e.g. inside a tmux window over SSH) without
    affecting whether queued prompts keep running."""

    # No command palette (and with it no Maximize / Minimize): what it
    # offered — quit, screenshot, theme — lives in the footer and Settings.
    ENABLE_COMMAND_PALETTE = False
    ESCAPE_TO_MINIMIZE = False

    CSS = BUTTON_CSS

    BINDINGS = [
        keymap.binding("app.settings", "settings", _("Settings"), priority=True),
        # Not Ctrl+T, Claude Code's to-do list key (#31); Alt+S is free there.
        keymap.binding("app.screenshot", "take_screenshot", _("Screenshot"), show=False, priority=True),
        keymap.binding("app.quit", "quit", _("Close"), priority=True),
        keymap.binding("app.jump_waiting", "jump_waiting", _("Next waiting"), show=False, priority=True),
        # Priority: a text box takes Ctrl+E (end of line) itself. Only
        # while one has focus (check_action) — elsewhere the key is free.
        keymap.binding("app.external_editor", "external_editor", _("Editor"), show=False, priority=True),
    ]

    def __init__(self, db_path: str):
        super().__init__()
        scrollbar.install()  # green 3D scrollbar everywhere
        self.storage = Storage(db_path)
        self.register_theme(theme.THEME)
        self.theme = theme.NAME

    def on_key(self, event) -> None:
        """Ctrl+Shift+C (ccc's tmux hands it on as CSI-u) copies the text
        selected with the mouse. Textual only binds Ctrl+C, which ccc must
        not use (#276). Not a binding: the key is reserved in keymap."""
        if event.key == "ctrl+shift+c":
            event.stop()
            text = self.screen.get_selected_text()
            if text:
                self.copy_to_clipboard(text)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return theme.VARIABLES

    async def on_mount(self) -> None:
        # The user's shortcuts (Settings → Keyboard shortcuts).
        self.set_keymap(keymap.load(self.storage))
        from ccc.tui import cli_pane

        # Same frame in every view (see cli_pane.style_window); there the
        # key list is the strip under the window (footer_strip).
        cli_pane.style_window(keymap.current(self.storage))
        cli_pane.hide_chrome()
        if cli_pane.embedded():
            self.set_interval(WAITING_SECONDS, self.publish_waiting_embedded)
        if cli_pane.available():
            # A Claude Code pane left by the previous ccc process (killed,
            # restarted): no screen of this one owns it, so none would close
            # it and every view would sit in the sidebar column next to it.
            # Conversations opens its own again.
            cli_pane.close()
        self.footer_strip = cli_pane.available() and cli_pane.find_strip() is not None
        if self.footer_strip:
            self.set_interval(0.3, self.publish_footer)
        if cli_pane.available() and cli_pane.on_ccc_server():
            self.set_interval(WAITING_SECONDS, self.publish_waiting)
        for screen in self.initial_screens():
            await self.push_screen(screen)

    footer_strip = False
    _published_footer: str | None = None
    _published_waiting: str | None = None
    # True while the shortcuts window waits for a key: no binding fires,
    # every key goes to that window (tui.keys).
    capturing_key = False

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if self.capturing_key:
            return False
        if action == "external_editor":
            return self._editable_box() is not None
        return True

    def _editable_box(self) -> TextArea | None:
        box = self.focused
        return box if isinstance(box, TextArea) and not box.read_only else None

    def action_external_editor(self) -> None:
        """The focused text box's text in an external editor (Ctrl+E):
        ccc steps aside until it's closed, the saved text comes back into
        the box, not sent."""
        from ccc import external_editor

        box = self._editable_box()
        if box is None:
            return
        argv = external_editor.command(self.storage)
        if argv is None:
            self.notify(_("No editor found — install one (e.g. nano) or set $EDITOR."), severity="warning")
            return
        folder = Path(self.storage.db_path).parent
        try:
            with self.suspend():
                text = external_editor.edit(argv, box.text, folder)
        except Exception as exc:  # SuspendNotSupported (textual serve), the editor failing to start
            self.notify(_("Can't open the editor here ({error}).").format(error=type(exc).__name__), severity="error")
            return
        if text is not None:
            box.text = text
            box.move_cursor(box.document.end)
        box.focus()

    def get_key_display(self, binding) -> str:
        # Ctrl keys as Textual shows them (^p); others the way ccc names
        # them everywhere (Alt+S, Ctrl+], +).
        if binding.key_display:
            return binding.key_display
        if binding.key.startswith("ctrl+") and len(binding.key) == 6:
            return super().get_key_display(binding)
        return keymap.pretty(binding.key)

    def apply_keymap(self) -> None:
        """The saved shortcuts changed: use them everywhere — bindings,
        the menu's and buttons' keys, ccc's tmux keys."""
        from ccc.tui import cli_pane
        from ccc.tui.layout import SidebarScreen

        self.set_keymap(keymap.load(self.storage))
        for screen in self.screen_stack:
            if isinstance(screen, SidebarScreen):
                screen.refresh_key_labels()
        cli_pane.apply_keys(keymap.current(self.storage), self.storage.db_path)

    def publish_footer(self) -> None:
        """Hand the current screen's keys to the strip under the window."""
        import subprocess

        from ccc.tui.footer_strip import FOOTER_OPTION, encode, footer_items

        # The name of the view (also under a window opened on top of it).
        title = next((s.SUB_TITLE for s in reversed(self.screen_stack) if getattr(s, "SUB_TITLE", "")), "")
        value = encode(footer_items(self), title)
        if value != self._published_footer:
            self._published_footer = value
            subprocess.run(tmux.own("set-option", "-w", "-t", os.environ["TMUX_PANE"], FOOTER_OPTION, value),
                           capture_output=True)

    def copy_to_clipboard(self, text: str) -> None:
        """Textual's copy is an OSC 52 that ccc's tmux drops (set-clipboard
        external); load-buffer -w hands the text to the clipboard of the
        terminal and into tmux's buffer, which Ctrl+Shift+V pastes."""
        super().copy_to_clipboard(text)
        from ccc.tui import cli_pane

        if cli_pane._on_ccc_server():
            import subprocess

            subprocess.run(tmux.own("load-buffer", "-w", "-"), input=text.encode(),
                           capture_output=True, timeout=5)

    def copy_to_clipboard_threadsafe(self, text: str) -> None:
        """For the clipboard bridge's thread (tui.claude_pane)."""
        self.call_from_thread(self.copy_to_clipboard, text)

    def publish_waiting_embedded(self) -> None:
        """The conversations that need you → the title of the Claude Code
        frame ccc draws itself (see publish_waiting for the tmux pane)."""
        pane = getattr(self.screen, "claude_pane", lambda: None)()
        if pane is None:
            return

        def work() -> str:
            from ccc import attention

            return attention.summary(attention.waiting(self.storage, exclude=pane.queue_id), pane.waiting_room())

        self.run_worker(work, thread=True, group="publish-waiting", exclusive=True, name="waiting-embedded")

    def on_worker_state_changed(self, event) -> None:
        worker = event.worker
        if worker.name == "waiting-embedded" and worker.state.name == "SUCCESS":
            pane = getattr(self.screen, "claude_pane", lambda: None)()
            if pane is not None:
                pane.set_waiting(worker.result or "")

    def publish_waiting(self) -> None:
        """The conversations that need you → the Claude Code pane's title
        bar (ccc.attention), in a thread: tmux and the database, never
        the UI's time."""
        def work() -> None:
            from ccc import attention
            from ccc.tui import cli_pane

            room = cli_pane.waiting_room()
            text = "" if room is None else attention.summary(
                attention.waiting(self.storage, exclude=self._looking_at()), room)
            if text != self._published_waiting:
                self._published_waiting = text
                cli_pane.publish_waiting(text)

        self.run_worker(work, thread=True, group="publish-waiting", exclusive=True)

    def _looking_at(self) -> int | None:
        """The conversation in the Claude Code pane, else the one selected."""
        from ccc.tui import cli_pane

        shown = cli_pane.find() if cli_pane.available() else None
        if shown is not None:
            return shown[1]
        embedded = getattr(self.screen, "claude_pane", lambda: None)()
        if embedded is not None:
            return embedded.queue_id
        return getattr(self.screen, "_current_queue_id", None)

    def action_jump_waiting(self) -> None:
        """Open the most urgent conversation that needs you (Alt+N; from
        the Claude Code pane too, see cli_pane.key_bindings)."""
        from ccc import attention
        from ccc.tui.layout import SidebarScreen

        items = attention.waiting(self.storage, exclude=self._looking_at())
        if not items:
            self.notify(_("No conversation needs you right now."))
            return
        target = items[0].queue_id
        screen = self.screen
        if hasattr(screen, "open_conversation"):
            screen.open_conversation(target)
        elif isinstance(screen, SidebarScreen):
            screen._navigate("conversations", screen._make_view("conversations", select_queue_id=target))
        else:
            self.notify(_("Close this window first (Esc)."), severity="warning")

    def initial_screens(self) -> list:
        return [DashboardScreen(self.storage)]

    async def action_quit(self) -> None:
        """Close ccc for you. In ccc's own tmux (the always-on instance)
        that means detaching your terminal — quitting would only restart
        the TUI a second later, and other devices stay attached; on ccc's
        server Ctrl+Q is a tmux key doing just that (cli_pane), this is
        the fallback (e.g. q on the Dashboard). Elsewhere: quit."""
        from ccc import instance

        if instance.inside_ccc_tmux():
            import subprocess

            subprocess.run(tmux.own("detach-client"), capture_output=True)
            return
        self.exit()

    def action_settings(self) -> None:
        from ccc.tui.settings import SettingsModal

        if not isinstance(self.screen, SettingsModal):
            self.push_screen(SettingsModal(self.storage))

    def action_take_screenshot(self) -> None:
        folder = export_dir(self.storage, SCREENSHOTS)
        path = self.save_screenshot(f"ccc-{datetime.now():%Y-%m-%d_%H-%M-%S}.svg", str(folder))
        self.notify(path, title=_("Screenshot saved"))
        send_export(self, self.storage, Path(path))


def run_app(db_path: str) -> None:
    app = CCContinueApp(db_path)
    app.run()
