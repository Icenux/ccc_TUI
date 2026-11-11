"""`python -m ccc.tui.cli_bar DB_PATH` — the bar under the Claude Code pane
(see cli_pane): the preset prompts (click one, or Alt+1 … Alt+9 anywhere).
The keys that work while Claude Code is open are in ccc's key strip at
the bottom (footer_strip). It's only this bar, not a second ccc."""
from __future__ import annotations

import subprocess
import sys

from rich.text import Text
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Static

from ccc import clipboard_bridge, keymap, tmux
from ccc.cli_input import apply_preset
from ccc.i18n import _
from ccc.prompt_presets import load_prompt_presets
from ccc.storage import Storage
from ccc.tui import cli_pane, theme
from ccc.tui.prompt_presets import bar_item

REFRESH_SECONDS = 2.0


class CliBar(App):
    CSS = """
    Screen {
        background: $panel;
    }
    #bar-presets {
        height: 1;
        padding: 0 1;
    }
    #bar-presets .bar-preset {
        width: auto;
        margin-right: 3;
    }
    #bar-presets .bar-preset:hover {
        background: $boost;
    }
    """
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, db_path: str):
        super().__init__()
        self.storage = Storage(db_path)
        self._shown: list | None = None
        self.register_theme(theme.THEME)
        self.theme = theme.NAME

    def compose(self):
        yield Horizontal(id="bar-presets")

    async def on_mount(self) -> None:
        await self.refresh_presets()
        self.set_interval(REFRESH_SECONDS, self.refresh_presets)
        # Copies made in Claude Code → your device's clipboard.
        current = cli_pane.find() if cli_pane.available() else None
        if current is not None:
            clipboard_bridge.start(current[1])

    async def refresh_presets(self) -> None:
        presets = load_prompt_presets(self.storage)
        if presets == self._shown:
            return
        self._shown = presets
        row = self.query_one("#bar-presets", Horizontal)
        await row.remove_children()
        if presets:
            keys = keymap.current(self.storage)
            await row.mount_all(
                Static(bar_item(n, p, keys), id=f"bar-preset-{n}", classes="bar-preset")
                for n, p in enumerate(presets, start=1)
            )
        else:
            await row.mount(Static(Text(_("No preset prompts — add them in Settings (Ctrl+P)"), style="dim")))

    def on_click(self, event) -> None:
        widget = event.widget
        if widget is None or not (widget.id or "").startswith("bar-preset-"):
            return
        current = cli_pane.find()
        if current is None:
            return
        pane, queue_id = current
        error = apply_preset(self.storage, queue_id, int(widget.id.removeprefix("bar-preset-")))
        if error:
            subprocess.run(tmux.own("display-message", "-d", "4000", error), capture_output=True)
        # Back to Claude: the bar is only clicked, never typed in.
        subprocess.run(tmux.own("select-pane", "-t", pane), capture_output=True)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m ccc.tui.cli_bar DB_PATH")
    CliBar(sys.argv[1]).run()


if __name__ == "__main__":
    main()
