import re
import subprocess

import pytest

from ccc.tui import cli_pane


class FakeTmux:
    """Records tmux calls; panes: {pane_id: {option: value}}."""

    def __init__(self, width=200):
        self.calls = []
        self.panes = {"%0": {}}
        self.width = width
        self.next_id = 1
        self.features = ["xterm*:clipboard:ccolour", "rxvt*:ignorefkeys"]
        self.server_options = {}

    def __call__(self, *args):
        self.calls.append(args)
        out = ""
        if args[0] == "list-panes":
            option = re.search(r"#\{(@[\w-]+)\}", args[-1]).group(1)
            out = "\n".join(f"{p} {o.get(option, '')}" for p, o in self.panes.items())
        elif args[0] == "display-message":
            out = str(self.width)
        elif args[0] == "split-window":
            pane = f"%{self.next_id}"
            self.next_id += 1
            self.panes[pane] = {}
            out = pane
        elif args[0] == "set-option" and args[1] == "-p":
            self.panes[args[3]][args[4]] = args[5]
        elif args[:3] == ("show-options", "-s", "terminal-features"):
            out = "\n".join(f"terminal-features[{i}] {v}" for i, v in enumerate(self.features))
        elif args[0] == "kill-pane":
            self.panes.pop(args[2], None)
        elif args[:2] == ("set-option", "-s") and len(args) == 4:
            self.server_options[args[2]] = args[3]
        elif args[:2] == ("set-option", "-su"):
            self.server_options.pop(args[2], None)
        elif args[:2] == ("show-options", "-sv"):
            out = self.server_options.get(args[2], "")
        return subprocess.CompletedProcess(args, 0, out + "\n", "")


@pytest.fixture
def tmux(monkeypatch):
    fake = FakeTmux()
    monkeypatch.setattr(cli_pane, "_tmux", fake)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/ccc,123,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    return fake


def test_available_only_inside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert not cli_pane.available()
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/ccc,1,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    assert cli_pane.available()


def test_open_splits_next_to_ccc_keeping_the_sidebar(tmux):
    pane = cli_pane.open(4)
    split = next(c for c in tmux.calls if c[0] == "split-window")
    assert split[split.index("-l") + 1] == str(200 - cli_pane.SIDEBAR_COLUMNS - 1)
    assert split[-1].endswith("-m ccc.attach_cli 4 'C-]'")
    assert "-d" in split  # ccc keeps the keyboard until focus()
    assert cli_pane.find() == (pane, 4)
    assert ("set-option", "-t", "%0", "mouse", "on") in tmux.calls
    # You can see which pane you type in, and what each one is.
    assert ("set-option", "-w", "-t", "%0", "pane-border-status", "top") in tmux.calls
    # No title over ccc itself (#250: no bar at the top).
    assert ("set-option", "-pu", "-t", "%0", "@ccc-title") in tmux.calls


def test_typing_here_only_while_claude_code_is_open():
    border = dict(cli_pane.BORDER_OPTIONS)["pane-border-format"]
    assert "#{?#{&&:#{pane_active},#{P:#{?#{@ccc-cli},1,}}}," in border


def test_open_titles_the_pane_with_the_conversation(tmux):
    pane = cli_pane.open(4, "Kompas")
    assert ("set-option", "-p", "-t", pane, "@ccc-title", "Claude Code · Kompas") in tmux.calls


def test_open_switches_the_existing_pane(tmux):
    pane = cli_pane.open(4)
    tmux.calls.clear()
    assert cli_pane.open(9) == pane
    assert tmux.calls[1][:4] == ("respawn-pane", "-k", "-t", pane)
    assert cli_pane.find() == (pane, 9)
    tmux.calls.clear()
    cli_pane.open(9)  # already shown: nothing restarted
    assert not any(c[0] in ("respawn-pane", "split-window") for c in tmux.calls)


