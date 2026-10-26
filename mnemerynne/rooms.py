"""Rooms: channels and direct messages between people, Discord/Matrix style.

- A channel is open (anyone may read, join and post), private (its members only) or tied to a
  directory group (whoever is in the group, checked live with group_refs, so it follows AD/Entra).
- A DM is one room per pair of people; a group DM up to MAX_GROUP_DM people. Both are created by
  opening them, so asking twice gives the same room.
- Messages are Markdown (raw HTML escaped, links allowlisted: ingest.markdown), at most MAX_CHARS,
  with one level of threads (replies to a top-level message), reactions, pins, edits and deletes
  (kept as "message deleted"). @name, @channel, @here and @Erynne mentions are recorded.
- Every change is also written to chat_events, the ordered log live delivery replays from
  (rooms_bus.py); the web layer wakes the listeners after it commits.

Everything here takes the person (a users row) and checks they can see the room; admins pass
admin=True to moderate (delete, archive), and that is logged by the caller.

Rooms is off until an admin turns it on (Settings → Rooms, meta 'rooms_enabled'): some
organisations don't want a chat app. Off, the page, its API, its stream and Claude's room tools all
answer as if it didn't exist; what was written is kept, and comes back when it's turned on again.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
from pathlib import Path
from typing import BinaryIO

from mnemerynne import comments, group_refs
from mnemerynne.config import Settings
from mnemerynne.ingest import markdown

MAX_CHARS = 4000
MAX_TOPIC = 300
MAX_GROUP_DM = 9
PAGE = 50
ERYNNE = "erynne"
SPECIAL_MENTIONS = ("channel", "here", ERYNNE)
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MENTION = re.compile(r"(?<![\w@/.])@([A-Za-z0-9._-]{1,64})")
_EMOJI = re.compile(r"^[^\s<>&\"']{1,16}$")


class RoomError(ValueError):
    """Refused; safe to show. status: the HTTP status the web layer answers with."""
    status = 400


class NotFound(RoomError):
    status = 404


class Forbidden(RoomError):
    status = 403


# ------------------------------------------------------------------- access
def room(conn: sqlite3.Connection, room_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM chat_rooms WHERE id = ?", (room_id,)).fetchone()
    if row is None:
        raise NotFound("No such room.")
    return row


def _member(conn: sqlite3.Connection, room_id: int, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM chat_members WHERE room_id = ? AND user_id = ?",
                        (room_id, user_id)).fetchone()


def can_see(conn: sqlite3.Connection, user: sqlite3.Row, r: sqlite3.Row, directory,
            groups: set[str] | None = None) -> bool:
    """May this person read the room? Open: anyone. Private, DMs: its members. Group: the group."""
    if not user["active"]:
        return False
    if r["visibility"] == "open":
        return True
    if r["visibility"] == "group":
        return group_refs.in_any(conn, user, directory, r["group_ref"], groups)
    return _member(conn, r["id"], user["id"]) is not None


def visible(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory, *, admin: bool = False) -> sqlite3.Row:
    """The room, if this person may read it (admins may, to moderate); else NotFound (never Forbidden,
    so a private room's existence isn't given away)."""
    r = room(conn, room_id)
    if admin or can_see(conn, user, r, directory):
        return r
    raise NotFound("No such room.")


def _writable(conn, user, r, directory) -> None:
    if r["archived_at"]:
        raise Forbidden("This room is archived; it can be read but not written in.")
    if not can_see(conn, user, r, directory):
        raise NotFound("No such room.")


def _owner_or_admin(conn, user, r, admin: bool) -> None:
    if admin:
        return
    m = _member(conn, r["id"], user["id"])
    if r["kind"] != "channel" or m is None or m["role"] != "owner":
        raise Forbidden("Only the channel's owner (or an admin) can change that.")


def audience(conn: sqlite3.Connection, r: sqlite3.Row, directory) -> list[sqlite3.Row]:
    """The people a room reaches (active users): an open channel's joined members, a group channel's
    group, a private channel's or DM's members."""
    if r["visibility"] == "group":
        everyone = conn.execute("SELECT * FROM users WHERE active = 1").fetchall()
        return [u for u in everyone if group_refs.in_any(conn, u, directory, r["group_ref"])]
    return conn.execute("SELECT u.* FROM chat_members m JOIN users u ON u.id = m.user_id "
                        "WHERE m.room_id = ? AND u.active = 1 AND m.hidden = 0 ORDER BY u.username COLLATE NOCASE",
                        (r["id"],)).fetchall()


# ------------------------------------------------------------------- events
def event(conn: sqlite3.Connection, room_id: int, kind: str, payload: dict) -> int:
    """Append to the log live delivery reads; the caller wakes the bus after committing."""
    return conn.execute("INSERT INTO chat_events(room_id, kind, payload) VALUES (?, ?, ?)",
                        (room_id, kind, json.dumps(payload, ensure_ascii=False))).lastrowid


# -------------------------------------------------------------------- rooms
def _clean_name(name: str) -> str:
    name = (name or "").strip().lstrip("#").lower().replace(" ", "-")
    if not _NAME.match(name):
        raise RoomError("A channel name is up to 40 letters, digits, - and _, starting with a letter or digit.")
    return name


CREATORS_KEY = "rooms_creators"
ENABLED_KEY = "rooms_enabled"


def enabled(conn: sqlite3.Connection) -> bool:
    """Whether this organisation uses Rooms (off unless an admin turned it on)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ENABLED_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ENABLED_KEY, "1" if on else "0"))


