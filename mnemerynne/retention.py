"""Housekeeping for logs and OAuth leftovers; the worker runs purge() hourly.

The call logs hold what people asked and what came back (tool arguments, API
response excerpts), so they are kept only as long as the *_days settings say.
"""

from __future__ import annotations

import sqlite3

from mnemerynne import db
from mnemerynne.config import Settings

# (table, column, setting): rows whose column is older than the setting's days go. Messages and
# expired announcements are kept this way too (an announcement with no expiry never matches).
LOGS = (("mcp_calls", "at", "mcp_calls_days"),
        ("api_calls", "at", "api_calls_days"),
        ("agent_calls", "at", "agent_calls_days"),
        ("graph_runs", "started_at", "graph_runs_days"),
        ("user_messages", "created_at", "message_days"),
        ("announcements", "expires_at", "announcement_days"),
        ("automation_runs", "started_at", "automation_run_days"))
# The same, for tables in a feature's own file (db.FEATURE_DBS): (feature, table, column, setting).
FEATURE_LOGS = (("games", "coin_transactions", "created_at", "coin_ledger_days"),)


def _older(days: int) -> str:
    return f"-{int(days)} days"


def purge(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    """Delete what is past its keep date. Returns rows deleted per table (only non-zero)."""
    out: dict[str, int] = {}
    for table, column, setting in LOGS:
        days = getattr(settings, setting)
        if days > 0:
            n = conn.execute(f"DELETE FROM {table} WHERE {column} < datetime('now', ?)", (_older(days),)).rowcount
            if n:
                out[table] = n
    for feature, table, column, setting in FEATURE_LOGS:     # games.db's coin ledger (or core's, before the move)
        days = getattr(settings, setting)
        if days > 0:
            with db.table_connection(conn, feature, table) as there:
                if there is not None:
                    n = there.execute(f"DELETE FROM main.{table} WHERE {column} < datetime('now', ?)",
                                      (_older(days),)).rowcount
                    if n:
                        out[table] = n

    core_path = conn.execute("PRAGMA database_list").fetchone()[2]   # feature rows whose user, file or folder
    if core_path:                                                     # went since (core can't cascade into them)
        for where, n in db.sweep_orphans(core_path).items():
            out[f"orphans:{where}"] = n
        from mnemerynne import search_index      # chunks whose copy of their file's place didn't catch up
        fixed = search_index.repair(core_path)
        if fixed:
            out["search_repaired"] = fixed

    from mnemerynne import scratchpad   # notes nobody touched in that long: into the archive, not away
    n = scratchpad.expire(conn, settings.scratchpad_days)
    if n:
        out["scratchpad_notes"] = n

    from mnemerynne import rooms   # messages with files on disk, and the live-delivery log (rooms.db once moved)
    with rooms.on(conn) as rooms_conn:
        if rooms_conn is not None:
            out.update(rooms.purge(rooms_conn, settings))

    # Codes and tokens past their expiry are useless; a day's grace keeps refresh-token
    # reuse detection working around the boundary.
    n = conn.execute("DELETE FROM oauth_codes WHERE expires_at < datetime('now', '-1 day')").rowcount
    n += conn.execute("DELETE FROM oauth_tokens WHERE expires_at < datetime('now', '-1 day')").rowcount
    if n:
        out["oauth_tokens"] = n

    days = settings.oauth_client_days
    if days > 0:
        n = conn.execute("DELETE FROM oauth_grants WHERE revoked_at IS NOT NULL AND revoked_at < datetime('now', ?)",
                         (_older(days),)).rowcount
        if n:
            out["oauth_grants"] = n
        # Registered but never connected (a consent page nobody finished, or anyone
        # registering clients): a day. Otherwise: no live connection and unused for `days`.
        n = conn.execute(
            "DELETE FROM oauth_clients WHERE NOT EXISTS (SELECT 1 FROM oauth_grants g "
            "  WHERE g.client_row = oauth_clients.id AND g.revoked_at IS NULL) "
            "AND ((NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_row = oauth_clients.id) "
            "      AND created_at < datetime('now', '-1 day')) "
            "  OR coalesce(last_used_at, created_at) < datetime('now', ?))", (_older(days),)).rowcount
        if n:
            out["oauth_clients"] = n
    return out
