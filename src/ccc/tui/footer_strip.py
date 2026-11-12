"""ccc's bottom bar (its key list) as its own row across the whole window.

On ccc's tmux server ccc's window is split — ccc narrows to its menu when
Claude Code opens beside it, and its own footer wouldn't fit there. So
there the footer lives in a 1-row pane under everything, in every view,
which keeps the frame the same everywhere: ccc publishes the current
screen's keys (exactly what its footer would show) as the @ccc-footer
window option (publish), and this strip (`python -m ccc.tui.footer_strip`)
shows them; clicking one presses that key in ccc."""
from __future__ import annotations

import json
import os
import subprocess
from time import monotonic

from rich.cells import cell_len
from rich.text import Text
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from ccc import keymap, tmux
from ccc.tui.theme import ACCENT

FOOTER_OPTION = "@ccc-footer"
POLL_SECONDS = 0.3
# tmux only passes the mouse to the pane it's over, so when the pointer
# leaves the strip no "left" ever arrives: the highlight goes after this
# long without a mouse move instead.
HOVER_IDLE_SECONDS = 0.5

# Textual key names → tmux key names, for clicks (send-keys).
tmux_key = keymap.tmux_key


def footer_items(app) -> list[list[str]]:
    """[key shown, description, tmux key] of what the screen's footer would
    show — the same bindings Textual's Footer lists."""
    items, seen = [], set()
    for _node, binding, enabled, _tooltip in app.screen.active_bindings.values():
        if not binding.show or not enabled or binding.action in seen:
            continue
        seen.add(binding.action)
        items.append([app.get_key_display(binding), binding.description, tmux_key(binding.key) or ""])
    return items


def encode(items: list[list[str]], title: str = "") -> str:
    """The keys (and the screen's name, shown first — #250) as the window option's value."""
    return json.dumps({"title": title, "items": items} if title else items, ensure_ascii=False)


def _load(value: str):
    try:
        return json.loads(value)
    except ValueError:
        return None


def decode(value: str) -> list[list[str]]:
    items = _load(value)
    if isinstance(items, dict):  # with the screen's name
        items = items.get("items")
    return [i for i in items if isinstance(i, list) and len(i) == 3] if isinstance(items, list) else []


def decode_title(value: str) -> str:
    data = _load(value)
    title = data.get("title") if isinstance(data, dict) else None
    return title if isinstance(title, str) else ""


def wrap(items: list[list[str]], width: int, title: str = "") -> list[list[list[str]]]:
    """Items split into rows no wider than `width` (an item is its key, a
    space, its description and a cell of padding either side; the first row
    starts with the screen's name), so the whole key list stays readable in
    a narrow window."""
    rows: list[list[list[str]]] = [[]]
    used = cell_len(title) + 5 if title else 0
    for item in items:
        cost = cell_len(f"{item[0]} {item[1]}") + 2
        if rows[-1] and used + cost > width:
            rows.append([])
            used = 0
        rows[-1].append(item)
        used += cost
    return rows


def _tmux(*args: str) -> subprocess.CompletedProcess:
    # $TMUX points at ccc's server (this runs in its pane); ccc's tmux binary.
    return subprocess.run(tmux.own(*args), capture_output=True, text=True, timeout=5)


class FooterStrip(App):
    def get_theme_variable_defaults(self) -> dict[str, str]:
        from ccc.tui import theme

        return theme.VARIABLES

    CSS = """
    Screen {
        background: $footer-background;
    }
    .strip-title {
        width: auto;
        padding: 0 2;
        margin-right: 1;
        background: $ccc-accent;
        color: $ccc-background;
        text-style: bold;
    }
    #strip-items {
        height: auto;
    }
    .strip-row {
        height: 1;
    }
    .strip-item {
        width: auto;
        padding: 0 1;
    }
    .strip-item.-hover {
        background: $footer-item-background;
        text-style: reverse;
    }
    """
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, main_pane: str):
        super().__init__()
        from ccc.tui import theme

        self.register_theme(theme.THEME)
        self.theme = theme.NAME
        self.main_pane = main_pane
        self._shown: str | None = None
        self._laid_out: tuple[str, int, int] | None = None
        self._hovered: Static | None = None
        self._hover_at = 0.0

    def compose(self):
        yield Vertical(id="strip-items")

    async def on_mount(self) -> None:
        await self.refresh_items()
        self.set_interval(POLL_SECONDS, self.refresh_items)
        self.set_interval(0.1, self.expire_hover)

    def set_hover(self, widget: Static | None) -> None:
        if widget is not self._hovered:
            if self._hovered is not None:
                self._hovered.remove_class("-hover")
            if widget is not None:
                widget.add_class("-hover")
            self._hovered = widget
        self._hover_at = monotonic()

    def expire_hover(self) -> None:
        if self._hovered is not None and monotonic() - self._hover_at > HOVER_IDLE_SECONDS:
            self.set_hover(None)

    def on_mouse_move(self, event) -> None:
        widget = event.widget
        self.set_hover(widget if widget is not None and widget.has_class("strip-item") else None)

    def on_leave(self, event) -> None:
        self.set_hover(None)

    async def refresh_items(self) -> None:
        self._shown = _tmux("show-options", "-wqv", "-t", self.main_pane, FOOTER_OPTION).stdout.strip()
        await self.layout_items()

    async def on_resize(self, event) -> None:
        await self.layout_items()

    async def layout_items(self) -> None:
        value, width, height = self._shown, self.size.width, self.size.height
        if value is None or width == 0 or self._laid_out == (value, width, height):
            return
        self._laid_out = (value, width, height)
        title = decode_title(value)
        rows = wrap(decode(value), width, title)
        column = self.query_one("#strip-items", Vertical)
        self._hovered = None
        await column.remove_children()
        await column.mount_all(
            Horizontal(
                *([Static(title.upper(), classes="strip-title")] if title and number == 0 else []),
                *(
                    Static(Text.assemble((shown, f"bold {ACCENT}"), " ", description.lower()),
                           classes="strip-item", name=key)
                    for shown, description, key in row
                ), classes="strip-row")
            for number, row in enumerate(rows)
        )
        # The pane is as tall as the rows the keys wrap into (tmux puts it
        # back to 1 row when the window is resized, which resizes this app).
        pane = os.environ.get("TMUX_PANE")
        if pane and len(rows) != height:
            _tmux("resize-pane", "-t", pane, "-y", str(len(rows)))

    def on_click(self, event) -> None:
        widget = event.widget
        if widget is None or not widget.has_class("strip-item") or not widget.name:
            return
        # Press that key in ccc, where it belongs — as if typed (-K, through
        # the client), so keys tmux binds while Claude Code is open (Alt+0)
        # do what they do when pressed.
        _tmux("select-pane", "-t", self.main_pane)
        client = _tmux("display-message", "-p", "-t", self.main_pane, "#{client_name}").stdout.strip()
        if client:
            _tmux("send-keys", "-K", "-c", client, widget.name)
        else:
            _tmux("send-keys", "-t", self.main_pane, widget.name)


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m ccc.tui.footer_strip MAIN_PANE")
    FooterStrip(sys.argv[1]).run()


if __name__ == "__main__":
    main()
