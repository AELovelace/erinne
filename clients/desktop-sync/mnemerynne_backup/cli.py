"""mnemerynne-backup: the backup app from the command line (and how it runs as a service on Linux)."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from mnemerynne_backup import __version__, autostart, signin
from mnemerynne_backup.backup import Backup, BackupError, connect, update_offer
from mnemerynne_backup.config import Config, config_dir, state_dir
from mnemerynne_backup.engine import Report
from mnemerynne_backup.transport import TransportError


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def describe(report: Report | dict) -> str:
    r = report if isinstance(report, dict) else report.json()
    if r.get("signed_out"):
        return f"{r['label']}: signed out; sign in again (mnemerynne-backup login)."
    if r.get("offline"):
        return f"{r['label']}: waiting (offline, or on a metered connection)."
    bits = [f"{r['uploaded']} sent ({_size(r['bytes'])})" if r["uploaded"] else "",
            f"{r['moved']} moved" if r["moved"] else "", f"{r['deleted']} to the recycle bin" if r["deleted"] else "",
            f"{len(r['waiting'])} still changing" if r["waiting"] else "",
            f"{len(r['skipped'])} skipped" if r["skipped"] else "", f"{len(r['errors'])} failed" if r["errors"] else ""]
    return f"{r['label']}: " + (", ".join(b for b in bits if b) or "up to date") + "."


def _print_report(r: Report, verbose: bool) -> None:
    print(describe(r))
    if verbose or r.errors:
        for path, why in r.errors:
            print(f"  failed  {path}: {why}")
    if verbose:
        for path, why in r.skipped:
            print(f"  skipped {path}: {why}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mnemerynne-backup", description="Back folders up to Mnemerynne.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")    # no command: the terminal app
    s = sub.add_parser("login", help="sign this computer in to Mnemerynne")
    s.add_argument("server", help="Mnemerynne's address, like https://beepsquad.wtf")
    s.add_argument("--token", action="store_true", help="paste a sync token instead of signing in in a browser")
    s.add_argument("--computer", help="this computer's name on the NAS (default: its own name)")
    sub.add_parser("logout", help="sign this computer out")
    s = sub.add_parser("add", help="back a folder up")
    s.add_argument("path")
    s.add_argument("--label", help="its name under Backups\\<computer> (default: the folder's name)")
    s.add_argument("--exclude", action="append", default=[], help="a pattern to leave out (repeatable)")
    s = sub.add_parser("remove", help="stop backing a folder up (the copy on the NAS stays)")
    s.add_argument("folder", help="label or path")
    sub.add_parser("list", help="the backed-up folders")
    s = sub.add_parser("sync", help="one backup pass now")
    s.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                   help="list every skipped file and why")    # also after the command: `sync -v`
    s.add_argument("--check", action="store_true", help="also compare with the NAS's list")
    s.add_argument("folders", nargs="*", help="labels (default: all)")
    sub.add_parser("run", help="keep backing up until stopped (a service)")
    sub.add_parser("status", help="how the last pass went")
    for name, help_ in (("pause", "stop backing a folder up for now"), ("resume", "start again")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("folder")
    s = sub.add_parser("exclude", help="set what to leave out of a folder")
    s.add_argument("folder")
    s.add_argument("patterns", nargs="*", help="glob patterns; none clears them")
    s = sub.add_parser("searchable", help="let Erynne search a backed-up folder, or stop")
    s.add_argument("folder")
    s.add_argument("on", choices=("on", "off"))
    s = sub.add_parser("restore", help="copy a backup (or part of it) back to this computer")
    s.add_argument("folder")
    s.add_argument("--path", help="one file or folder inside the backup")
    s.add_argument("--version", dest="version_id", type=int, help="an earlier version of --path (see versions)")
    s.add_argument("--to", required=True, help="where to put it; nothing there is overwritten")
    s = sub.add_parser("versions", help="earlier versions of a backed-up file")
    s.add_argument("folder")
    s.add_argument("path")
    s = sub.add_parser("autostart", help="start backing up when you sign in to this computer")
    s.add_argument("on", choices=("on", "off"))
    s.add_argument("--service", action="store_true", help="Linux without a desktop: a systemd user service")
    args = p.parse_args(argv)
    if args.cmd is None:
        from mnemerynne_backup import tui    # textual is only needed for the full app
        return tui.main()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    config = Config.load()
    backup = Backup(config)
    try:
        return _run(args, config, backup) or 0
    except (BackupError, TransportError, signin.SignInError) as exc:
        print(exc, file=sys.stderr)
        return 1


def _run(args, config: Config, backup: Backup) -> int | None:
    if args.cmd == "login":
        server = args.server.rstrip("/")
        info = connect(server)
        config.server, config.api_url = server, info.get("api_url") or server
        if args.computer:
            config.computer = args.computer
        if args.token:
            signin.save_token(server, getpass.getpass("Sync token (from Mnemerynne → Preferences → Backups): "))
        else:
            print("Finish signing in in your browser…")
            signin.browser_sign_in(server, config.computer)
        backup.save()
        backup.transport.roots()        # proves the sign-in works
        print(f"Signed in to {server} as {config.computer}; backups go to {config.base_url}.")
    elif args.cmd == "logout":
        if config.server:
            signin.sign_out(config.server)
        print("Signed out.")
    elif args.cmd == "add":
        f = backup.add(args.path, args.label, args.exclude)
        print(f"Backing up {f.path} to Backups\\{config.computer}\\{f.label}.")
    elif args.cmd == "remove":
        f = backup.remove(args.folder)
        print(f"Stopped backing up {f.path}. Its copy on the NAS stays until you delete it in Files.")
    elif args.cmd == "list":
        if not config.folders:
            print("No folders yet: mnemerynne-backup add PATH")
        for f in config.folders:
            extra = " (paused)" if f.paused else ""
            extra += f"; leaving out {', '.join(f.excludes)}" if f.excludes else ""
            print(f"{f.label}: {f.path}{extra}")
    elif args.cmd == "sync":
        reports = backup.run(check=True if args.check else None, only=args.folders or None)
        for r in reports:
            _print_report(r, args.verbose)
        return 0 if all(r.ok for r in reports) else 2
    elif args.cmd == "run":
        from mnemerynne_backup.watch import Runner

        def show(reports):
            for r in reports:
                if r.uploaded or r.moved or r.deleted or not r.ok:
                    print(describe(r), flush=True)

        runner = Runner(backup, show)
        try:
            runner.loop()
        except KeyboardInterrupt:
            pass
    elif args.cmd == "status":
        print(f"Signed in to {config.server or '(nobody)'} as {config.computer}. Settings: {config_dir()}, "
              f"state: {state_dir()}.")
        for label, r in backup.last_reports().items():
            print(describe(r) if r else f"{label}: not backed up yet.")
        if config.server:
            try:
                offer = update_offer(config.server)
            except TransportError:
                offer = None
            if offer:
                print(f"Version {offer[0]} is available (this is {__version__}): {offer[1]}")
    elif args.cmd in ("pause", "resume"):
        backup.set_paused(args.folder, args.cmd == "pause")
    elif args.cmd == "exclude":
        backup.set_excludes(args.folder, args.patterns)
    elif args.cmd == "searchable":
        root = backup.set_searchable(args.folder, args.on == "on")
        print(f"{root['label']}: " + ("Erynne can search it (indexing runs in the background)."
                                      if root["searchable"] else "not searchable."))
    elif args.cmd == "versions":
        folder = backup._folder(args.folder)
        for v in backup.transport.versions(backup._root_id(folder), args.path):
            print(f"{v['id']}: {v['created_at']}  {_size(v['size'])}")
    elif args.cmd == "restore":
        n = backup.restore(args.folder, args.to, args.path, version_id=args.version_id)
        print(f"Restored {n} file{'s' if n != 1 else ''} to {args.to}.")
    elif args.cmd == "autostart":
        print(autostart.set_enabled(args.on == "on", service=args.service))
    return None


if __name__ == "__main__":
    sys.exit(main())