def creators(conn: sqlite3.Connection) -> str:
    """The group(s) whose members may make channels; '' = everyone (meta 'rooms_creators')."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (CREATORS_KEY,)).fetchone()
    return row["value"] if row else ""


def set_creators(conn: sqlite3.Connection, value: str) -> str:
    value = group_refs.join(group_refs.split(value))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (CREATORS_KEY, value))
    return value


def may_create(conn: sqlite3.Connection, user: sqlite3.Row, directory, *, admin: bool = False) -> bool:
    group = creators(conn)
    return admin or not group or group_refs.in_any(conn, user, directory, group)


def all_channels(conn: sqlite3.Connection) -> list[dict]:
    """Every channel, private and archived ones too (admins' Settings → Rooms)."""
    return [{"id": r["id"], "name": r["name"], "visibility": r["visibility"], "topic": r["topic"],
             "group": group_refs.label(conn, r["group_ref"]), "archived": bool(r["archived_at"]),
             "messages": r["messages"], "last_at": r["last_at"]}
            for r in conn.execute("SELECT c.*, (SELECT count(*) FROM chat_messages m WHERE m.room_id = c.id) AS messages, "
                                  "(SELECT max(created_at) FROM chat_messages m WHERE m.room_id = c.id) AS last_at "
                                  "FROM chat_rooms c WHERE c.kind = 'channel' ORDER BY c.name")]


def create_channel(conn: sqlite3.Connection, user: sqlite3.Row, name: str, *, topic: str = "",
                   visibility: str = "open", group_ref: str = "") -> int:
    if visibility not in ("open", "private", "group"):
        raise RoomError("A channel is open, private or tied to a group.")
    if visibility == "group" and not group_refs.split(group_ref):
        raise RoomError("Pick the group whose members are in this channel.")
    name = _clean_name(name)
    if conn.execute("SELECT 1 FROM chat_rooms WHERE kind = 'channel' AND name = ?", (name,)).fetchone():
        raise RoomError(f"There is already a #{name}.")
    room_id = conn.execute(
        "INSERT INTO chat_rooms(kind, name, topic, visibility, group_ref, created_by) VALUES ('channel', ?, ?, ?, ?, ?)",
        (name, (topic or "").strip()[:MAX_TOPIC], visibility, group_refs.join(group_refs.split(group_ref))
         if visibility == "group" else "", user["id"])).lastrowid
    conn.execute("INSERT INTO chat_members(room_id, user_id, role) VALUES (?, ?, 'owner')", (room_id, user["id"]))
    event(conn, room_id, "room.update", {"room_id": room_id})
    return room_id


def open_dm(conn: sqlite3.Connection, user: sqlite3.Row, usernames: list[str]) -> int:
    """The DM (one other person) or group DM with these people, made on first use."""
    names = {n.strip().lstrip("@").lower() for n in usernames if n and n.strip()} - {user["username"].lower()}
    if not names:
        raise RoomError("Pick who to message.")
    if len(names) + 1 > MAX_GROUP_DM:
        raise RoomError(f"A group DM is at most {MAX_GROUP_DM} people; make a private channel instead.")
    marks = ",".join("?" * len(names))
    found = conn.execute(f"SELECT id, username FROM users WHERE active = 1 AND lower(username) IN ({marks})",
                         tuple(names)).fetchall()
    missing = names - {r["username"].lower() for r in found}
    if missing:
        raise RoomError(f"There's nobody called {', '.join(sorted(missing))}.")
    ids = sorted({user["id"], *(r["id"] for r in found)})
    key = ",".join(str(i) for i in ids)
    row = conn.execute("SELECT id FROM chat_rooms WHERE dm_key = ?", (key,)).fetchone()
    if row:
        conn.execute("UPDATE chat_members SET hidden = 0 WHERE room_id = ? AND user_id = ?", (row["id"], user["id"]))
        return row["id"]
    kind = "dm" if len(ids) == 2 else "group_dm"
    room_id = conn.execute("INSERT INTO chat_rooms(kind, visibility, dm_key, created_by) VALUES (?, 'direct', ?, ?)",
                           (kind, key, user["id"])).lastrowid
    conn.executemany("INSERT INTO chat_members(room_id, user_id) VALUES (?, ?)", [(room_id, i) for i in ids])
    event(conn, room_id, "room.update", {"room_id": room_id})
    return room_id


def update(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory, *, admin: bool = False,
           name: str | None = None, topic: str | None = None, erynne: bool | None = None,
           archived: bool | None = None) -> None:
    r = visible(conn, user, room_id, directory, admin=admin)
    if r["kind"] != "channel" and (name is not None or archived is not None):
        raise RoomError("Only channels have a name and can be archived.")
    if r["kind"] == "channel":
        _owner_or_admin(conn, user, r, admin)
    elif not can_see(conn, user, r, directory):
        raise NotFound("No such room.")
    if name is not None:
        clean = _clean_name(name)
        if conn.execute("SELECT 1 FROM chat_rooms WHERE kind = 'channel' AND name = ? AND id <> ?",
                        (clean, room_id)).fetchone():
            raise RoomError(f"There is already a #{clean}.")
        conn.execute("UPDATE chat_rooms SET name = ? WHERE id = ?", (clean, room_id))
    if topic is not None:
        conn.execute("UPDATE chat_rooms SET topic = ? WHERE id = ?", (topic.strip()[:MAX_TOPIC], room_id))
    if erynne is not None:
        conn.execute("UPDATE chat_rooms SET erynne = ? WHERE id = ?", (int(erynne), room_id))
    if archived is not None:
        conn.execute("UPDATE chat_rooms SET archived_at = CASE WHEN ? THEN datetime('now') END WHERE id = ?",
                     (int(archived), room_id))
    event(conn, room_id, "room.update", {"room_id": room_id})


def join(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory) -> None:
    r = visible(conn, user, room_id, directory)
    if r["kind"] != "channel" or r["visibility"] == "private":
        raise Forbidden("This room is by invitation.")
    conn.execute("INSERT INTO chat_members(room_id, user_id) VALUES (?, ?) ON CONFLICT DO UPDATE SET hidden = 0",
                 (room_id, user["id"]))
    event(conn, room_id, "member", {"room_id": room_id, "user": user["username"], "joined": True})


def leave(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory) -> None:
    """Leave a channel; a DM is only hidden (it comes back when someone writes)."""
    r = visible(conn, user, room_id, directory)
    if r["kind"] == "channel":
        conn.execute("DELETE FROM chat_members WHERE room_id = ? AND user_id = ?", (room_id, user["id"]))
        event(conn, room_id, "member", {"room_id": room_id, "user": user["username"], "joined": False})
    else:
        conn.execute("UPDATE chat_members SET hidden = 1 WHERE room_id = ? AND user_id = ?", (room_id, user["id"]))


def invite(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, username: str, directory) -> None:
    """Add someone to a private channel (any member may)."""
    r = visible(conn, user, room_id, directory)
    if r["kind"] != "channel" or r["visibility"] != "private":
        raise RoomError("Only private channels have invitations; others are open to their audience.")
    _writable(conn, user, r, directory)
    other = conn.execute("SELECT id, username FROM users WHERE active = 1 AND username = ? COLLATE NOCASE",
                         (username.strip().lstrip("@"),)).fetchone()
    if other is None:
        raise RoomError(f"There's nobody called {username}.")
    conn.execute("INSERT INTO chat_members(room_id, user_id) VALUES (?, ?) ON CONFLICT DO UPDATE SET hidden = 0",
                 (room_id, other["id"]))
    event(conn, room_id, "member", {"room_id": room_id, "user": other["username"], "joined": True})


def remove(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, username: str, directory, *,
           admin: bool = False) -> None:
    r = visible(conn, user, room_id, directory, admin=admin)
    _owner_or_admin(conn, user, r, admin)
    other = conn.execute("SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
                         (username.strip().lstrip("@"),)).fetchone()
    if other is None or not conn.execute("DELETE FROM chat_members WHERE room_id = ? AND user_id = ?",
                                         (room_id, other["id"])).rowcount:
        raise RoomError(f"{username} isn't a member.")
    event(conn, room_id, "member", {"room_id": room_id, "user": other["username"], "joined": False})


def _dm_label(conn: sqlite3.Connection, r: sqlite3.Row, user_id: int) -> tuple[str, list[str]]:
    others = [row[0] for row in conn.execute(
        "SELECT u.username FROM chat_members m JOIN users u ON u.id = m.user_id WHERE m.room_id = ? AND u.id <> ? "
        "ORDER BY u.username COLLATE NOCASE", (r["id"], user_id))]
    return ", ".join(others) or "(just you)", others


def view_room(conn: sqlite3.Connection, r: sqlite3.Row, user: sqlite3.Row) -> dict:
    m = _member(conn, r["id"], user["id"])
    out = {"id": r["id"], "kind": r["kind"], "visibility": r["visibility"], "topic": r["topic"],
           "erynne": bool(r["erynne"]), "archived": bool(r["archived_at"]),
           "joined": m is not None and not m["hidden"], "role": m["role"] if m else None,
           "notify": m["notify"] if m else "all", "group": group_refs.label(conn, r["group_ref"])}
    if r["kind"] == "channel":
        out["name"] = r["name"]
    else:
        out["name"], out["people"] = _dm_label(conn, r, user["id"])
    return out


def _unread(conn: sqlite3.Connection, room_id: int, user_id: int, last_read: int) -> tuple[int, int]:
    unread = conn.execute("SELECT count(*) FROM chat_messages WHERE room_id = ? AND id > ? AND thread_root_id IS NULL "
                          "AND deleted_at IS NULL AND (user_id IS NOT ? OR via <> 'web')",
                          (room_id, last_read, user_id)).fetchone()[0]
    mentions = conn.execute("SELECT count(*) FROM chat_mentions c JOIN chat_messages m ON m.id = c.message_id "
                            "WHERE m.room_id = ? AND c.user_id = ? AND m.id > ? AND m.deleted_at IS NULL",
                            (room_id, user_id, last_read)).fetchone()[0]
    return unread, mentions


def rooms_for(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> list[dict]:
    """The sidebar: channels the person is in or can see (open ones they joined, group ones they belong
    to, private ones they're in), then their DMs; each with unread and mention counts."""
    groups = group_refs.member_of(conn, user, directory)
    rows = conn.execute(
        "SELECT r.*, m.user_id AS me, m.hidden, m.last_read_id FROM chat_rooms r LEFT JOIN chat_members m "
        "ON m.room_id = r.id AND m.user_id = ? WHERE r.archived_at IS NULL ORDER BY r.kind <> 'channel', r.name",
        (user["id"],)).fetchall()
    out = []
    for r in rows:
        if r["visibility"] == "group":
            if not group_refs.in_any(conn, user, directory, r["group_ref"], groups):
                continue
        elif r["me"] is None or r["hidden"]:
            continue
        item = view_room(conn, r, user)
        item["unread"], item["mentions"] = _unread(conn, r["id"], user["id"], r["last_read_id"] or 0)
        last = conn.execute("SELECT id, created_at FROM chat_messages WHERE room_id = ? AND deleted_at IS NULL "
                            "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        item["last_at"] = last["created_at"] if last else None
        out.append(item)
    dms = sorted((i for i in out if i["kind"] != "channel"), key=lambda i: i["last_at"] or "", reverse=True)
    return [i for i in out if i["kind"] == "channel"] + dms


def browse(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> list[dict]:
    """Channels the person could join: open ones, and group ones they belong to."""
    groups = group_refs.member_of(conn, user, directory)
    out = []
    for r in conn.execute("SELECT * FROM chat_rooms WHERE kind = 'channel' AND archived_at IS NULL "
                          "AND visibility IN ('open', 'group') ORDER BY name").fetchall():
        if r["visibility"] == "group" and not group_refs.in_any(conn, user, directory, r["group_ref"], groups):
            continue
        item = view_room(conn, r, user)
        item["members"] = conn.execute("SELECT count(*) FROM chat_members WHERE room_id = ?", (r["id"],)).fetchone()[0]
        out.append(item)
    return out


def unread_summary(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> dict:
    """For the nav badge: rooms with unread messages, mentions, and unread DMs (all 0 while Rooms is off)."""
    if not enabled(conn):
        return {"rooms": 0, "mentions": 0, "dms": 0}
    listed = rooms_for(conn, user, directory)
    return {"rooms": sum(1 for r in listed if r["unread"]), "mentions": sum(r["mentions"] for r in listed),
            "dms": sum(r["unread"] for r in listed if r["kind"] != "channel" and r["notify"] != "none")}


# ----------------------------------------------------------------- messages
def _message_row(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT m.*, u.username FROM chat_messages m LEFT JOIN users u ON u.id = m.user_id "
                       "WHERE m.id = ?", (message_id,)).fetchone()
    if row is None:
        raise NotFound("No such message.")
    return row


def view_message(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """A message as everyone in the room sees it (per-viewer bits, like "mine", are worked out by the page)."""
    deleted = row["deleted_at"] is not None
    author = "Erynne" if row["via"] == "erynne" and row["user_id"] is None else row["username"]
    out = {"id": row["id"], "room_id": row["room_id"], "thread_root_id": row["thread_root_id"],
           "user": row["username"], "who": "Erynne" if author == "Erynne" else comments.who(
               row["username"], row["via"] if row["via"] in ("claude", "erynne") else None),
           "via": row["via"], "created_at": row["created_at"], "edited_at": row["edited_at"],
           "deleted": deleted, "pinned": row["pinned_at"] is not None,
           "body": "" if deleted else row["body"], "html": "" if deleted else render(row["body"])}
    if row["thread_root_id"] is None:
        replies = conn.execute("SELECT count(*), max(created_at) FROM chat_messages WHERE thread_root_id = ? "
                               "AND deleted_at IS NULL", (row["id"],)).fetchone()
        out["replies"], out["last_reply_at"] = replies[0], replies[1]
    # Each emoji once, in the order first used, with who reacted in the order they did.
    reactions: dict[str, list[str]] = {}
    for emoji, name in conn.execute("SELECT r.emoji, u.username FROM chat_reactions r JOIN users u ON u.id = r.user_id "
                                    "WHERE r.message_id = ? ORDER BY r.rowid", (row["id"],)):
        reactions.setdefault(emoji, []).append(name)
    out["reactions"] = [] if deleted else [{"emoji": e, "users": u} for e, u in reactions.items()]
    out["attachments"] = [] if deleted else [
        {"id": a["id"], "name": a["name"], "mime": a["mime"], "bytes": a["bytes"]}
        for a in conn.execute("SELECT id, name, mime, bytes FROM chat_attachments WHERE message_id = ? ORDER BY id",
                              (row["id"],))]
    return out


def render(body: str) -> str:
    """Message Markdown as HTML: raw HTML escaped, links allowlisted (ingest.markdown)."""
    return markdown.render_html(body)


def _mentions(conn: sqlite3.Connection, r: sqlite3.Row, body: str, author_id: int | None, directory,
              online: set[int]) -> tuple[list[tuple[int, str]], bool]:
    """(user id, kind) mentioned, and whether @Erynne was asked."""
    names = {m.group(1).rstrip(".").lower() for m in _MENTION.finditer(body)}
    erynne = ERYNNE in names
    found: dict[int, str] = {}
    if names & {"channel", "here"}:
        for u in audience(conn, r, directory):
            if u["id"] != author_id and ("channel" in names or u["id"] in online):
                found.setdefault(u["id"], "channel" if "channel" in names else "here")
    people = names - set(SPECIAL_MENTIONS)
    if people:
        marks = ",".join("?" * len(people))
        for u in conn.execute(f"SELECT * FROM users WHERE active = 1 AND lower(username) IN ({marks})", tuple(people)):
            if u["id"] != author_id and can_see(conn, u, r, directory):
                found[u["id"]] = "user"
    return list(found.items()), erynne


def post(conn: sqlite3.Connection, user: sqlite3.Row | None, room_id: int, body: str, directory, *,
         via: str = "web", thread_root_id: int | None = None, attachment_ids: list[int] | None = None,
         online: set[int] | None = None) -> tuple[dict, bool]:
    """Post a message (user None: Erynne). Returns (the message, whether @Erynne was asked)."""
    r = room(conn, room_id)
    if user is not None:
        _writable(conn, user, r, directory)
    elif r["archived_at"]:
        raise Forbidden("This room is archived.")
    body = (body or "").strip()
    attachment_ids = attachment_ids or []
    if not body and not attachment_ids:
        raise RoomError("Write something first.")
    if len(body) > MAX_CHARS:
        raise RoomError(f"A message is at most {MAX_CHARS} characters.")
    if thread_root_id is not None:
        root = conn.execute("SELECT room_id, thread_root_id FROM chat_messages WHERE id = ?",
                            (thread_root_id,)).fetchone()
        if root is None or root["room_id"] != room_id:
            raise NotFound("No such message to reply to.")
        thread_root_id = root["thread_root_id"] or thread_root_id   # replies to a reply join its thread
    message_id = conn.execute(
        "INSERT INTO chat_messages(room_id, user_id, via, thread_root_id, body) VALUES (?, ?, ?, ?, ?)",
        (room_id, user["id"] if user is not None else None, via, thread_root_id, body)).lastrowid
    if attachment_ids:
        marks = ",".join("?" * len(attachment_ids))
        claimed = conn.execute(f"UPDATE chat_attachments SET message_id = ? WHERE id IN ({marks}) AND room_id = ? "
                               f"AND user_id IS ? AND message_id IS NULL",
                               (message_id, *attachment_ids, room_id, user["id"] if user else None)).rowcount
        if claimed != len(set(attachment_ids)):
            raise RoomError("An attached file is missing; upload it again.")
    mentioned, erynne = _mentions(conn, r, body, user["id"] if user else None, directory, online or set())
    conn.executemany("INSERT INTO chat_mentions(message_id, user_id, kind) VALUES (?, ?, ?)",
                     [(message_id, uid, kind) for uid, kind in mentioned])
    # A DM someone had closed comes back when a message arrives.
    conn.execute("UPDATE chat_members SET hidden = 0 WHERE room_id = ? AND ? IN ('dm', 'group_dm')", (room_id, r["kind"]))
    if user is not None:
        conn.execute("UPDATE chat_members SET last_read_id = ? WHERE room_id = ? AND user_id = ?",
                     (message_id, room_id, user["id"]))
    out = view_message(conn, _message_row(conn, message_id))
    # Usernames, not ids: the page knows who it is by name, and lights up "mentions you".
    out["mentions"] = [conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()[0]
                       for uid, _ in mentioned]
    event(conn, room_id, "message.new", {"message": out})
    if thread_root_id is not None:
        event(conn, room_id, "message.edit", {"message": view_message(conn, _message_row(conn, thread_root_id))})
    return out, erynne and bool(r["erynne"]) and user is not None


def _own_message(conn, user, message_id, directory, *, admin=False, owner_ok=False) -> tuple[sqlite3.Row, sqlite3.Row]:
    msg = _message_row(conn, message_id)
    r = visible(conn, user, msg["room_id"], directory, admin=admin)
    mine = msg["user_id"] == user["id"]
    owner = False
    if owner_ok and not mine and r["kind"] == "channel":
        m = _member(conn, r["id"], user["id"])
        owner = m is not None and m["role"] == "owner"
    if not (mine or admin or owner):
        raise Forbidden("That's someone else's message.")
    if msg["deleted_at"]:
        raise RoomError("That message was deleted.")
    return msg, r


def edit(conn: sqlite3.Connection, user: sqlite3.Row, message_id: int, body: str, directory) -> dict:
    msg, r = _own_message(conn, user, message_id, directory)
    if r["archived_at"]:
        raise Forbidden("This room is archived.")
    body = (body or "").strip()
    if not body or len(body) > MAX_CHARS:
        raise RoomError(f"A message is 1 to {MAX_CHARS} characters.")
    conn.execute("UPDATE chat_messages SET body = ?, edited_at = datetime('now') WHERE id = ?", (body, message_id))
    out = view_message(conn, _message_row(conn, message_id))
    event(conn, r["id"], "message.edit", {"message": out})
    return out


def delete(conn: sqlite3.Connection, user: sqlite3.Row, message_id: int, directory, *, admin: bool = False) -> dict:
    """Your own message, or any in a channel you own (or any, as an admin). Kept as "message deleted"
    so threads still make sense; its attachments go."""
    msg, r = _own_message(conn, user, message_id, directory, admin=admin, owner_ok=True)
    conn.execute("UPDATE chat_messages SET deleted_at = datetime('now'), pinned_at = NULL WHERE id = ?", (message_id,))
    conn.execute("DELETE FROM chat_reactions WHERE message_id = ?", (message_id,))
    conn.execute("DELETE FROM chat_mentions WHERE message_id = ?", (message_id,))
    out = view_message(conn, _message_row(conn, message_id))
    event(conn, r["id"], "message.delete", {"message": out})
    return out


def pin(conn: sqlite3.Connection, user: sqlite3.Row, message_id: int, pinned: bool, directory) -> dict:
    msg = _message_row(conn, message_id)
    r = visible(conn, user, msg["room_id"], directory)
    _writable(conn, user, r, directory)
    if msg["deleted_at"]:
        raise RoomError("That message was deleted.")
    conn.execute("UPDATE chat_messages SET pinned_at = CASE WHEN ? THEN datetime('now') END, pinned_by = ? WHERE id = ?",
                 (int(pinned), user["id"] if pinned else None, message_id))
    out = view_message(conn, _message_row(conn, message_id))
    event(conn, r["id"], "pin", {"message": out})
    return out


def react(conn: sqlite3.Connection, user: sqlite3.Row, message_id: int, emoji: str, on: bool, directory) -> dict:
    emoji = (emoji or "").strip()
    if not _EMOJI.match(emoji):
        raise RoomError("That isn't a reaction.")
    msg = _message_row(conn, message_id)
    r = visible(conn, user, msg["room_id"], directory)
    _writable(conn, user, r, directory)
    if msg["deleted_at"]:
        raise RoomError("That message was deleted.")
    if on:
        if conn.execute("SELECT count(DISTINCT emoji) FROM chat_reactions WHERE message_id = ?",
                        (message_id,)).fetchone()[0] >= 20:
            raise RoomError("That message has enough different reactions.")
        conn.execute("INSERT OR IGNORE INTO chat_reactions(message_id, user_id, emoji) VALUES (?, ?, ?)",
                     (message_id, user["id"], emoji))
    else:
        conn.execute("DELETE FROM chat_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                     (message_id, user["id"], emoji))
    out = view_message(conn, _message_row(conn, message_id))
    event(conn, r["id"], "reaction", {"message": out})
    return out


def history(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory, *, before: int | None = None,
            limit: int = PAGE, admin: bool = False) -> list[dict]:
    """Top-level messages, oldest first, the `limit` before message `before` (latest when None)."""
    visible(conn, user, room_id, directory, admin=admin)
    rows = conn.execute("SELECT m.*, u.username FROM chat_messages m LEFT JOIN users u ON u.id = m.user_id "
                        "WHERE m.room_id = ? AND m.thread_root_id IS NULL AND (? IS NULL OR m.id < ?) "
                        "ORDER BY m.id DESC LIMIT ?", (room_id, before, before, max(1, min(limit, 200)))).fetchall()
    return [view_message(conn, r) for r in reversed(rows)]


def thread(conn: sqlite3.Connection, user: sqlite3.Row, root_id: int, directory) -> list[dict]:
    """A thread: its first message, then the replies, oldest first."""
    root = _message_row(conn, root_id)
    if root["thread_root_id"] is not None:
        root = _message_row(conn, root["thread_root_id"])
    visible(conn, user, root["room_id"], directory)
    rows = conn.execute("SELECT m.*, u.username FROM chat_messages m LEFT JOIN users u ON u.id = m.user_id "
                        "WHERE m.thread_root_id = ? ORDER BY m.id", (root["id"],)).fetchall()
    return [view_message(conn, root)] + [view_message(conn, r) for r in rows]


def pins(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, directory) -> list[dict]:
    visible(conn, user, room_id, directory)
    rows = conn.execute("SELECT m.*, u.username FROM chat_messages m LEFT JOIN users u ON u.id = m.user_id "
                        "WHERE m.room_id = ? AND m.pinned_at IS NOT NULL AND m.deleted_at IS NULL "
                        "ORDER BY m.pinned_at DESC", (room_id,)).fetchall()
    return [view_message(conn, r) for r in rows]


def mark_read(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, message_id: int, directory) -> None:
    """Read up to message_id. A group channel's reader gets a member row for the cursor (membership
    itself follows the group); an open channel only keeps a cursor once joined, so peeking at one
    doesn't put it in the sidebar."""
    r = visible(conn, user, room_id, directory)
    if r["visibility"] == "open" and _member(conn, room_id, user["id"]) is None:
        return
    conn.execute("INSERT INTO chat_members(room_id, user_id, last_read_id) VALUES (?, ?, ?) "
                 "ON CONFLICT(room_id, user_id) DO UPDATE SET last_read_id = max(last_read_id, excluded.last_read_id)",
                 (room_id, user["id"], message_id))


def set_notify(conn: sqlite3.Connection, user: sqlite3.Row, room_id: int, notify: str, directory) -> None:
    if notify not in ("all", "mentions", "none"):
        raise RoomError("Notifications are all, mentions or none.")
    visible(conn, user, room_id, directory)
    conn.execute("INSERT INTO chat_members(room_id, user_id, notify) VALUES (?, ?, ?) "
                 "ON CONFLICT(room_id, user_id) DO UPDATE SET notify = excluded.notify", (room_id, user["id"], notify))


def find_room(conn: sqlite3.Connection, user: sqlite3.Row, ref: int | str, directory) -> sqlite3.Row:
    """A room by id, "#channel" / "channel", or "@person" (their DM, made if needed): for the MCP tools."""
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return visible(conn, user, int(ref), directory)
    text = str(ref).strip()
    if text.startswith("@"):
        return room(conn, open_dm(conn, user, [text]))
    row = conn.execute("SELECT * FROM chat_rooms WHERE kind = 'channel' AND name = ?",
                       (text.lstrip("#").lower(),)).fetchone()
    if row is None or not can_see(conn, user, row, directory):
        raise NotFound(f"There's no room called {text} you can see. list_rooms shows yours.")
    return row


# -------------------------------------------------------------------- files
# Shown in the page; anything else is a download (an SVG or HTML file never renders inline).
INLINE = ("image/png", "image/jpeg", "image/gif", "image/webp")
EVENTS_DAYS = 7


def files_dir(settings: Settings) -> Path:
    return settings.data_dir / "rooms"


def save_upload(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, room_id: int, filename: str,
                stream: BinaryIO, directory) -> dict:
    """Keep a file for a message in this room (attached when the message is sent). Readable by whoever
    can see the room, as long as the message is kept."""
    r = room(conn, room_id)
    _writable(conn, user, r, directory)
    name = Path((filename or "file").replace("\\", "/")).name.strip()[:200] or "file"
    folder = files_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    rel = secrets.token_hex(16) + ".bin"
    path = folder / rel
    sha, size = hashlib.sha256(), 0
    try:
        with path.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > settings.room_file_bytes:
                    raise RoomError(f"A file is at most {settings.room_file_bytes // (1024 * 1024)} MB here; put "
                                    "bigger ones in Files and link them.")
                sha.update(chunk)
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise RoomError("That file is empty.")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    file_id = conn.execute("INSERT INTO chat_attachments(room_id, user_id, name, mime, bytes, sha256, rel_path) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (room_id, user["id"], name, mime, size, sha.hexdigest(), rel)).lastrowid
    return {"id": file_id, "name": name, "mime": mime, "bytes": size}


def attachment(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, file_id: int, directory,
               *, admin: bool = False) -> tuple[sqlite3.Row, Path]:
    """A file and where it is, if this person may read it: they can see its room, and it belongs to a
    message that wasn't deleted (or it's their own, not yet sent)."""
    row = conn.execute("SELECT a.*, m.deleted_at FROM chat_attachments a LEFT JOIN chat_messages m "
                       "ON m.id = a.message_id WHERE a.id = ?", (file_id,)).fetchone()
    if row is None or row["deleted_at"] is not None or (row["message_id"] is None and row["user_id"] != user["id"]):
        raise NotFound("No such file.")
    visible(conn, user, row["room_id"], directory, admin=admin)
    path = files_dir(settings) / row["rel_path"]
    if not path.is_file():
        raise NotFound("That file is gone.")
    return row, path


def purge(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    """Messages (and their files) past room_message_days, files never sent after a day, files of
    deleted messages, and live-delivery events after EVENTS_DAYS. Called hourly by retention.purge."""
    out: dict[str, int] = {}
    gone: list[str] = []
    if settings.room_message_days > 0:
        old = f"-{int(settings.room_message_days)} days"
        gone += [r[0] for r in conn.execute(
            "SELECT a.rel_path FROM chat_attachments a JOIN chat_messages m ON m.id = a.message_id "
            "WHERE m.created_at < datetime('now', ?)", (old,))]
        n = conn.execute("DELETE FROM chat_messages WHERE created_at < datetime('now', ?) AND id NOT IN "
                         "(SELECT thread_root_id FROM chat_messages WHERE thread_root_id IS NOT NULL "
                         " AND created_at >= datetime('now', ?))", (old, old)).rowcount
        if n:
            out["chat_messages"] = n
    stale = ("WHERE (message_id IS NULL AND created_at < datetime('now', '-1 day')) "
             "OR message_id IN (SELECT id FROM chat_messages WHERE deleted_at IS NOT NULL)")
    gone += [r[0] for r in conn.execute(f"SELECT rel_path FROM chat_attachments {stale}")]
    conn.execute(f"DELETE FROM chat_attachments {stale}")
    for rel in gone:
        (files_dir(settings) / rel).unlink(missing_ok=True)
    if gone:
        out["chat_attachments"] = len(gone)
    n = conn.execute("DELETE FROM chat_events WHERE created_at < datetime('now', ?)",
                     (f"-{EVENTS_DAYS} days",)).rowcount
    if n:
        out["chat_events"] = n
    return out


# ----------------------------------------------------------------- previews
_INTERNAL = re.compile(r"^/(files|wiki)/(\d+)(?:/[A-Za-z]*)?(?:[?#].*)?$")


def previews(conn: sqlite3.Connection, user: sqlite3.Row, links: list[str]) -> dict[str, dict]:
    """Cards for links to Mnemerynne files and wiki pages, worked out for this viewer: someone who can't
    read the file gets {"hidden": true}, so a room never shows them its name."""
    from mnemerynne import fs, proposals

    access = fs.Access.of(conn, user["id"])
    out: dict[str, dict] = {}
    for link in links[:20]:
        match = _INTERNAL.match(link or "")
        if not match:
            continue
        row = fs.file_row(conn, int(match.group(2)))
        if row is None or row["deleted_at"] is not None or not access.can_read_file(row):
            out[link] = {"hidden": True}
            continue
        out[link] = {"kind": "wiki" if match.group(1) == "wiki" else "file", "title": row["display_name"],
                     "where": proposals.where(conn, row["collection_id"], row["folder_id"])}
    return out
