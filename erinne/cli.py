"""Command-line administration: `erinne <command>` or `python -m erinne <command>`.

Passwords are prompted for (or read from stdin with --password-stdin), never
taken as arguments, so they stay out of shell history and process lists.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from erinne import db, permissions, users
from erinne.config import get_settings


def _connect():
    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    return conn


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def cmd_init_db(args):
    conn = _connect()
    print(f"Database ready at {get_settings().db_path} (schema v{db.schema_version(conn)}).")


def cmd_create_user(args):
    conn = _connect()
    users.create_user(conn, args.username, _read_password(args), role=args.role)
    print(f"Created {args.role} '{args.username.lower()}' with a private inbox.")


def cmd_reset_password(args):
    conn = _connect()
    if not users.set_password(conn, args.username, _read_password(args)):
        raise SystemExit(f"No user '{args.username}'.")
    print(f"Password updated for '{args.username.lower()}'.")


def cmd_set_role(args):
    conn = _connect()
    if not users.set_role(conn, args.username, args.role):
        raise SystemExit(f"No user '{args.username}'.")
    print(f"'{args.username.lower()}' is now {args.role}.")


def cmd_list_users(args):
    conn = _connect()
    for u in users.list_users(conn):
        state = "" if u["active"] else "  (disabled)"
        print(f"{u['username']:<30} {u['role']}{state}")


def cmd_create_library(args):
    conn = _connect()
    lib_id = permissions.create_library(conn, args.name)
    print(f"Created library '{args.name}' (id {lib_id}).")


def cmd_grant(args):
    conn = _connect()
    lib = permissions.get_library(conn, args.library)
    user = users.get_user(conn, args.username)
    if lib is None or user is None:
        raise SystemExit("Unknown library or user.")
    permissions.grant_user(conn, lib["id"], user["id"], can_write=args.write)
    print(f"Granted '{user['username']}' {'read/write' if args.write else 'read'} access to '{lib['name']}'.")


def cmd_serve(args):
    import uvicorn

    from erinne.web.app import create_app

    settings = get_settings()
    # proxy_headers + FORWARDED_ALLOW_IPS let login throttling see real client
    # IPs behind nginx.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="erinne", description="Erinne administration")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create or migrate the database").set_defaults(func=cmd_init_db)
    sub.add_parser("serve", help="Run the web app").set_defaults(func=cmd_serve)
    sub.add_parser("list-users", help="List users").set_defaults(func=cmd_list_users)

    p = sub.add_parser("create-user", help="Create a user (prompts for the password)")
    p.add_argument("username")
    p.add_argument("--role", choices=users.ROLES, default="user")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("reset-password", help="Set a new password (prompts for it)")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("set-role", help="Make a user an admin or a regular user")
    p.add_argument("username")
    p.add_argument("role", choices=users.ROLES)
    p.set_defaults(func=cmd_set_role)

    p = sub.add_parser("create-library", help="Create a shared library")
    p.add_argument("name")
    p.set_defaults(func=cmd_create_library)

    p = sub.add_parser("grant", help="Give a user access to a library")
    p.add_argument("library")
    p.add_argument("username")
    p.add_argument("--write", action="store_true", help="Also allow uploading to the library")
    p.set_defaults(func=cmd_grant)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc
