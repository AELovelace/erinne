"""Import users, libraries and grants from a MicroJames install.

    python scripts/migrate_microjames.py --source C:\\Scripts\\MicroJames --dry-run
    python scripts/migrate_microjames.py --source /opt/microjames

What it does:
  * users        -> erinne users, keeping their pbkdf2 password hashes (same
                    passwords work) and roles; each gets a private inbox
  * docs/<Name>  -> a library per top-level folder (except docs/users)
  * access_control.json grants of those folders -> library grants
  * files        -> counted only; importing them needs the ingestion pipeline (P1)

Grants that point at another user's private folder are skipped: erinne never
shares private inboxes. Publish such documents as a library instead.
Running it twice is safe: existing users, libraries and grants are left alone.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erinne import auth, db, permissions, users  # noqa: E402
from erinne.config import get_settings  # noqa: E402

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown"}


@dataclass
class Plan:
    users: list[tuple[str, str, str]] = field(default_factory=list)  # (username, pw_hash, role)
    libraries: list[str] = field(default_factory=list)
    grants: list[tuple[str, str]] = field(default_factory=list)  # (username, library)
    warnings: list[str] = field(default_factory=list)
    file_counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # name -> (supported, skipped)


def _count_files(folder: Path) -> tuple[int, int]:
    supported = skipped = 0
    for path in folder.rglob("*"):
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                supported += 1
            else:
                skipped += 1
    return supported, skipped


def build_plan(source: Path, admin_users: set[str]) -> Plan:
    plan = Plan()
    users_db = source / "data" / "users.db"
    if not users_db.exists():
        raise SystemExit(f"Not a MicroJames install (missing {users_db}).")

    src = sqlite3.connect(f"file:{users_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    columns = {row[1] for row in src.execute("PRAGMA table_info(users)")}
    has_role = "role" in columns
    for row in src.execute("SELECT * FROM users ORDER BY username"):
        name = row["username"]
        try:
            name = auth.validate_username(name)
        except ValueError as exc:
            plan.warnings.append(f"Skipping user '{row['username']}': {exc}")
            continue
        if not str(row["password"]).startswith("$pbkdf2-sha256$"):
            plan.warnings.append(f"Skipping user '{name}': unrecognised password hash format.")
            continue
        role = "admin" if (has_role and row["role"] == "admin") or name in admin_users else "user"
        plan.users.append((name, row["password"], role))
    src.close()

    docs = source / "docs"
    user_folders = {}
    if (docs / "users").is_dir():
        user_folders = {p.name.lower(): p for p in (docs / "users").iterdir() if p.is_dir()}
    library_folders = {}
    if docs.is_dir():
        for p in sorted(docs.iterdir()):
            if p.is_dir() and p.name.lower() != "users":
                library_folders[p.name.lower()] = p
                plan.libraries.append(p.name)

    for name, folder in sorted(user_folders.items()):
        plan.file_counts[f"inbox:{name}"] = _count_files(folder)
    for folder in library_folders.values():
        plan.file_counts[f"library:{folder.name}"] = _count_files(folder)

    known_users = {u for u, _, _ in plan.users}
    acl_path = source / "data" / "access_control.json"
    acl = json.loads(acl_path.read_text(encoding="utf-8")) if acl_path.exists() else {}
    for raw_user, sources in sorted(acl.items()):
        user = raw_user.strip().lower()
        if user not in known_users:
            plan.warnings.append(f"Grant for unknown user '{raw_user}' skipped.")
            continue
        for raw_source in sources:
            key = raw_source.strip().lower()
            if key in library_folders:
                plan.grants.append((user, library_folders[key].name))
            elif key in user_folders:
                plan.warnings.append(
                    f"Grant '{user}' -> '{raw_source}' skipped: that is a private inbox; "
                    "publish those documents as a library instead."
                )
            else:
                plan.warnings.append(f"Grant '{user}' -> '{raw_source}' skipped: no such folder.")
    return plan


def apply_plan(conn: sqlite3.Connection, plan: Plan) -> list[str]:
    done = []
    for name, pw_hash, role in plan.users:
        if users.get_user(conn, name):
            done.append(f"user {name}: already exists, left unchanged")
            continue
        users.create_user(conn, name, role=role, pw_hash=pw_hash)
        done.append(f"user {name}: created ({role})")
    for lib in plan.libraries:
        if permissions.get_library(conn, lib):
            done.append(f"library {lib}: already exists")
            continue
        permissions.create_library(conn, lib)
        done.append(f"library {lib}: created")
    for name, lib in plan.grants:
        library = permissions.get_library(conn, lib)
        user = users.get_user(conn, name)
        permissions.grant_user(conn, library["id"], user["id"])
        done.append(f"grant {name} -> {lib}: read")
    return done


def print_plan(plan: Plan) -> None:
    print("Users:")
    for name, _, role in plan.users:
        print(f"  {name} ({role})")
    print("Libraries:", ", ".join(plan.libraries) or "(none)")
    print("Grants:")
    for name, lib in plan.grants:
        print(f"  {name} -> {lib}")
    print("Files (supported pdf/md, other types skipped):")
    for name, (supported, skipped) in plan.file_counts.items():
        print(f"  {name}: {supported} to import later, {skipped} unsupported")
    for warning in plan.warnings:
        print(f"WARNING: {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="MicroJames install directory")
    parser.add_argument(
        "--admin-users", default="admin",
        help="Comma-separated usernames MicroJames treated as admins by name (its ADMIN_USERS; default 'admin')",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, change nothing")
    args = parser.parse_args()

    admin_users = {u.strip().lower() for u in args.admin_users.split(",") if u.strip()}
    plan = build_plan(args.source, admin_users)
    print_plan(plan)
    if args.dry_run:
        print("\nDry run: nothing was changed.")
        return

    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    print()
    for line in apply_plan(conn, plan):
        print(line)
    print(f"\nImported into {settings.db_path}.")


if __name__ == "__main__":
    main()
