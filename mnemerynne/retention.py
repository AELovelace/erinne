"""Housekeeping for logs and OAuth leftovers; the worker runs purge() hourly.

The call logs hold what people asked and what came back (tool arguments, API
response excerpts), so they are kept only as long as the *_days settings say.
"""

from __future__ import annotations

import sqlite3

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

    from mnemerynne import rooms   # messages with files on disk, and the live-delivery log
    out.update(rooms.purge(conn, settings))

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
