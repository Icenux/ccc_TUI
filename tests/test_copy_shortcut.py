"""Ctrl+Shift+C copies the text selected in ccc (#276)."""
from unittest.mock import MagicMock

from ccc.tui.app import CCContinueApp


def test_ctrl_shift_c_copies_selection():
    app = CCContinueApp.__new__(CCContinueApp)
    app.copy_to_clipboard = MagicMock()
    screen = MagicMock()
    screen.get_selected_text.return_value = "hello"
    event = MagicMock(key="ctrl+shift+c")
    CCContinueApp.screen = property(lambda self: screen)
    try:
        app.on_key(event)
    finally:
        del CCContinueApp.screen
    app.copy_to_clipboard.assert_called_once_with("hello")
    event.stop.assert_called_once()
