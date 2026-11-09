"""`ccc api …` — the HTTP API for the Android app (ccc.api): run it, make
the token the app needs, list the devices signed in and sign one out."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ccc import api, api_audit, api_scope, pairing
from ccc.i18n import _
from ccc.models import REVOKED_BY_COMPUTER


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("api", help=_("HTTP API for the Android app (serve, token, pair, devices)"))
    sub = parser.add_subparsers(dest="api_command", required=True)
    serve = sub.add_parser("serve", help=_("Run the API (what the ccc-api service runs)"))
    serve.add_argument("--host", default=api.DEFAULT_HOST,
                       help=_("address to listen on (default {host}: reach it through tailscale serve)").format(
                           host=api.DEFAULT_HOST))
    serve.add_argument("--port", type=int, default=api.DEFAULT_PORT)
    token = sub.add_parser("token", help=_("Make the token the app signs in with (shown once)"))
    token.add_argument("--new", action="store_true", help=_("make a new one; every device is signed out"))
    pair = sub.add_parser("pair", help=_("Show a QR code the app scans to sign in (good for 5 minutes)"))
    pair.add_argument("--server", help=_("the API's address, if ccc can't find it (https://…ts.net)"))
    pair.add_argument("--image", nargs="?", const="", metavar="FILE",
                      help=_("save the QR as a picture (default ~/Pictures/ccc-pair-qr.png) instead of drawing it in the terminal"))
    roots = sub.add_parser("roots", help=_("Folders the app may start conversations in (none set: only those ccc already knows)"))
    roots.add_argument("--add", metavar="FOLDER", help=_("allow this folder and everything inside it"))
    roots.add_argument("--remove", metavar="FOLDER", help=_("stop allowing this folder"))
    skip = sub.add_parser("skip-permissions", help=_("Whether the app may start conversations that skip every permission check"))
    skip.add_argument("state", choices=("on", "off"), nargs="?", help=_("leave out to show the state"))
    log = sub.add_parser("log", help=_("What the devices did: changes, refusals and pairings (newest last)"))
    log.add_argument("-n", type=int, default=50, help=_("how many entries (default 50)"))
    sub.add_parser("doctor", help=_("Check the API's security: where it listens, file permissions, devices, limits, alarms"))
    devices = sub.add_parser("devices", help=_("List the devices signed in"))
    devices.add_argument("--all", action="store_true", help=_("also the devices signed out (history, read only)"))
    revoke = sub.add_parser("revoke", help=_("Sign a device out"))
    revoke.add_argument("id", type=int, help=_("its number from `ccc api devices`"))


def run(storage, db_path: str, args: argparse.Namespace) -> int:
    if args.api_command == "serve":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        api.serve(db_path, args.host, args.port)
        return 0
    if args.api_command == "pair":
        return _pair(storage, args.server, getattr(args, "image", None))
    if args.api_command == "roots":
        return _roots(storage, args.add, args.remove)
    if args.api_command == "skip-permissions":
        if args.state:
            api_scope.set_skip_allowed(storage, args.state == "on")
        print(_("The app may start conversations that skip permission checks: {state}.").format(
            state=_("yes") if api_scope.skip_allowed(storage) else _("no")))
        return 0
    if args.api_command == "log":
        entries = storage.list_audit(args.n)
        for e in reversed(entries):
            e = {k: api_audit.clean(str(v), 400) for k, v in e.items()}  # rows written before the cleaning, too
            who = f"{e['device_name']} ({e['device_id']})" if e["device_id"] is not None else "—"
            print(f"{e['at'][:19].replace('T', ' ')}  {e['status']}  {e['method']:<6} {e['path']}  {e['detail']}  ·  {who}  {e['ip']}".rstrip())
        if not entries:
            print(_("Nothing logged yet."))
        return 0
    if args.api_command == "doctor":
        from ccc import api_doctor

        checks = api_doctor.run_checks(storage, db_path)
        marks = {api_doctor.OK: "✓", api_doctor.WARN: "!", api_doctor.FAIL: "✗"}
        for check in checks:
            print(f"{marks[check.level]} {check.text}")
        return 1 if any(c.level == api_doctor.FAIL for c in checks) else 0
    if args.api_command == "devices":
        devices = storage.list_devices(include_revoked=args.all)
        for device in devices:
            seen = device.last_seen_at.strftime("%Y-%m-%d %H:%M") if device.last_seen_at else _("never")
            line = f"{device.id:>3}  {device.name}  ·  {_('last seen')} {seen}  {device.last_ip}".rstrip()
            if device.revoked_at:
                line += f"  ·  {_('signed out')} {device.revoked_at.strftime('%Y-%m-%d %H:%M')} ({api.revoked_label(device)})"
            print(line)
        if not devices:
            print(_("No device is signed in."))
        return 0
    if args.api_command == "revoke":
        if not storage.revoke_device(args.id, REVOKED_BY_COMPUTER):
            print(_("No such device."))
            return 1
        print(_("Signed out."))
        return 0
    if api.has_token(storage) and not args.new:
        print(_("A token is set. ccc keeps only its fingerprint, so it can't show it again — "
                "`ccc api token --new` makes a new one (every device is signed out)."))
        return 0
    print(api.new_token(storage))
    print(_("Enter it in the app now — it isn't shown again. Every token made before no longer works."))
    return 0


def _pair(storage, server: str | None, image: str | None = None) -> int:
    server = (server or "").strip().rstrip("/") or pairing.server_url(storage)
    if not server:
        print(_("ccc can't tell where the app reaches the API. Put it on the tailnet (tailscale serve --bg 8737) "
                "or give the address: ccc api pair --server https://<computer>.<tailnet>.ts.net"))
        return 1
    code, expires = pairing.new_code(storage)
    if image is not None:
        path = Path(image).expanduser() if image else Path.home() / "Pictures" / "ccc-pair-qr.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        pairing.save_png(pairing.payload(server, code), path)
        print(_("QR saved as a picture: {path}").format(path=path))
    else:
        # Black on white whatever the terminal's colours: a scanner needs dark modules on a light ground.
        for line in pairing.qr_lines(pairing.payload(server, code)):
            print(f"\033[30;47m{line}\033[0m")
    print(_("Scan it in the app (Connect → Scan QR). Good until {time}, once.").format(time=expires.strftime("%H:%M")))
    print(_("Address: {server}").format(server=server))
    return 0


def _roots(storage, add: str | None, remove: str | None) -> int:
    try:
        if add:
            print(_("Allowed: {path}").format(path=api_scope.add_root(storage, add)))
        if remove and not api_scope.remove_root(storage, remove):
            print(_("That folder wasn't allowed."))
            return 1
    except ValueError as exc:
        print(exc)
        return 1
    chosen = api_scope.roots(storage)
    print(_("The app may start conversations in:") if chosen
          else _("No folder set: the app may use only the folders ccc already knows:"))
    for folder in chosen or api_scope.allowed_folders(storage):
        print(f"  {folder}")
    return 0
