from ccc.tui.layout import WORKING_FRAMES


def test_working_dot_has_a_narrow_wedge_that_goes_round():
    """The dot is a braille ring of 8 dots with one missing (a 45° wedge, the narrowest a terminal
    can draw — not a half circle) and the missing one walks round the whole ring."""
    assert len(WORKING_FRAMES) == len(set(WORKING_FRAMES)) == 8
    missing = []
    for frame in WORKING_FRAMES:
        bits = ord(frame) - 0x2800
        assert bin(bits).count("1") == 7
        missing.append(0xFF ^ bits)
    assert sorted(missing) == [1, 2, 4, 8, 16, 32, 64, 128]
