from textual.content import Content, Span

from ccc.tui.help import colour_markers


def test_markers_are_shown_in_their_colour_without_the_word():
    # #303: "blue `●`" showed the word and an orange (code) dot.
    content = Content("blue ● x and gold ⣾⣷⣯ (turning) and ‖", spans=[
        Span(5, 6, ".code_inline"), Span(18, 21, ".code_inline"), Span(36, 37, ".code_inline")])
    out = colour_markers(content)
    assert out.plain == "● x and ⣾ (turning) and ‖"
    styles = {out.plain[s.start:s.end]: str(s.style) for s in out.spans}
    assert "#0087ff" in styles["●"] and "#ffd700" in styles["⣾"] and "magenta" in styles["‖"]


def test_other_inline_code_is_left_alone():
    content = Content("press `n` blue", spans=[Span(7, 8, ".code_inline")])
    assert colour_markers(content) is content
