"""The frame is one thin grey line everywhere: tmux's lines and ccc's own (#250)."""
import pytest
from textual.color import Color

from ccc.tui import theme
from ccc.tui.app import CCContinueApp


def _xterm(colour: str) -> str:
    """tmux's colourN as hex (the 6×6×6 cube and the grey ramp)."""
    n = int(colour.removeprefix("colour"))
    if n >= 232:
        grey = 8 + 10 * (n - 232)
        return f"#{grey:02x}{grey:02x}{grey:02x}"
    n -= 16
    return "#" + "".join(f"{(0 if c == 0 else 55 + 40 * c):02x}" for c in (n // 36, n // 6 % 6, n % 6))


def test_tmux_and_ccc_use_the_same_line_colour():
    # tmux's colourN and Textual's hex are one colour of the 256 palette.
    assert _xterm(theme.TMUX_FRAME) == theme.FRAME == theme.VARIABLES["ccc-frame"]
    assert _xterm(theme.TMUX_ACCENT) == theme.ACCENT


def test_theme_colours_stay_distinct_in_a_256_colour_terminal():
    # #76: Textual rounds to the 256 palette in tmux — page, panels, lines and the
    # selected row must not become one colour there.
    from rich.color import Color as RichColor, ColorSystem

    colours = [theme.BACKGROUND, theme.PANEL, theme.LINE, theme.SELECTION, theme.ACCENT, theme.WARNING, theme.ERROR]
    numbers = {RichColor.parse(c).downgrade(ColorSystem.EIGHT_BIT).number for c in colours}
    assert len(numbers) == len(colours)


@pytest.mark.asyncio
async def test_menu_is_a_framed_block_without_tmux_and_gives_way_to_its_frame(tmp_path):
    app = CCContinueApp(str(tmp_path / "history.db"))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        sidebar = app.screen.query_one("#sidebar")
        grey = Color.parse(theme.FRAME)
        # Plain terminal: its own thin frame on all four sides.
        assert {sidebar.styles.border_top[0], sidebar.styles.border_right[0],
                sidebar.styles.border_bottom[0], sidebar.styles.border_left[0]} == {"solid"}
        assert sidebar.styles.border_right[1] == grey
        # In ccc's tmux the top and bottom are tmux's; the menu adds its left
        # line and a right one to Claude Code…
        app.screen.add_class("-tmux-frame")
        app.refresh_css(animate=False)
        await pilot.pause()
        assert sidebar.styles.border_right[0] == "solid" and sidebar.styles.border_left[0] == "solid"
        assert sidebar.styles.border_top[0] == "" and sidebar.styles.border_bottom[0] == ""
        # …and none while Claude Code is open (tmux draws that one).
        app.screen.add_class("-cli-open")
        app.refresh_css(animate=False)
        await pilot.pause()
        assert sidebar.styles.border_right[0] == "" and sidebar.styles.border_left[0] == "solid"


def test_status_bar_is_the_designs_dark_bar_with_the_accent_keys():
    variables = theme.THEME.to_color_system().generate()
    assert variables["footer-background"] == theme.PANEL
    assert variables["footer-key-foreground"] == theme.ACCENT
    assert variables["background"] == "#000000"
