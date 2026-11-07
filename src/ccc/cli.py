from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ccc.i18n import _
from ccc.orchestrator import enqueue_prompt
from ccc.storage import Storage


# Data directory used before the project was renamed from cc-continue.
OLD_DATA_DIR_NAME = "cc-continue"


def default_db_path() -> Path:
    data_dir = Path.home() / ".local" / "share" / "ccc"
    old_dir = data_dir.parent / OLD_DATA_DIR_NAME
    if old_dir.is_dir() and not data_dir.exists():
        old_dir.rename(data_dir)  # carry history over from the old name
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)  # the database, the app build and the backups in it are the owner's alone (#160)
    return data_dir / "history.db"


def _tell_tmux_client(client: str | None, message: str, socket: str | None = None) -> None:
    """Show a message in the tmux status line of that client (Alt+N
    pressed in the Claude Code pane has no ccc screen to notify on)."""
    if not client:
        return
    import subprocess

    from ccc import instance

    server = ["-S", socket] if socket else ["-L", instance.TMUX_SOCKET]
    subprocess.run(["tmux", *server, "display-message", "-c", client, "-d", "4000", message], capture_output=True)


def add_command(db_path: str, project_path: str, prompt: str) -> None:
    storage = Storage(db_path)
    enqueue_prompt(storage, project_path, prompt)


