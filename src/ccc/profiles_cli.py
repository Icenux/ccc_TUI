"""`ccc profile list | add NAME [--folder DIR] | add-api NAME --run CMD … | login NAME | remove NAME`:
the Claude accounts and other agents conversations can run under
(ccc.profiles, #239, #226)."""
from __future__ import annotations

import argparse
import getpass
import subprocess
import sys

from ccc import profiles
from ccc.i18n import _


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("profile", help=_("Claude accounts: list, add, sign in, remove"))
    sub = parser.add_subparsers(dest="profile_command", required=True)
    sub.add_parser("list", help=_("List the accounts"))
    add = sub.add_parser("add", help=_("Add an account (its own folder; then sign in with `ccc profile login NAME`)"))
    add.add_argument("name")
    add.add_argument("--folder", help=_("the account's folder (default: ~/.claude-NAME)"))
    api = sub.add_parser("add-api", help=_("Add another agent / API (no Claude Code or Anthropic account needed)"))
    api.add_argument("name")
    api.add_argument("--run", required=True, help=_("command line of one run; placeholders {prompt} {model} {url} {session_id} {project}"))
    api.add_argument("--interactive", default="", help=_("command line of the interactive session (default: --run)"))
    api.add_argument("--resume", default="", help=_("command line when a conversation continues (default: --run)"))
    api.add_argument("--url", default="", help=_("API address ({url})"))
    api.add_argument("--model", default="", help=_("model name ({model})"))
    api.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                     help=_("environment variable of the agent; VALUE may use {url} {key} {model} (repeatable)"))
    api.add_argument("--key-stdin", action="store_true", help=_("read the API key from stdin instead of asking for it"))
    login = sub.add_parser("login", help=_("Sign in to an account (runs `claude auth login` for it)"))
    login.add_argument("name")
    remove = sub.add_parser("remove", help=_("Forget an account (its folder and login stay on disk)"))
    remove.add_argument("name")


def _state(name: str) -> str:
    if (profile := profiles.get(name)) is not None and profile.is_command:
        return "API"
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
    if command == "add-api":
        env = {}
        for item in args.env:
            variable, separator, value = item.partition("=")
            if not separator or not variable:
                print(_("--env must be NAME=VALUE: {item}").format(item=item))
                return 1
            env[variable] = value
        uses_key = any("{key}" in value for value in env.values())
        key = ""
        if uses_key:
            key = sys.stdin.readline().strip() if args.key_stdin or not sys.stdin.isatty() else getpass.getpass(_("API key: "))
        try:
            profile = profiles.add_command(
                args.name, run=args.run, interactive=args.interactive, resume=args.resume,
                url=args.url, model=args.model, key=key, env=env,
            )
        except profiles.ProfileError as error:
            print(error)
            return 1
        print(_("Agent '{name}' added. Choose it when starting a conversation.").format(name=profile.name))
        print(_(profiles.API_LIMITS_NOTICE))
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
