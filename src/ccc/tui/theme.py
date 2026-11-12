"""ccc's look: one colour theme — black page, near-black panels, thin grey
lines, a mint accent (#250) — plus ccc's own colours, used by every view
and window as $ccc-… variables. The apps hand them to Textual
(App.get_theme_variable_defaults), so they reach every stylesheet already
when it's parsed.

The colours sit on the xterm 256-colour cube, like the buttons
(layout.BUTTON_CSS): in a 256-colour terminal (tmux) Textual rounds to
the nearest one, and two near colours would become one (#76). The design's
colours (#250) are given next to the cube colour they became."""
from __future__ import annotations

import dataclasses

from textual.theme import BUILTIN_THEMES

NAME = "ccc"

BACKGROUND = "#000000"  # the page behind the panels
PANEL = "#080808"  # the menu and the content panels (design #0A0A0A)
LINE = "#262626"  # dividing lines, panel frames
TEXT = "#e4e4e4"  # (design #E6E6E6)
TEXT_MUTED = "#9e9e9e"  # (design #9AA0A6)
TEXT_DIM = "#626262"  # (design #5C6166)
ACCENT = "#87ffaf"  # active, running, the screen's name (design #7BF2A2)
WARNING = "#d7af5f"  # waiting, limits (design #E6C46A)
ERROR = "#d78787"  # blocked, errors (design #E57E7E)
# The selected row (design #0E2216): that dark green rounds to black in 256
# colours — the row would vanish — so the darkest green of the cube.
SELECTION = "#005f00"

# Textual's dark theme on ccc's colours, so that every widget (tables,
# lists, dialogs) sits on the same ground as the page, focus is the accent
# green and the key list at the bottom is ccc's status bar.
THEME = dataclasses.replace(
    BUILTIN_THEMES["textual-dark"], name=NAME, background=BACKGROUND, surface=PANEL, panel="#121212",
    foreground=TEXT, primary=ACCENT, secondary=TEXT_MUTED, accent=ACCENT, success=ACCENT, warning=WARNING,
    error=ERROR,
    variables={
        "footer-background": PANEL,
        "footer-key-foreground": ACCENT,
        "footer-key-background": "transparent",
        "footer-description-foreground": TEXT_MUTED,
        "footer-description-background": "transparent",
        "footer-item-background": "transparent",
        "block-cursor-background": SELECTION,
        "block-cursor-foreground": ACCENT,
        "block-cursor-text-style": "bold",
        "input-cursor-background": ACCENT,
        "input-cursor-foreground": BACKGROUND,
        "border": ACCENT,
        "border-blurred": LINE,
    },
)

VARIABLES = {
    "ccc-background": BACKGROUND,
    "ccc-sidebar": PANEL,  # the menu on the left
    "ccc-panel": PANEL,  # the content panels
    "ccc-line": LINE,  # dividing lines, panel frames
    "ccc-text": TEXT,
    "ccc-text-muted": TEXT_MUTED,
    "ccc-text-dim": TEXT_DIM,
    "ccc-accent": ACCENT,
    "ccc-warning": WARNING,
    "ccc-error": ERROR,
    "ccc-title": ACCENT,  # panel and window titles
    "ccc-selection": SELECTION,  # the selected row
    "ccc-human": "#5f87af",  # your messages
    "ccc-claude": "#5faf87",  # Claude's replies
    "ccc-frame": LINE,  # frame lines (the same grey as the tmux frame, FRAME)
}

# The frame lines — the tmux borders and ccc's own panel lines — are one
# colour: the design's thin grey. colourN is the 256-cube colour Textual's
# hex is rounded to, so a line looks the same on both sides of a border.
FRAME, TMUX_FRAME = LINE, "colour235"
# The accent in tmux: the "typing here" mark on the pane you type in.
TMUX_ACCENT = "colour121"