def config_command(db_path: str, setting: str, value: str | None) -> None:
    from ccc.orchestrator import DEFAULT_CONTINUE_TEXT

    from ccc.notify import DISCORD_WEBHOOK_KEY, TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY, Notifier, is_discord_webhook

    storage = Storage(db_path)
    if setting == "language":
        from ccc import i18n

        if value is None:
            current = storage.get_setting(i18n.LANGUAGE_KEY) or "auto"
            print(_("{setting} (now: {language})").format(setting=current, language=i18n.resolve(current)))
            return
        choice = value.strip().lower()
        if choice not in ("auto", *(code for code, _name in i18n.LANGUAGES)):
            print(_("ccc: language takes auto, en or cs"), file=sys.stderr)
            sys.exit(1)
        storage.set_setting(i18n.LANGUAGE_KEY, "" if choice == "auto" else choice)
        i18n.set_language(i18n.resolve(choice))
        print(_("language set to {language}. Restart ccc (Ctrl+Q) to see it in the TUI.").format(language=choice))
        return
    if setting == "telegram-test":
        notifier = Notifier(storage)
        if not notifier.telegram_configured():
            print(_("ccc: set `ccc config telegram-token …` and `ccc config telegram-chat …` first"), file=sys.stderr)
            sys.exit(1)
        if notifier.send_telegram_now(_("✅ ccc: Telegram notifications work.")):
            print(_("Test message sent."))
        else:
            print(_("ccc: sending failed — check the token and chat id"), file=sys.stderr)
            sys.exit(1)
        return
    if setting == "analytics-folder":
        from ccc.analytics_archive import FOLDER_KEY, configured_folder

        if value is None:
            print(configured_folder(storage) or _("(off)"))
        elif value.strip().lower() == "off":
            storage.set_setting(FOLDER_KEY, "")
            print(_("analytics-folder removed: monthly archive off."))
        else:
            folder = str(Path(value.strip()).expanduser().resolve())
            storage.set_setting(FOLDER_KEY, folder)
            print(_("analytics-folder set to {folder} — finished months are archived there.").format(folder=folder))
        return
    if setting == "discord-test":
        notifier = Notifier(storage)
        if not notifier.discord_configured():
            print(_("ccc: set `ccc config discord-webhook …` first"), file=sys.stderr)
            sys.exit(1)
        if notifier.send_discord_now(_("✅ ccc: Discord notifications work.")):
            print(_("Test message sent."))
        else:
            print(_("ccc: sending failed — check the webhook URL"), file=sys.stderr)
            sys.exit(1)
        return
    if setting == "discord-webhook":
        if value is None:
            current = storage.get_setting(DISCORD_WEBHOOK_KEY)
            # never print the secret part (the token after the webhook id)
            print(current.rsplit("/", 1)[0] + "/…" if current else _("(not set)"))
        elif value.strip().lower() in ("off", "none", ""):
            storage.set_setting(DISCORD_WEBHOOK_KEY, "")
            print(_("discord-webhook removed."))
        elif not is_discord_webhook(value.strip()):
            print(_("ccc: that isn't a Discord webhook URL (https://discord.com/api/webhooks/…)"), file=sys.stderr)
            sys.exit(1)
        else:
            storage.set_setting(DISCORD_WEBHOOK_KEY, value.strip())
            print(_("discord-webhook saved."))
        return
    if setting in ("telegram-token", "telegram-chat"):
        key = TELEGRAM_TOKEN_KEY if setting == "telegram-token" else TELEGRAM_CHAT_KEY
        if value is None:
            current = storage.get_setting(key)
            if current and setting == "telegram-token":
                current = current[:4] + "…" + current[-2:]  # never print the secret
            print(current or _("(not set)"))
        else:
            storage.set_setting(key, value.strip())
            print(_("{setting} saved.").format(setting=setting))
        return
    if setting == "usage-reserve":
        from ccc.orchestrator import RESERVE_KEY, usage_reserve

        if value is None:
            current = usage_reserve(storage)
            print(f"{current}%" if current else _("off"))
            return
        if value.strip().lower() in ("off", "0", "none"):
            storage.set_setting(RESERVE_KEY, "")
            print(_("usage-reserve off."))
            return
        try:
            percent = int(value.strip().rstrip("%"))
        except ValueError:
            percent = -1
        if not 0 < percent < 100:
            print(_("ccc: usage-reserve takes a number from 1 to 99, or off"), file=sys.stderr)
            sys.exit(1)
        storage.set_setting(RESERVE_KEY, str(percent))
        print(_("usage-reserve set to {percent}%: queued tasks wait while a usage window is at or above it.").format(percent=percent))
        return
    if setting == "continue-text":
        if value is None:
            print(storage.get_setting("continue_text", DEFAULT_CONTINUE_TEXT))
        else:
            storage.set_setting("continue_text", value)
            print(_("continue-text set to: {text}").format(text=value))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccc")
    subparsers = parser.add_subparsers(dest="command")

    add_parser = subparsers.add_parser(
        "add",
        help=_("Queue a prompt for a project without opening the TUI "
               "(needs ccc-daemon running to actually execute it)"),
    )
    add_parser.add_argument("project_path")
    add_parser.add_argument("prompt")
    subparsers.add_parser(
        "tui", help=_("Run the TUI itself (what the ccc-tui service runs; plain `ccc` attaches to it)")
    )
    preset_parser = subparsers.add_parser(
        "preset", help=_("Type preset prompt N into an interactive conversation's Claude Code (Alt+N in its pane)")
    )
    preset_parser.add_argument("number", type=int)
    preset_parser.add_argument("--queue", type=int, required=True)
    preset_parser.add_argument("--db", default=None, help=_("ccc database (default: the usual one)"))
    preset_parser.add_argument("--client", default=None, help=_("tmux client to show an error on"))
    preset_parser.add_argument("--socket", default=None, help=_("that client's tmux server socket"))

    subparsers.add_parser("backup", help=_("Back up the ccc database now (the daemon also does it daily)"))
    subparsers.add_parser(
        "archive-analytics", help=_("Archive finished months into the analytics folder now (the daemon does it hourly)")
    )

    from ccc import api_cli, goals_cli, keys_cli, profiles_cli, revert_cli, reviews_cli, routines_cli, workflows_cli

    keys_cli.add_parser(subparsers)
    routines_cli.add_parser(subparsers)
    reviews_cli.add_parser(subparsers)
    goals_cli.add_parser(subparsers)
    revert_cli.add_parser(subparsers)
    profiles_cli.add_parser(subparsers)
    workflows_cli.add_parser(subparsers)
    api_cli.add_parser(subparsers)

    config_parser = subparsers.add_parser("config", help=_("Show or change ccc settings"))
    config_sub = config_parser.add_subparsers(dest="setting", required=True)
    continue_parser = config_sub.add_parser(
        "continue-text",
        help=_("Message sent after a usage limit interrupted a prompt mid-way (no value: show it)"),
    )
    continue_parser.add_argument("value", nargs="?")
    token_parser = config_sub.add_parser("telegram-token", help=_("Telegram bot token for notifications (no value: show it masked)"))
    token_parser.add_argument("value", nargs="?")
    chat_parser = config_sub.add_parser("telegram-chat", help=_("Telegram chat id to notify (no value: show it)"))
    chat_parser.add_argument("value", nargs="?")
    reserve_parser = config_sub.add_parser(
        "usage-reserve", help=_("Don't start queued tasks above this %% of a usage window (1-99, or off)")
    )
    reserve_parser.add_argument("value", nargs="?")
    test_parser = config_sub.add_parser("telegram-test", help=_("Send a test notification"))
    test_parser.set_defaults(value=None)
    webhook_parser = config_sub.add_parser(
        "discord-webhook", help=_("Discord channel webhook URL for notifications (no value: show it masked; off: remove)")
    )
    webhook_parser.add_argument("value", nargs="?")
    archive_parser = config_sub.add_parser(
        "analytics-folder", help=_("Folder for the monthly analytics archive (no value: show it; off: stop archiving)")
    )
    archive_parser.add_argument("value", nargs="?")
    discord_test_parser = config_sub.add_parser("discord-test", help=_("Send a test notification to Discord"))
    discord_test_parser.set_defaults(value=None)
    language_parser = config_sub.add_parser(
        "language", help=_("Language of ccc: auto (system), en or cs (no value: show it)")
    )
    language_parser.add_argument("value", nargs="?")

    args = parser.parse_args(argv)

    if args.command == "archive-analytics":
        from ccc.analytics_archive import archive, configured_folder

        storage = Storage(str(default_db_path()))
        folder = configured_folder(storage)
        if folder is None:
            print(_("ccc: set `ccc config analytics-folder PATH` first"), file=sys.stderr)
            sys.exit(1)
        added = archive(storage, folder)
        months = ", ".join(f"{y:04d}-{m:02d}" for y, m in added) or _("nothing new")
        print(_("Archived: {months} ({folder})").format(months=months, folder=folder))
        return

    if args.command == "preset":
        from ccc.cli_input import apply_preset

        error = apply_preset(Storage(args.db or str(default_db_path())), args.queue, args.number)
        if error:
            _tell_tmux_client(args.client, error, args.socket)
            print(f"ccc preset: {error}", file=sys.stderr)
            sys.exit(1)
        return

    if args.command == "backup":
        from ccc import backup

        db_path = default_db_path()
        path = backup.backup_now(db_path, backup.default_backup_dir(db_path))
        backup.prune(path.parent)
        print(_("Backed up to {path}").format(path=path))
        return

    if args.command == "config":
        config_command(str(default_db_path()), args.setting, args.value)
        return

    if args.command == "keys":
        sys.exit(keys_cli.run(Storage(str(default_db_path())), args))

    if args.command == "routine":
        sys.exit(routines_cli.run(Storage(str(default_db_path())), args))

    if args.command == "review":
        sys.exit(reviews_cli.run(Storage(str(default_db_path())), args))

    if args.command == "goal":
        sys.exit(goals_cli.run(Storage(str(default_db_path())), args))

    if args.command == "workflow":
        sys.exit(workflows_cli.run(Storage(str(default_db_path())), args))

    if args.command == "runs":
        sys.exit(revert_cli.run_runs(Storage(str(default_db_path())), args))

    if args.command == "profile":
        sys.exit(profiles_cli.run(args))

    if args.command == "revert":
        sys.exit(revert_cli.run_revert(Storage(str(default_db_path())), args))

    if args.command == "api":
        db_path = str(default_db_path())
        sys.exit(api_cli.run(Storage(db_path), db_path, args))

    if args.command == "add":
        try:
            add_command(str(default_db_path()), str(Path(args.project_path).resolve()), args.prompt)
        except ValueError as exc:
            print(f"ccc add: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    from ccc import instance

    if args.command == "tui" or instance.inside_ccc_tmux():
        # Inside ccc's own tmux server `ccc` is the instance itself.
        from ccc.tui.app import run_app

        run_app(str(default_db_path()))
        return
    instance.attach()


if __name__ == "__main__":
    sys.exit(main())
