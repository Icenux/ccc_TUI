"""`ccc profile list | add NAME [--folder DIR] | login NAME | remove NAME`:
the Claude accounts conversations can run under (ccc.profiles, #239)."""
from __future__ import annotations

import argparse
import subprocess

from ccc import profiles
from ccc.i18n import _


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("profile", help=_("Claude accounts: list, add, sign in, remove"))
    sub = parser.add_subparsers(dest="profile_command", required=True)
    sub.add_parser("list", help=_("List the accounts"))
    add = sub.add_parser("add", help=_("Add an account (its own folder; then sign in with `ccc profile login NAME`)"))
    add.add_argument("name")
    add.add_argument("--folder", help=_("the account's folder (default: ~/.claude-NAME)"))
    login = sub.add_parser("login", help=_("Sign in to an account (runs `claude auth login` for it)"))
    login.add_argument("name")
    remove = sub.add_parser("remove", help=_("Forget an account (its folder and login stay on disk)"))
    remove.add_argument("name")


def _state(name: str) -> str:
    saved = profiles.has_credentials_file(name)
    if saved is None:
        return "?"
    return _("signed in") if saved else _("not signed in")


def run(args: argparse.Namespace) -> int:
    command = args.profile_command
    if command == "list":
        print(f"{'(default)':<16} {profiles.default_config_dir()}")
        for profile in profiles.list_profiles():
            print(f"{profile.name:<16} {profile.config_dir}  [{_state(profile.name)}]")
        return 0
    if command == "add":
        try:
            profile = profiles.add(args.name, args.folder)
        except profiles.ProfileError as error:
            print(error)
            return 1
        print(_("Account '{name}' added ({folder}). Sign in: ccc profile login {name}").format(name=profile.name, folder=profile.config_dir))
        return 0
    if command == "remove":
        if not profiles.remove(args.name):
            print(_("No account '{name}'.").format(name=args.name))
            return 1
        print(_("Account '{name}' forgotten; its folder was left alone.").format(name=args.name))
        return 0
    if command == "login":
        if profiles.get(args.name) is None:
            print(_("No account '{name}'.").format(name=args.name))
            return 1
        try:
            return subprocess.run(["claude", "auth", "login"], env=profiles.env_for(args.name)).returncode
        except FileNotFoundError:
            print(_("The claude command was not found."))
            return 1
    return 1