def test_mouse_left_alone_on_a_foreign_tmux(tmux, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    cli_pane.open(4)
    assert not any(c[0] == "set-option" and ("mouse" in c or "pane-border-status" in c) for c in tmux.calls)


def test_focus_zooms_on_narrow_windows(tmux):
    pane = cli_pane.open(4)
    cli_pane.focus()
    assert ("select-pane", "-t", pane) in tmux.calls
    assert not any(c[0] == "resize-pane" and "-Z" in c for c in tmux.calls)
    tmux.width = 80
    cli_pane.focus()
    assert ("resize-pane", "-Z", "-t", pane) in tmux.calls


def test_zoom_ccc_for_a_dialog_only_while_claude_is_open(tmux, monkeypatch):
    assert not cli_pane.zoom_ccc()  # no Claude Code pane: ccc has the window already
    cli_pane.open(4)
    zoom = {"on": False}

    def zoomable(*args):
        if args[0] == "display-message" and args[-1] == "#{window_zoomed_flag}":
            return subprocess.CompletedProcess(args, 0, ("1" if zoom["on"] else "0") + "\n", "")
        if args[:2] == ("resize-pane", "-Z"):
            zoom["on"] = not zoom["on"]
        return tmux(*args)

    monkeypatch.setattr(cli_pane, "_tmux", zoomable)
    zoomed = cli_pane.zoom_ccc()
    assert zoomed and zoom["on"]
    assert not cli_pane.zoom_ccc()  # already zoomed: not ours to undo
    cli_pane.unzoom_ccc(False)
    assert zoom["on"]
    cli_pane.unzoom_ccc(zoomed)
    assert not zoom["on"]


def test_close_kills_only_the_cli_pane(tmux):
    cli_pane.close()  # nothing open: nothing killed
    assert not any(c[0] == "kill-pane" for c in tmux.calls)
    pane = cli_pane.open(4)
    cli_pane.close()
    assert ("kill-pane", "-t", pane) in tmux.calls
    assert cli_pane.find() is None


def test_bar_opens_across_the_window_and_keys_are_bound(tmux):
    pane = cli_pane.open(4, "Kompas", "/data/h.db")
    split = [c for c in tmux.calls if c[0] == "split-window"]
    assert len(split) == 3  # Claude, the key strip (once), the bar
    bar_split = next(c for c in split if c[-1].endswith("/data/h.db"))
    # Under Claude Code, in its column — ccc's sidebar column is untouched.
    assert bar_split[1:3] == ("-v", "-d") and "-b" not in bar_split and bar_split[bar_split.index("-l") + 1] == "1"
    assert bar_split[bar_split.index("-t") + 1] == pane
    assert bar_split[-1].endswith("-m ccc.tui.cli_bar /data/h.db")
    bar = cli_pane.find_bar()
    assert bar is not None and bar != pane
    bound = {c[2]: c for c in tmux.calls if c[0] == "bind-key" and c[1] == "-n" and c[2] not in ("C-S-c", "C-S-v", "C-q")}
    assert set(bound) == {"M-0", *[f"M-{n}" for n in range(1, 10)], "C-p", "M-s", "M-n"}
    assert f"select-pane -t {pane}" in bound["M-0"][6]
    assert "#{e|<|:#{window_width},100}" in bound["M-0"][6]  # numeric, not string compare
    assert "preset 3 --queue 4 --db /data/h.db" in bound["M-3"][7] and "#{socket_path}" in bound["M-3"][7]
    # Switching conversations keeps the one bar, rebinds for the new one.
    tmux.calls.clear()
    cli_pane.open(9, "Other", "/data/h.db")
    assert not any(c[0] == "split-window" for c in tmux.calls)
    assert any(c[0] == "bind-key" and c[2] == "M-1" and "--queue 9" in c[7] for c in tmux.calls)


def test_layout_is_restored_when_the_window_resizes(tmux):
    cli_pane.open(4, "Kompas", "/data/h.db")
    bar = cli_pane.find_bar()
    strip = cli_pane.find_strip()
    hook = [c for c in tmux.calls if c[0] == "set-hook"][-1]
    assert hook[1:5] == ("-w", "-t", "%0", "window-resized")
    assert hook[5] == f"resize-pane -t %0 -x 32 ; resize-pane -t {bar} -y 1 ; resize-pane -t {strip} -y 1"
    assert ("resize-pane", "-t", bar, "-y", "1") in tmux.calls  # applied right away too


def test_close_removes_bar_and_keys(tmux):
    cli_pane.open(4, "Kompas", "/data/h.db")
    cli_pane.close()
    # Only the key strip is kept in shape now.
    strip = cli_pane.find_strip()
    assert [c for c in tmux.calls if c[0] == "set-hook"][-1][5] == f"resize-pane -t {strip} -y 1"
    assert cli_pane.find() is None and cli_pane.find_bar() is None
    unbound = {c[2] for c in tmux.calls if c[:2] == ("unbind-key", "-n")}
    assert unbound == {"M-0", *[f"M-{n}" for n in range(1, 10)], "C-p", "M-s", "M-n"}


def test_no_keys_bound_on_a_foreign_tmux(tmux, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    cli_pane.open(4, "Kompas", "/data/h.db")
    assert not any(c[0] == "bind-key" for c in tmux.calls)


def test_bindings_never_take_claude_code_keys():
    keys = {b[2] for b in cli_pane.key_bindings("%0", "%1", 1, "/data/h.db")}
    assert not keys & {"Tab", "BTab", "C-t", "C-d", "C-c", "C-o", "C-r", "C-b", "M-p", "M-o", "M-t", "M-v"}


def test_alt_s_screenshots_ccc_without_moving_the_keyboard():
    binding = next(b for b in cli_pane.key_bindings("%0", "%1", 1, "/data/h.db") if b[2] == "M-s")
    # In ccc it's ccc's key; in Claude it goes to ccc, the keyboard stays.
    assert binding[-2:] == ("send-keys M-s", "send-keys -t %0 M-s")


def test_title_bars_from_the_start_on_the_ccc_server(tmux):
    # Set when the TUI starts, not when Claude opens: a title row showing up
    # later shifted all of ccc down a row (and stayed after switching views).
    cli_pane.style_window()
    assert ("set-option", "-w", "-t", "%0", "pane-border-status", "top") in tmux.calls
    # The only split then: the key strip across the window.
    assert [c[1:6] for c in tmux.calls if c[0] == "split-window"] == [("-v", "-f", "-d", "-l", "1")]


def test_style_window_removes_a_bar_left_without_claude(tmux):
    cli_pane.open(4, "Kompas", "/data/h.db")
    del tmux.panes[cli_pane.find()[0]]  # Claude's pane ended by itself
    cli_pane.style_window()
    assert cli_pane.find_bar() is None
    assert cli_pane.find_strip() is not None


def test_key_strip_is_created_once(tmux):
    cli_pane.style_window()
    cli_pane.style_window()
    strips = [c for c in tmux.calls if c[0] == "split-window"]
    assert len(strips) == 1 and strips[0][-1].endswith("-m ccc.tui.footer_strip %0")
    assert cli_pane.find_strip() is not None


def test_sidebar_width_matches_the_menu():
    import re as _re

    from ccc.tui.layout import SHARED_CSS

    width = int(_re.search(r"#sidebar \{\s*width: (\d+);", SHARED_CSS).group(1))
    assert cli_pane.SIDEBAR_COLUMNS == width


def test_copy_and_paste_keys_on_the_ccc_server(tmux):
    cli_pane.style_window()
    # Never "on": a pane must not be able to set the clipboard by printing OSC 52.
    assert ("set-option", "-s", "set-clipboard", "external") in tmux.calls
    assert not any(c[:3] == ("set-option", "-s", "set-clipboard") and c[3] == "on" for c in tmux.calls)
    assert ("bind-key", "-T", "copy-mode", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel") in tmux.calls
    assert ("bind-key", "-n", "C-S-v", "paste-buffer", "-p") in tmux.calls
    # Ctrl+Shift+C outside a selection does nothing — never Ctrl+C (interrupt) to Claude.
    root_copy = next(c for c in tmux.calls if c[:3] == ("bind-key", "-n", "C-S-c"))
    assert root_copy[3:6] == ("if-shell", "-F", "#{pane_in_mode}")
    assert root_copy[6] == "send-keys -X copy-pipe-and-cancel"
    # In ccc's own pane it's handed on to the TUI as CSI u (ctrl+shift+c), nowhere else.
    assert root_copy[7] == cli_pane.COPY_KEY_TO_CCC
    assert "@ccc-title" in root_copy[7] and "99;6u" in root_copy[7]


def test_clipboard_left_alone_on_a_foreign_tmux(tmux, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    cli_pane.style_window()
    assert not any("set-clipboard" in c or "C-S-v" in c for c in tmux.calls)


def test_ctrl_q_detaches_the_terminal_on_the_ccc_server(tmux):
    cli_pane.style_window()
    assert ("bind-key", "-n", "C-q", "detach-client") in tmux.calls


def test_terminal_feature_added_once_and_copies_removed(tmux):
    cli_pane.style_window()
    assert ("set-option", "-as", "terminal-features", ",*:clipboard:extkeys") in tmux.calls
    tmux.calls.clear()
    tmux.features += ["*:clipboard:extkeys"] * 4  # left by earlier starts
    cli_pane.style_window()
    assert not any(c[:2] == ("set-option", "-as") for c in tmux.calls)
    removed = [c[2] for c in tmux.calls if c[:2] == ("set-option", "-su")]
    assert removed == ["terminal-features[5]", "terminal-features[4]", "terminal-features[3]"]


def test_no_zoom_key_on_the_ccc_server(tmux):
    cli_pane.style_window()
    assert ("unbind-key", "-T", "prefix", "z") in tmux.calls


def test_changed_shortcuts_move_the_tmux_keys(tmux, tmp_path, monkeypatch):
    from ccc import keymap, managed_sessions
    from ccc.storage import Storage

    detach = []
    monkeypatch.setattr(managed_sessions, "set_detach_key", detach.append)
    db = str(tmp_path / "h.db")
    keymap.save(Storage(db), {**keymap.defaults(), "app.screenshot": "f6"})
    cli_pane.open(4, "Kompas", db)
    bound = {c[2] for c in tmux.calls if c[:2] == ("bind-key", "-n")}
    assert "F6" in bound and "M-s" not in bound
    # attach_cli gets the close-Claude key.
    assert next(c for c in tmux.calls if c[0] == "split-window")[-1].endswith("4 'C-]'")

    tmux.calls.clear()
    keys = {**keymap.defaults(), "app.screenshot": "f7", "conversations.close_claude": "f8", "app.quit": "f9"}
    cli_pane.apply_keys(keys, db)
    unbound = {c[2] for c in tmux.calls if c[:2] == ("unbind-key", "-n")}
    rebound = {c[2] for c in tmux.calls if c[:2] == ("bind-key", "-n")}
    assert {"F6", "C-q"} <= unbound  # what was bound before…
    assert {"F7", "F9"} <= rebound  # …gives way to the new keys
    assert ("bind-key", "-n", "F9", "detach-client") in tmux.calls
    assert detach == ["F8"]


def test_alt_n_goes_to_ccc_from_claude_code():
    binding = next(b for b in cli_pane.key_bindings("%0", "%1", 1, "/data/h.db") if b[2] == "M-n")
    # ccc opens the waiting conversation in the pane and focuses it there.
    assert binding[-2:] == ("send-keys M-n", "send-keys -t %0 M-n")


def test_waiting_conversations_are_in_the_claude_code_title_only():
    border = dict(cli_pane.BORDER_OPTIONS)["pane-border-format"]
    assert f"#{{&&:#{{{cli_pane.PANE_OPTION}}},#{{{cli_pane.WAITING_OPTION}}}}}" in border
    assert f"#{{{cli_pane.WAITING_OPTION}}}" in border


def test_publish_waiting_escapes_hashes(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_pane, "_tmux", lambda *args: calls.append(args))
    monkeypatch.setattr(cli_pane, "_own_pane", lambda: "%0")
    cli_pane.publish_waiting("? #1 #[fg=red] waits")
    assert calls == [("set-option", "-w", "-t", "%0", cli_pane.WAITING_OPTION, "? ##1 ##[fg=red] waits")]


def test_whole_frame_is_one_colour():
    options = dict(cli_pane.BORDER_OPTIONS)
    # Active and inactive borders: the same grey line in ccc and in Claude Code (#250).
    assert options["pane-border-style"] == "fg=colour235"
    assert options["pane-active-border-style"] == "fg=colour235,bold"
    assert "colour74" not in options["pane-border-format"]


def test_strip_and_bar_have_no_title_text(tmux):
    cli_pane.open(4, "Kompas", db_path="/tmp/ccc.db")
    titles = [c for c in tmux.calls if c[0] == "set-option" and "@ccc-title" in c]
    assert not any("keys" in str(c[-1]) for c in titles)
    strip, bar = cli_pane.find_strip(), cli_pane.find_bar()
    assert ("set-option", "-pu", "-t", strip, "@ccc-title") in tmux.calls
    assert ("set-option", "-pu", "-t", bar, "@ccc-title") in tmux.calls
