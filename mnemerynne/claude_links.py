"""Links Claude can fetch: a file's text behind a short-lived address.

Tool results through /mcp carry `link`s for the person, which need their
sign-in, so Claude can't open them. When a result points at a file, it also
gets a `fetch` address (/c/<id>) that Claude's web fetch can open:

- One per person, Claude connection (token or OAuth grant) and file, reused
  while it has a while left (the cited page goes in the address: ?page=N).
  It expires after a set time (admins: Access → Settings, meta
  'claude_links'), and revoking the connection ends it.
- Checked again on every fetch: the person is active, the connection still
  works, links are on, and the file is still shared with Claude (and not
  hidden from it). A failed check looks like any unknown link.
- Serves the extracted text, from the cited page on, a part at a time; never
  the original file. Each fetch is logged with Claude's other calls.

Anyone holding an address can read that file's text until it expires, e.g.
from a shared transcript: that's the trade-off for Claude reading without a
sign-in, which is why links are short-lived and limited in fetches.
"""

from __future__ import annotations

import json
import secrets
import sqlite3

from mnemerynne import fs

SETTINGS_KEY = "claude_links"
DEFAULTS = {"enabled": True, "minutes": 60}
MINUTES = (5, 1440)
REUSE_MINUTES = 10        # a link with less left than this isn't handed out again
MAX_FETCHES = 100         # per link
PART_CHARS = 60_000       # text per fetch; the rest is a "more" address away


class LinkError(Exception):
    """Unknown, expired or no longer allowed: all look the same to the fetcher."""

    status = 404


class TooMany(LinkError):
    status = 429


# ----------------------------------------------------------------- settings
def config(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    stored = {}
    if row:
        try:
            stored = json.loads(row["value"])
        except ValueError:
            pass
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save_config(conn: sqlite3.Connection, enabled: bool | None = None, minutes: int | None = None) -> dict:
    cfg = config(conn)
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    if minutes is not None:
        if not MINUTES[0] <= int(minutes) <= MINUTES[1]:
            raise ValueError(f"Links last between {MINUTES[0]} minutes and {MINUTES[1] // 60} hours.")
        cfg["minutes"] = int(minutes)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (SETTINGS_KEY, json.dumps(cfg)))
    return cfg


# -------------------------------------------------------------------- links
def make(conn: sqlite3.Connection, caller, file_id: int, minutes: int) -> str:
    """A link id for this caller and file: an unexpired one again, or a new one."""
    row = conn.execute(
        "SELECT id FROM claude_links WHERE user_id = ? AND file_id = ? AND token_id IS ? AND grant_id IS ? "
        "AND expires_at > datetime('now', ?) AND fetches < ? ORDER BY expires_at DESC LIMIT 1",
        (caller.user_id, file_id, caller.token_id, caller.grant_id, f"+{REUSE_MINUTES} minutes",
         MAX_FETCHES)).fetchone()
    if row:
        return row["id"]
    link_id = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO claude_links(id, user_id, token_id, grant_id, file_id, expires_at) "
                 "VALUES (?, ?, ?, ?, ?, datetime('now', ?))",
                 (link_id, caller.user_id, caller.token_id, caller.grant_id, file_id, f"+{minutes} minutes"))
    return link_id


def attach(conn: sqlite3.Connection, base: str, caller, out):
    """Give every entry of a tool result that points at a file (a `file_id` and a `link`) its `fetch`
    address. Only for Claude's own connections (a token or grant), with links on and a public address."""
    if not base or (caller.token_id is None and caller.grant_id is None):
        return out
    cfg = config(conn)
    if not cfg["enabled"]:
        return out

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("file_id"), int) and "link" in value:
                page = value.get("page") if isinstance(value.get("page"), int) else None
                value["fetch"] = (f"{base}/c/{make(conn, caller, value['file_id'], cfg['minutes'])}"
                                  + (f"?page={page}" if page else ""))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(out)
    return out


def _check(conn: sqlite3.Connection, link_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    """(link, file) if the link may be read now; LinkError otherwise."""
    link = conn.execute("SELECT * FROM claude_links WHERE id = ? AND expires_at > datetime('now')",
                        (link_id,)).fetchone()
    if link is None or not config(conn)["enabled"]:
        raise LinkError
    if link["fetches"] >= MAX_FETCHES:
        raise TooMany
    if not conn.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (link["user_id"],)).fetchone():
        raise LinkError
    if link["token_id"] is not None and not conn.execute(
            "SELECT 1 FROM api_tokens WHERE id = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))", (link["token_id"],)).fetchone():
        raise LinkError
    if link["grant_id"] is not None and not conn.execute(
            "SELECT 1 FROM oauth_grants WHERE id = ? AND revoked_at IS NULL", (link["grant_id"],)).fetchone():
        raise LinkError
    file = conn.execute("SELECT f.*, c.kind AS collection_kind, c.name AS collection_name FROM files f "
                        "JOIN collections c ON c.id = f.collection_id WHERE f.id = ? AND f.deleted_at IS NULL",
                        (link["file_id"],)).fetchone()
    if file is None or not fs.Access.for_claude(conn, link["user_id"]).can_read_file(file):
        raise LinkError
    return link, file


def read(conn: sqlite3.Connection, base: str, link_id: str, start: int | None = None,
         page: int | None = None) -> str:
    """The file's text for one fetch, as Markdown: from `start` (a part number), else from the page
    before the cited `page` (for context), else from the beginning. Counts and logs the fetch.
    LinkError when it may not be read."""
    from mnemerynne import permissions

    link, file = _check(conn, link_id)
    units = conn.execute("SELECT unit_no, page_no, heading_path, text FROM blob_units WHERE sha256 = ? "
                         "ORDER BY unit_no", (file["blob_sha256"],)).fetchall()
    if start is None:
        start = next((u["unit_no"] for u in units if page and u["page_no"] and u["page_no"] >= page - 1),
                     units[0]["unit_no"] if units else 0)
    place = permissions.PRIVATE_LABEL if file["collection_kind"] == "private" else file["collection_name"]
    lines = [f"# {file['display_name']}", "",
             f"From {place} in Mnemerynne. This copy of its text expires at {link['expires_at']} UTC.", ""]
    if not units:
        lines.append("This file has no readable text (it may be a picture, or still being read).")
    shown, more = 0, None
    for u in units:
        if u["unit_no"] < start:
            continue
        if shown and shown + len(u["text"]) > PART_CHARS:
            more = u["unit_no"]
            break
        heading = f"Page {u['page_no']}" if u["page_no"] else (u["heading_path"] or "")
        if heading:
            lines += [f"## {heading}", ""]
        lines += [u["text"][:PART_CHARS], ""]
        shown += len(u["text"])
    if start > (units[0]["unit_no"] if units else 0):
        lines.append(f"(Earlier parts: {base}/c/{link_id}?from={units[0]['unit_no']})")
    if more is not None:
        lines.append(f"(More of this file: {base}/c/{link_id}?from={more})")
    conn.execute("UPDATE claude_links SET fetches = fetches + 1, last_fetched_at = datetime('now') WHERE id = ?",
                 (link_id,))
    conn.execute("INSERT INTO mcp_calls(user_id, token_id, grant_id, tool, args, file_ids, ok) "
                 "VALUES (?, ?, ?, 'fetch_link', ?, ?, 1)",
                 (link["user_id"], link["token_id"], link["grant_id"],
                  json.dumps({"file_id": file["id"], "from": start}), json.dumps([file["id"]])))
    return "\n".join(lines).rstrip() + "\n"
