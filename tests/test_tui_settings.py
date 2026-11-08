from pathlib import Path

import pytest
from textual.widgets import Input, Select, Static

from ccc.notify import TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY
from ccc.orchestrator import RESERVE_KEY
from ccc.presets import LaunchChoice, list_presets, save_preset
from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.settings import SettingsModal


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path):
    return CCContinueApp(str(tmp_path / "history.db"))


async def _open_settings(pilot, app):
    await pilot.pause()
    await pilot.press("ctrl+p")
    await pilot.pause()
    assert isinstance(app.screen, SettingsModal)
    return app.screen


def _footer_keys(app):
    return {b.binding.key for b in app.screen.active_bindings.values() if b.binding.show}


@pytest.mark.asyncio
async def test_footer_shows_only_what_the_sidebar_does_not(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        keys = _footer_keys(app)
        assert {"ctrl+p", "ctrl+q", "question_mark"} <= keys
        assert not keys & {"ctrl+t", "ctrl+d"}  # Claude Code's keys (#31)
        assert not keys & {"d", "c", "s", "v", "a", "n", "r", "q"}
        await pilot.press("c")
        await pilot.pause()
        keys = _footer_keys(app)
        assert not keys & {"d", "c", "s", "a", "n", "r", "escape"}
        assert "v" in keys  # the conversation's Review is its own action, not a view


@pytest.mark.asyncio
async def test_no_command_palette_or_maximize(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        assert app.use_command_palette is False
        # Ctrl+P now opens Settings instead of the palette.
        await _open_settings(pilot, app)


@pytest.mark.asyncio
async def test_settings_saves_everything(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal.query_one("#settings-telegram-token", Input).value = "123:abc"
        modal.query_one("#settings-telegram-chat", Input).value = "42"
        modal.query_one("#settings-continue-text", Input).value = "Go on."
        modal.query_one("#settings-reserve", Select).value = "80"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        s = app.storage
        assert s.get_setting(TELEGRAM_TOKEN_KEY) == "123:abc"
        assert s.get_setting(TELEGRAM_CHAT_KEY) == "42"
        assert s.get_setting("continue_text") == "Go on."
        assert s.get_setting(RESERVE_KEY) == "80"


@pytest.mark.asyncio
async def test_empty_token_field_keeps_the_saved_token(tmp_path):
    app = _app(tmp_path)
    app.storage.set_setting(TELEGRAM_TOKEN_KEY, "secret")
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        assert modal.query_one("#settings-telegram-token", Input).value == ""  # never shown
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(TELEGRAM_TOKEN_KEY) == "secret"


@pytest.mark.asyncio
async def test_cancel_saves_nothing(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal.query_one("#settings-reserve", Select).value = "90"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.storage.get_setting(RESERVE_KEY) is None


@pytest.mark.asyncio
async def test_one_theme_with_ccc_colours_whatever_was_saved(tmp_path):
    from textual.app import App

    from ccc.tui import theme

    Storage(tmp_path / "history.db").set_setting("tui_theme", "nord")  # from the old theme picker
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        assert app.theme == theme.NAME
        # Its own, not the test default from conftest.
        assert type(app).get_theme_variable_defaults is not App.get_theme_variable_defaults
        assert app.get_css_variables()["ccc-sidebar"] == theme.VARIABLES["ccc-sidebar"]
        modal = await _open_settings(pilot, app)
        assert not modal.query("#settings-theme")  # no picker


@pytest.mark.asyncio
async def test_reserve_off_and_telegram_test_and_turn_off(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("ccc.tui.settings.send_telegram", lambda t, c, text: sent.append((t, c)))
    app = _app(tmp_path)
    app.storage.set_setting(RESERVE_KEY, "70")
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        assert modal.query_one("#settings-reserve", Select).value == "70"
        modal.query_one("#settings-telegram-token", Input).value = "t0k"
        modal.query_one("#settings-telegram-chat", Input).value = "7"
        modal._send_test()
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        assert sent == [("t0k", "7")]
        # The test went through: "on" right away, not only after Save and reopening.
        assert app.storage.get_setting(TELEGRAM_TOKEN_KEY) == "t0k"
        assert "● on" in modal.query_one("#telegram-state", Static).render().plain
        modal._telegram_off()
        assert app.storage.get_setting(TELEGRAM_TOKEN_KEY) == ""
        modal.query_one("#settings-reserve", Select).value = "off"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(RESERVE_KEY) == ""


@pytest.mark.asyncio
async def test_delete_preset_and_backup(tmp_path):
    app = _app(tmp_path)
    save_preset(app.storage, "yolo", LaunchChoice(frozenset()))
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal.query_one("#settings-preset", Select).value = "yolo"
        modal._delete_preset()
        assert list_presets(app.storage) == {}
        modal._backup()
        assert list((tmp_path / "backups").glob("history-*.db"))


@pytest.mark.asyncio
async def test_screenshot_saves_svg(tmp_path, monkeypatch):
    app = _app(tmp_path)
    app.storage.set_setting("export_folder", str(tmp_path / "out"))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.action_take_screenshot()  # no key until #31
        await pilot.pause()
        assert len(list((tmp_path / "out" / "screenshots").glob("ccc-*.svg"))) == 1


@pytest.mark.asyncio
async def test_export_folder_saved_from_settings(tmp_path):
    from ccc.export_paths import export_folder

    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        app.screen.query_one("#settings-export-folder", Input).value = "~/my-exports"
        app.screen.query_one("#settings-export-send", Select).value = "both"
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert export_folder(app.storage) == Path.home() / "my-exports"
    assert app.storage.get_setting("export_send") == "both"


@pytest.mark.asyncio
async def test_ctrl_q_quits_outside_ccc_tmux(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.return_code == 0


HOOK = "https://discord.com/api/webhooks/123/SECRETTOKEN"


@pytest.mark.asyncio
async def test_discord_webhook_saved_kept_and_validated(tmp_path):
    from ccc.notify import DISCORD_WEBHOOK_KEY

    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal.query_one("#settings-discord-webhook", Input).value = "https://evil.example/x"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsModal)  # refused, window stays
        assert app.storage.get_setting(DISCORD_WEBHOOK_KEY) is None
        modal.query_one("#settings-discord-webhook", Input).value = HOOK
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(DISCORD_WEBHOOK_KEY) == HOOK

        modal = await _open_settings(pilot, app)
        assert modal.query_one("#settings-discord-webhook", Input).value == ""  # never shown
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(DISCORD_WEBHOOK_KEY) == HOOK  # empty field keeps it


@pytest.mark.asyncio
async def test_discord_test_and_turn_off(tmp_path, monkeypatch):
    from ccc.notify import DISCORD_WEBHOOK_KEY

    sent = []
    monkeypatch.setattr("ccc.tui.settings.send_discord", lambda url, text: sent.append(url))
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal._send_discord_test()  # nothing filled in
        assert sent == []
        modal.query_one("#settings-discord-webhook", Input).value = HOOK
        modal._send_discord_test()
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        assert sent == [HOOK]
        assert app.storage.get_setting(DISCORD_WEBHOOK_KEY) == HOOK
        assert "● on" in modal.query_one("#discord-state", Static).render().plain
        modal._discord_off()
        assert app.storage.get_setting(DISCORD_WEBHOOK_KEY) == ""


@pytest.mark.asyncio
async def test_archive_folder_saved_and_archive_now(tmp_path):
    from ccc.analytics_archive import FOLDER_KEY

    app = _app(tmp_path)
    folder = tmp_path / "vault" / "ccc_analytics"
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal._archive_now()  # empty field: archive is off, nothing written
        assert not (tmp_path / "analytics").exists()
        modal.query_one("#settings-archive-folder", Input).value = str(folder)
        modal._archive_now()
        assert (folder / "ccc_analytics.md").exists()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(FOLDER_KEY) == str(folder)


@pytest.mark.asyncio
async def test_ctrl_q_detaches_inside_ccc_tmux(tmp_path, monkeypatch):
    # The always-on instance: quitting would only restart the TUI.
    import subprocess

    calls = []
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/ccc,1,0")
    monkeypatch.setattr(subprocess, "run", lambda args, capture_output=True: calls.append(args))
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert calls == [["tmux", "detach-client"]]
        assert app.is_running


@pytest.mark.asyncio
async def test_another_language_restarts_the_tui(tmp_path, monkeypatch):
    from ccc import i18n

    monkeypatch.delenv("CCC_LANG", raising=False)
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr("ccc.instance.inside_ccc_tmux", lambda: True)
    app = _app(tmp_path)
    exits = []
    monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
    try:
        async with app.run_test(size=(140, 45)) as pilot:
            modal = await _open_settings(pilot, app)
            assert modal.query_one("#settings-language", Select).value == "auto"
            await pilot.press("ctrl+s")  # unchanged: no restart
            await pilot.pause()
            assert exits == [] and app.storage.get_setting(i18n.LANGUAGE_KEY) == ""
            modal = await _open_settings(pilot, app)
            modal.query_one("#settings-language", Select).value = "cs"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.storage.get_setting(i18n.LANGUAGE_KEY) == "cs"
            assert exits == [True]
    finally:
        i18n.set_language("en")


@pytest.mark.asyncio
async def test_editor_choice_offers_only_installed_editors_and_is_saved(tmp_path, monkeypatch):
    from ccc import external_editor

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("nano", "hx", "code"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        select = modal.query_one("#settings-editor", Select)
        assert [value for _label, value in select._options] == ["auto", "nano", "hx"]  # no display: no code
        assert select.value == "auto"
        select.value = "hx"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.storage.get_setting(external_editor.SETTING_KEY) == "hx"
        assert external_editor.command(app.storage) == ("hx",)
        modal = await _open_settings(pilot, app)
        assert modal.query_one("#settings-editor", Select).value == "hx"


@pytest.mark.asyncio
async def test_failed_test_does_not_turn_the_connection_on(tmp_path, monkeypatch):
    from ccc.notify import DISCORD_WEBHOOK_KEY

    def fail(*args):
        raise OSError("down")

    monkeypatch.setattr("ccc.tui.settings.send_telegram", fail)
    monkeypatch.setattr("ccc.tui.settings.send_discord", fail)
    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        modal.query_one("#settings-telegram-token", Input).value = "t0k"
        modal.query_one("#settings-telegram-chat", Input).value = "7"
        modal.query_one("#settings-discord-webhook", Input).value = HOOK
        modal._send_test()
        modal._send_discord_test()
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        assert not app.storage.get_setting(TELEGRAM_TOKEN_KEY)
        assert not app.storage.get_setting(DISCORD_WEBHOOK_KEY)
        assert "○ off" in modal.query_one("#telegram-state", Static).render().plain
        assert "○ off" in modal.query_one("#discord-state", Static).render().plain


@pytest.mark.asyncio
async def test_dashboard_columns_are_chosen_in_settings(tmp_path):
    from textual.widgets import SelectionList

    from ccc import dashboard_columns as dc

    app = _app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        modal = await _open_settings(pilot, app)
        columns = modal.query_one("#settings-dashboard-columns", SelectionList)
        assert set(columns.selected) == set(dc.DEFAULT)
        columns.deselect(dc.PATH)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert dc.PATH not in dc.shown(app.storage)


async def _wait_for(pilot, condition, seconds=10.0):
    """Wait until condition() holds (the pairing code is made in a thread, so how long it takes depends on the machine)."""
    waited = 0.0
    while not condition() and waited < seconds:
        await pilot.pause(0.1)
        waited += 0.1


async def _open_connection_tab(pilot, app):
    from textual.widgets import TabbedContent

    settings = await _open_settings(pilot, app)
    settings.query_one(TabbedContent).active = "settings-tab-connection"
    await pilot.pause()
    return settings


@pytest.mark.asyncio
async def test_devices_are_listed_in_the_tab_with_pairing_above_them(tmp_path, monkeypatch):
    from textual.widgets import Button, DataTable

    from ccc import api, pairing
    from ccc.tui.devices import PairModal

    monkeypatch.setattr(pairing, "tailscale_url", lambda: "https://xia.ts.net:8737")
    app = _app(tmp_path)
    phone, _token = api.add_device(app.storage, "Pixel")
    api.add_device(app.storage, "Tablet")
    app.storage.revoke_device(api.add_device(app.storage, "Old")[0].id, "app")
    async with app.run_test(size=(120, 50)) as pilot:
        settings = await _open_connection_tab(pilot, app)
        # No "Devices…" button any more: the list is right in the tab, below the pairing section.
        assert not settings.query("#settings-devices")
        pair = settings.query_one("#devices-pair", Button)
        table = settings.query_one("#devices-table", DataTable)
        assert pair.region.y < table.region.y
        assert table.row_count == 2  # the signed-out one is only in the history
        pair.press()
        await _wait_for(pilot, lambda: isinstance(app.screen, PairModal) and "Address:" in str(app.screen.query_one("#pair-info").render()))
        assert isinstance(app.screen, PairModal)
        assert app.storage.get_setting(pairing.CODE_KEY)
        assert "Address: https://xia.ts.net:8737" in str(app.screen.query_one("#pair-info").render())
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is settings
        settings.query_one("#devices-revoke", Button).press()
        await pilot.pause()
        assert [d.name for d in app.storage.list_devices()] == ["Tablet"]
        assert phone.id not in [d.id for d in app.storage.list_devices()]
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_pair_window_closes_by_itself_once_the_phone_has_paired(tmp_path, monkeypatch):
    from textual.widgets import Button, DataTable, Static

    from ccc import api, pairing
    from ccc.tui.devices import PairModal

    monkeypatch.setattr(pairing, "tailscale_url", lambda: "https://xia.ts.net:8737")
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        settings = await _open_connection_tab(pilot, app)
        settings.query_one("#devices-pair", Button).press()
        await _wait_for(pilot, lambda: isinstance(app.screen, PairModal) and str(app.screen.query_one("#pair-qr", Static).render()).strip())
        assert isinstance(app.screen, PairModal)
        assert str(app.screen.query_one("#pair-qr", Static).render()).strip()
        # The phone scans: the code is traded for a device.
        app.storage.delete_setting_if(pairing.CODE_KEY, app.storage.get_setting(pairing.CODE_KEY))
        api.add_device(app.storage, "Redmi")
        await _wait_for(pilot, lambda: app.screen is settings)
        assert app.screen is settings
        assert settings.query_one("#devices-table", DataTable).row_count == 1
        assert "Paired: Redmi" in str(settings.query_one("#devices-info").render())


@pytest.mark.asyncio
async def test_devices_list_follows_a_sign_out_made_elsewhere(tmp_path):
    from textual.widgets import DataTable

    from ccc import api

    app = _app(tmp_path)
    phone, _token = api.add_device(app.storage, "Pixel")
    api.add_device(app.storage, "Tablet")
    async with app.run_test(size=(100, 30)) as pilot:
        settings = await _open_connection_tab(pilot, app)
        table = settings.query_one("#devices-table", DataTable)
        assert table.row_count == 2
        app.storage.revoke_device(phone.id, 'computer')  # `ccc api revoke`, or the app signing out
        await pilot.pause(3.5)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_devices_list_shows_the_history_of_signed_out_devices_read_only(tmp_path):
    from textual.widgets import Button, DataTable

    from ccc import api

    app = _app(tmp_path)
    phone, _token = api.add_device(app.storage, "Pixel")
    api.add_device(app.storage, "Tablet")
    async with app.run_test(size=(120, 30)) as pilot:
        settings = await _open_connection_tab(pilot, app)
        table = settings.query_one("#devices-table", DataTable)
        settings.query_one("#devices-history", Button).press()
        await pilot.pause()
        assert table.row_count == 0
        assert settings.query_one("#devices-revoke", Button).disabled
        app.storage.revoke_device(phone.id, "app")  # signed out in the app: the open history picks it up
        await pilot.pause(3.5)
        assert table.row_count == 1
        assert [str(c) for c in table.get_row_at(0)][0] == "Pixel"
        assert "in the app" in str(table.get_row_at(0)[-1])
        settings.query_one("#devices-history", Button).press()  # back to the active ones
        await pilot.pause()
        assert table.row_count == 1 and str(table.get_row_at(0)[0]) == "Tablet"


@pytest.mark.asyncio
async def test_each_service_has_a_window_for_the_messages_it_gets(tmp_path):
    from textual.widgets import Checkbox

    from ccc.notify import CAT_DIGEST, CAT_QUEUE, category_states
    from ccc.tui.notify_categories import NotifyCategoriesModal

    app = _app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        settings = await _open_settings(pilot, app)
        settings.query_one("#settings-discord-categories").press()
        await pilot.pause()
        assert isinstance(app.screen, NotifyCategoriesModal)
        app.screen.query_one(f"#notify-{CAT_QUEUE}", Checkbox).value = False
        app.screen.query_one(f"#notify-{CAT_DIGEST}", Checkbox).value = True
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsModal)
    storage = Storage(str(tmp_path / "history.db"))
    assert category_states(storage, "discord")[CAT_QUEUE] is False
    assert category_states(storage, "discord")[CAT_DIGEST] is True
    assert category_states(storage, "telegram")[CAT_QUEUE] is True  # the other service keeps its own


@pytest.mark.asyncio
async def test_the_messages_window_fits_a_small_terminal(tmp_path):
    from textual.widgets import Checkbox

    from ccc.tui.notify_categories import NotifyCategoriesModal

    app = _app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        settings = await _open_settings(pilot, app)
        settings.query_one("#settings-discord-categories").press()
        await pilot.pause()
        box = app.screen.query_one("NotifyCategoriesModal > Container")
        assert box.region.height <= 24 and box.region.width <= 80
        app.screen.query_one("#notify-health", Checkbox).focus()  # the last ones scroll into view
        await pilot.pause()
        assert app.screen.query_one("#notify-health", Checkbox).region.height == 1
        for button in ("#notify-cancel", "#notify-save"):  # the buttons are on screen, like in Settings
            region = app.screen.query_one(button).region
            assert 0 < region.bottom <= 24 and 0 < region.right <= 80
        app.screen.query_one("#notify-queue", Checkbox).value = False
        app.screen.query_one("#notify-save").press()
        await pilot.pause()
        assert isinstance(app.screen, SettingsModal)
