"""Access administration: shared libraries, who can read or edit them, groups,
which libraries may be searched from Claude and by whom, and a log of every
change. Admins only; editors (write grants) organise a library's contents in
the file manager but can't change access."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from erinne import api_tokens, permissions, users
from erinne.config import Settings
from erinne.db import transaction
from erinne.ingest import store
from erinne.web.deps import api_user, get_db, get_settings, page_user

router = APIRouter()


def admin_api_user(user=Depends(api_user), settings: Settings = Depends(get_settings)):
    if not users.is_admin(user, settings.admin_usernames):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


def _log(conn: sqlite3.Connection, actor: sqlite3.Row, action: str, target: str, detail: str = "") -> None:
    conn.execute("INSERT INTO access_log(actor_id, actor_name, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                 (actor["id"], actor["username"], action, target, detail))


def _library(conn: sqlite3.Connection, library_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM collections WHERE id = ? AND kind = 'library'", (library_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Library not found")
    return row


def _group(conn: sqlite3.Connection, group_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return row


def _grants(conn: sqlite3.Connection, library_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT g.can_write, g.can_claude, g.user_id, g.group_id, u.username, gr.name AS group_name "
        "FROM collection_grants g LEFT JOIN users u ON u.id = g.user_id LEFT JOIN groups gr ON gr.id = g.group_id "
        "WHERE g.collection_id = ? ORDER BY g.group_id IS NULL, COALESCE(u.username, gr.name) COLLATE NOCASE",
        (library_id,)).fetchall()
    return [{"type": "user" if r["user_id"] else "group", "id": r["user_id"] or r["group_id"],
             "name": r["username"] or r["group_name"], "access": "write" if r["can_write"] else "read",
             "claude": bool(r["can_claude"])}
            for r in rows]


@router.get("/admin/libraries", response_class=HTMLResponse)
def libraries_page(request: Request, user=Depends(page_user), settings: Settings = Depends(get_settings)):
    if not users.is_admin(user, settings.admin_usernames):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return request.app.state.templates.TemplateResponse(
        request, "admin_libraries.html", {"user": user, "is_admin": True, "active": "admin"})


# ----------------------------------------------------------------- libraries
@router.get("/api/admin/libraries")
def list_libraries(user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        "SELECT c.id, c.name, c.created_at, c.claude_enabled, c.claude_changed_at, "
        "u.username AS claude_changed_by, "
        "(SELECT count(*) FROM files f WHERE f.collection_id = c.id AND f.deleted_at IS NULL) AS files, "
        "(SELECT COALESCE(sum(b.size), 0) FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
        " WHERE f.collection_id = c.id) AS bytes "
        "FROM collections c LEFT JOIN users u ON u.id = c.claude_changed_by "
        "WHERE c.kind = 'library' ORDER BY c.name COLLATE NOCASE").fetchall()
    return {"libraries": [{**dict(r), "claude_enabled": bool(r["claude_enabled"]), "grants": _grants(conn, r["id"])}
                          for r in rows]}


@router.get("/api/admin/principals")
def principals(user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Who access can be granted to: active users and groups."""
    people = conn.execute("SELECT id, username AS name FROM users WHERE active = 1 "
                          "ORDER BY username COLLATE NOCASE").fetchall()
    groups = conn.execute("SELECT id, name FROM groups ORDER BY name COLLATE NOCASE").fetchall()
    return {"users": [dict(r) for r in people], "groups": [dict(r) for r in groups]}


class LibraryName(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.post("/api/admin/libraries", status_code=status.HTTP_201_CREATED)
def create_library(body: LibraryName, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    name = body.name.strip()
    if permissions.get_library(conn, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already a library called “{name}”.")
    with transaction(conn):
        library_id = permissions.create_library(conn, name)
        # The creator can fill it straight away.
        permissions.grant_user(conn, library_id, user["id"], can_write=True)
        _log(conn, user, "library.create", name)
    return {"id": library_id, "name": name}


@router.patch("/api/admin/libraries/{library_id}")
def rename_library(library_id: int, body: LibraryName, user=Depends(admin_api_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    old = _library(conn, library_id)["name"]
    name = body.name.strip()
    other = permissions.get_library(conn, name)
    if other and other["id"] != library_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already a library called “{name}”.")
    with transaction(conn):
        conn.execute("UPDATE collections SET name = ? WHERE id = ?", (name, library_id))
        _log(conn, user, "library.rename", name, f"was {old}")
    return {"id": library_id, "name": name}


@router.delete("/api/admin/libraries/{library_id}")
def delete_library(library_id: int, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    """Delete a library and everything in it (bin included), for good."""
    name = _library(conn, library_id)["name"]
    file_ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE collection_id = ?", (library_id,))]
    for file_id in file_ids:
        store.delete_file(conn, settings, file_id)  # also removes blobs nothing else uses
    conn.execute("DELETE FROM collections WHERE id = ?", (library_id,))  # folders and grants cascade
    _log(conn, user, "library.delete", name, f"{len(file_ids)} files")
    return {"status": "deleted", "id": library_id, "files": len(file_ids)}


class GrantBody(BaseModel):
    type: Literal["user", "group"]
    id: int
    access: Literal["read", "write", "none"]
    claude: bool | None = None    # None keeps the grant's current Claude setting


@router.put("/api/admin/libraries/{library_id}/grants")
def set_grant(library_id: int, body: GrantBody, user=Depends(admin_api_user),
              conn: sqlite3.Connection = Depends(get_db)):
    library = _library(conn, library_id)
    table = "users" if body.type == "user" else "groups"
    column = "username" if body.type == "user" else "name"
    principal = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (body.id,)).fetchone()
    if principal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such {body.type}")
    who = principal[0] if body.type == "user" else f"group {principal[0]}"
    with transaction(conn):
        if body.access == "none":
            (permissions.revoke_user if body.type == "user" else permissions.revoke_group)(conn, library_id, body.id)
            _log(conn, user, "grant.remove", library["name"], who)
        else:
            grant = permissions.grant_user if body.type == "user" else permissions.grant_group
            grant(conn, library_id, body.id, can_write=body.access == "write", can_claude=body.claude)
            claude = "" if body.claude is None else f", Claude {'on' if body.claude else 'off'}"
            _log(conn, user, "grant", library["name"], f"{who}: {body.access}{claude}")
    return {"grants": _grants(conn, library_id)}


class ClaudeBody(BaseModel):
    enabled: bool


@router.put("/api/admin/libraries/{library_id}/claude")
def set_claude(library_id: int, body: ClaudeBody, user=Depends(admin_api_user),
               conn: sqlite3.Connection = Depends(get_db)):
    """Allow (or stop) searching this library from Claude. People still need a
    grant with Claude switched on; this is the library-wide master switch."""
    library = _library(conn, library_id)
    with transaction(conn):
        permissions.set_claude_enabled(conn, library_id, body.enabled, user["id"])
        _log(conn, user, "claude.enable" if body.enabled else "claude.disable", library["name"])
    return {"id": library_id, "claude_enabled": body.enabled}


@router.put("/api/admin/users/{user_id}/inbox-claude")
def set_inbox_claude(user_id: int, body: ClaudeBody, user=Depends(admin_api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Allow (or stop) a person searching their own inbox from Claude."""
    owner = users.get_user_by_id(conn, user_id)
    inbox = permissions.private_collection_id(conn, user_id) if owner else None
    if inbox is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    with transaction(conn):
        permissions.set_claude_enabled(conn, inbox, body.enabled, user["id"])
        _log(conn, user, "claude.enable" if body.enabled else "claude.disable", f"inbox of {owner['username']}")
    return {"user_id": user_id, "claude_enabled": body.enabled}


# -------------------------------------------------------------------- groups
@router.get("/api/admin/groups")
def list_groups(user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    groups = conn.execute("SELECT id, name FROM groups ORDER BY name COLLATE NOCASE").fetchall()
    members = conn.execute("SELECT m.group_id, u.id, u.username FROM group_members m JOIN users u ON u.id = m.user_id "
                           "ORDER BY u.username COLLATE NOCASE").fetchall()
    return {"groups": [{"id": g["id"], "name": g["name"],
                        "members": [{"id": m["id"], "name": m["username"]} for m in members if m["group_id"] == g["id"]]}
                       for g in groups]}


class GroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    members: list[int] = []


def _check_users(conn: sqlite3.Connection, user_ids: list[int]) -> None:
    if not user_ids:
        return
    marks = ",".join("?" * len(user_ids))
    found = {r[0] for r in conn.execute(f"SELECT id FROM users WHERE id IN ({marks})", user_ids)}
    if missing := set(user_ids) - found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such user: {sorted(missing)[0]}")


def _member_names(conn: sqlite3.Connection, group_id: int) -> str:
    rows = conn.execute("SELECT u.username FROM group_members m JOIN users u ON u.id = m.user_id "
                        "WHERE m.group_id = ? ORDER BY u.username COLLATE NOCASE", (group_id,))
    return ", ".join(r[0] for r in rows) or "nobody"


@router.post("/api/admin/groups", status_code=status.HTTP_201_CREATED)
def create_group(body: GroupBody, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    name = body.name.strip()
    if conn.execute("SELECT 1 FROM groups WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already a group called “{name}”.")
    _check_users(conn, body.members)
    group_id = permissions.create_group(conn, name, list(dict.fromkeys(body.members)))
    _log(conn, user, "group.create", name, _member_names(conn, group_id))
    return {"id": group_id, "name": name}


class GroupRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.patch("/api/admin/groups/{group_id}")
def rename_group(group_id: int, body: GroupRename, user=Depends(admin_api_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    old = _group(conn, group_id)["name"]
    name = body.name.strip()
    other = conn.execute("SELECT id FROM groups WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if other and other["id"] != group_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already a group called “{name}”.")
    with transaction(conn):
        conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
        _log(conn, user, "group.rename", name, f"was {old}")
    return {"id": group_id, "name": name}


class MembersBody(BaseModel):
    members: list[int]


@router.put("/api/admin/groups/{group_id}/members")
def set_members(group_id: int, body: MembersBody, user=Depends(admin_api_user),
                conn: sqlite3.Connection = Depends(get_db)):
    name = _group(conn, group_id)["name"]
    _check_users(conn, body.members)
    permissions.set_group_members(conn, group_id, body.members)
    _log(conn, user, "group.members", name, _member_names(conn, group_id))
    return {"id": group_id, "members": permissions.group_member_ids(conn, group_id)}


@router.delete("/api/admin/groups/{group_id}")
def delete_group(group_id: int, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Delete a group; its library grants go with it (members keep their own)."""
    name = _group(conn, group_id)["name"]
    with transaction(conn):
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))  # members and grants cascade
        _log(conn, user, "group.delete", name)
    return {"status": "deleted", "id": group_id}


# ------------------------------------------------------------------ overview
@router.get("/api/admin/access")
def access_overview(user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Who can see what: every active user's libraries, access level, Claude
    use and the grants that give it."""
    people = conn.execute(
        "SELECT id, username, role, (SELECT count(*) FROM api_tokens t WHERE t.user_id = users.id "
        "AND t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > datetime('now'))) AS tokens "
        "FROM users WHERE active = 1 ORDER BY username COLLATE NOCASE").fetchall()
    return {"users": [{"id": p["id"], "name": p["username"], "role": p["role"], "tokens": p["tokens"],
                       "libraries": permissions.access_summary(conn, p["id"])} for p in people]}


@router.delete("/api/admin/users/{user_id}/tokens")
def revoke_user_tokens(user_id: int, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Cut someone off from Claude at once: revoke all their API tokens."""
    owner = users.get_user_by_id(conn, user_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    with transaction(conn):
        count = api_tokens.revoke_all(conn, user_id)
        _log(conn, user, "tokens.revoke", owner["username"], f"{count} token(s)")
    return {"revoked": count}


@router.get("/api/admin/claude-activity")
def claude_activity(limit: int = 100, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Recent MCP tool calls: who searched what, and which files' text went to Claude."""
    rows = conn.execute(
        "SELECT m.at, u.username, m.tool, m.args, m.file_ids, m.ok, m.ms FROM mcp_calls m "
        "LEFT JOIN users u ON u.id = m.user_id ORDER BY m.id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
    file_ids = {f for r in rows for f in json.loads(r["file_ids"])}
    names = {}
    if file_ids:
        marks = ",".join("?" * len(file_ids))
        names = {r[0]: r[1] for r in conn.execute(f"SELECT id, display_name FROM files WHERE id IN ({marks})",
                                                  tuple(file_ids))}
    return {"calls": [{"at": r["at"], "user": r["username"], "tool": r["tool"], "args": json.loads(r["args"]),
                       "files": [names.get(f, f"#{f} (deleted)") for f in json.loads(r["file_ids"])],
                       "ok": bool(r["ok"]), "ms": r["ms"]} for r in rows]}


@router.get("/api/admin/log")
def access_log(limit: int = 100, user=Depends(admin_api_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT at, actor_name, action, target, detail FROM access_log ORDER BY id DESC LIMIT ?",
                        (max(1, min(limit, 1000)),)).fetchall()
    return {"entries": [dict(r) for r in rows]}
