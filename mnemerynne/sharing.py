"""Sharing a file or folder from Files: with an account, a group (Mnemerynne's or a domain group), everyone with
an active account, or anyone holding a link (migrations/0077_item_shares.sql).

Shares live only in Mnemerynne. The share's permissions on disk (what Explorer shows) never change, so a share
is easy to take back and can't break anything over SMB. fs.Access asks `granted()` what reaches a person through
shares, after their own rights, so every existing page (browse, viewer, editor, downloads) honours them.

Rules that keep it safe:
- A share lasts only while its maker could still do it themselves: their account is active and their *own*
  rights (never other shares: nothing is re-shared) still reach the item, with write for "Can edit". When they
  lose it, the share does nothing until they get it back.
- Folders share everything inside them, files added later included, but nothing above them: the person sees
  the shared folder as the top, and the folder names on the way down from the library stay hidden.
- SharePoint files can't be shared: SharePoint checks each person's own account (fs.Access.can_read_file).
- Links are always view-only. Their token is HMAC(secret key, nonce): 256 bits, nothing in it names the item,
  only its SHA-256 is looked up, and a wrong, expired or removed one is the same bare 404 (web/share_links.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass, field

from mnemerynne import fs, permissions

LINK_DAYS = 7                                    # a new link's expiry unless the sharer picks another
EXPIRY_CHOICES = (1, 7, 30, None)                # days; None: until removed
KINDS = ("user", "group", "ad_group", "everyone", "link")
LINK_PREFIX = "/s/"                              # the public address: /s/<token>
_ACTIVE = "(s.expires_at IS NULL OR s.expires_at > datetime('now'))"


class ShareRefused(ValueError):
    """The share can't be made or changed; the message is safe to show."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ links
def link_token(secret_key: str, nonce: str) -> str:
    """The token in a link's URL: 43 URL-safe characters (256 bits) only this server can make from the nonce."""
    if not secret_key:
        raise ShareRefused("Links need MNEMERYNNE_SECRET_KEY set on the server.", 503)
    mac = hmac.new(secret_key.encode(), b"mnemerynne-share-link:" + nonce.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def token_hash(token: str) -> str:
    """What the database keeps (and /s/ looks up) for a token: its SHA-256, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


def link_url(public_url: str, token: str) -> str:
    return (public_url or "").rstrip("/") + LINK_PREFIX + token


def link_target(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """The live link share a token opens, or None: wrong, expired, removed, its item binned or gone, or its
    maker no longer able to read it. Callers answer every None the same way (no hint which one it was)."""
    if not token or len(token) > 64:
        return None
    row = conn.execute(f"SELECT s.* FROM item_shares s WHERE s.link_hash = ? AND s.kind = 'link' AND {_ACTIVE}",
                       (token_hash(token),)).fetchone()
    if row is None or not _item_live(conn, row) or _maker_level(conn, row, {}) is None:
        return None
    conn.execute("UPDATE item_shares SET last_used_at = datetime('now') WHERE id = ?", (row["id"],))
    return row


# ------------------------------------------------------------------ what reaches a person
@dataclass
class Granted:
    """What reaches one person through shares, for fs.Access. Folder sets hold the shared folders and every
    folder below them; `roots` only the shared folders themselves (where the person's path starts)."""
    read: dict[int, set[int]] = field(default_factory=dict)       # collection -> folder ids
    write: dict[int, set[int]] = field(default_factory=dict)
    roots: dict[int, set[int]] = field(default_factory=dict)
    files: dict[int, bool] = field(default_factory=dict)          # file id -> may change it

    def can_read(self, collection_id: int, folder_id: int | None) -> bool:
        return folder_id is not None and folder_id in self.read.get(collection_id, ())

    def can_write(self, collection_id: int, folder_id: int | None) -> bool:
        return folder_id is not None and folder_id in self.write.get(collection_id, ())


def _ad_groups(conn: sqlite3.Connection, user_id: int) -> set[str]:
    """The person's domain groups (lower-cased), from the directory the app already holds; none without one."""
    from mnemerynne import share
    rt = share.current()
    account = conn.execute("SELECT ad_account FROM users WHERE id = ?", (user_id,)).fetchone()
    if rt is None or not getattr(rt.directory, "enabled", False) or not account or not account["ad_account"]:
        return set()
    try:
        return rt.directory.groups_of(account["ad_account"])
    except Exception:                                   # the directory is away: domain-group shares wait for it
        return set()


def _shares_for(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Live shares that name this person directly, through a group, or as everyone (links aren't here)."""
    groups = permissions._group_ids(conn, user_id)
    ad = sorted(_ad_groups(conn, user_id))
    marks_g = ",".join("?" * len(groups)) or "NULL"
    marks_a = ",".join("?" * len(ad)) or "NULL"
    return conn.execute(
        f"SELECT s.* FROM item_shares s JOIN users u ON u.id = ? WHERE {_ACTIVE} AND u.active = 1 "
        f"AND s.created_by <> u.id AND (s.kind = 'everyone' OR (s.kind = 'user' AND s.user_id = u.id) "
        f"OR (s.kind = 'group' AND s.group_id IN ({marks_g})) OR (s.kind = 'ad_group' AND lower(s.ad_group) IN ({marks_a})))",
        (user_id, *groups, *ad)).fetchall()


def _item_live(conn: sqlite3.Connection, row) -> bool:
    if row["file_id"] is not None:
        f = conn.execute("SELECT deleted_at, folder_id FROM files WHERE id = ?", (row["file_id"],)).fetchone()
        return f is not None and f["deleted_at"] is None and (f["folder_id"] is None or fs.is_live_folder(conn, f["folder_id"]))
    return fs.is_live_folder(conn, row["folder_id"])


def _place(conn: sqlite3.Connection, row) -> tuple[int, int | None]:
    """(collection, folder) of a share's item: a file's own folder, or the shared folder itself."""
    if row["file_id"] is not None:
        f = conn.execute("SELECT collection_id, folder_id FROM files WHERE id = ?", (row["file_id"],)).fetchone()
        return f["collection_id"], f["folder_id"]
    f = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (row["folder_id"],)).fetchone()
    return f["collection_id"], row["folder_id"]


def _maker_level(conn: sqlite3.Connection, row, makers: dict) -> bool | None:
    """What the share's maker may still do with its item through their own rights: True (change it), False
    (read it), None (nothing: the share stands still). `makers` caches one Access per maker for a pass."""
    maker = row["created_by"]
    if maker not in makers:
        active = conn.execute("SELECT active FROM users WHERE id = ?", (maker,)).fetchone()
        makers[maker] = fs.Access.of(conn, maker, shares=False) if active and active["active"] else None
    access = makers[maker]
    if access is None:
        return None
    cid, fid = _place(conn, row)
    if not access.can_read(cid, fid):
        return None
    return access.can_write(cid, fid)


def granted(conn: sqlite3.Connection, user_id: int) -> Granted:
    """Everything shares give this person right now (fs.Access asks once, the first time their own rights
    don't cover something). "Can edit" only counts while the maker may still change the item."""
    out = Granted()
    makers: dict = {}
    for row in _shares_for(conn, user_id):
        if not _item_live(conn, row):
            continue
        level = _maker_level(conn, row, makers)
        if level is None:
            continue
        write = bool(row["can_write"]) and level
        if row["file_id"] is not None:
            out.files[row["file_id"]] = out.files.get(row["file_id"], False) or write
            continue
        cid, top = _place(conn, row)
        below = set(fs.subtree(conn, top))
        out.read.setdefault(cid, set()).update(below)
        out.roots.setdefault(cid, set()).add(top)
        if write:
            out.write.setdefault(cid, set()).update(below)
    return out


# ------------------------------------------------------------------ making and listing shares
def _item(conn: sqlite3.Connection, item_type: str, item_id: int) -> sqlite3.Row:
    if item_type == "file":
        row = conn.execute("SELECT f.id, f.collection_id, f.folder_id, f.display_name AS name, f.deleted_at, "
                           "c.source FROM files f JOIN collections c ON c.id = f.collection_id WHERE f.id = ?",
                           (item_id,)).fetchone()
    elif item_type == "folder":
        row = conn.execute("SELECT f.id, f.collection_id, f.id AS folder_id, f.name, f.deleted_at, c.source "
                           "FROM folders f JOIN collections c ON c.id = f.collection_id WHERE f.id = ?",
                           (item_id,)).fetchone()
    else:
        raise ShareRefused("Only files and folders can be shared.")
    if row is None or row["deleted_at"] is not None:
        raise ShareRefused("Not found", 404)
    return row


def _may_share(conn: sqlite3.Connection, user_id: int, item: sqlite3.Row, can_write: bool) -> None:
    """The sharer's own rights (not shares) must cover what they give: read, and write for "Can edit"."""
    access = fs.Access.of(conn, user_id, shares=False)
    cid, fid = item["collection_id"], item["folder_id"]
    access.confirm(cid, fid)
    if not access.can_read(cid, fid):
        raise ShareRefused("Not found", 404)                 # the same 404 as a file that isn't there
    if item["source"] == "sharepoint":
        raise ShareRefused("SharePoint files are shared in SharePoint.")
    if can_write and not access.can_write(cid, fid):
        raise ShareRefused("You can only share with \"Can edit\" what you can change yourself.", 403)


def _check_target(conn: sqlite3.Connection, user_id: int, kind: str, target) -> dict:
    """The column a share's target goes in, checked: an active account (not yourself), a group, a domain group."""
    if kind == "user":
        row = conn.execute("SELECT id FROM users WHERE id = ? AND active = 1", (target,)).fetchone()
        if row is None:
            raise ShareRefused("No such account.")
        if row["id"] == user_id:
            raise ShareRefused("That's you: you have it already.")
        return {"user_id": row["id"]}
    if kind == "group":
        row = conn.execute("SELECT id FROM groups WHERE id = ?", (target,)).fetchone()
        if row is None:
            raise ShareRefused("No such group.")
        return {"group_id": row["id"]}
    if kind == "ad_group":
        name = str(target or "").strip()
        match = next((g for g in domain_groups() if g.lower() == name.lower()), None)
        if match is None:
            raise ShareRefused("No such domain group.")
        return {"ad_group": match}
    if kind in ("everyone", "link"):
        return {}
    raise ShareRefused("Share with a person, a group, everyone, or anyone with the link.")


def _expiry_sql(days) -> str | None:
    if days not in EXPIRY_CHOICES:
        raise ShareRefused("Pick 1, 7 or 30 days, or never.")
    return None if days is None else f"+{int(days)} days"


def create(conn: sqlite3.Connection, user_id: int, item_type: str, item_id: int, kind: str, *, target=None,
           can_write: bool = False, expires_days=None, secret_key: str = "") -> int:
    """Share an item; returns the share's id. Sharing again with the same person or group changes that share
    (its right and expiry) instead of adding another. A link (create_link) is always view-only and defaults to
    LINK_DAYS; other shares last until removed unless given days."""
    item = _item(conn, item_type, item_id)
    if kind == "link":
        can_write = False
        if not secret_key:
            raise ShareRefused("Links need MNEMERYNNE_SECRET_KEY set on the server.", 503)
    if expires_days == "default":
        expires_days = LINK_DAYS if kind == "link" else None
    _may_share(conn, user_id, item, can_write)
    cols = _check_target(conn, user_id, kind, target)
    expiry = _expiry_sql(expires_days)
    where = {"file_id": item_id if item_type == "file" else None, "folder_id": item_id if item_type == "folder" else None}
    if kind == "link":
        nonce = secrets.token_urlsafe(24)                       # 192 random bits; the token is made from it
        cols |= {"link_nonce": nonce, "link_hash": token_hash(link_token(secret_key, nonce))}
    else:
        same = conn.execute(
            "SELECT id FROM item_shares WHERE COALESCE(file_id, 0) = ? AND COALESCE(folder_id, 0) = ? AND kind = ? "
            "AND COALESCE(user_id, 0) = ? AND COALESCE(group_id, 0) = ? AND COALESCE(ad_group, '') = ?",
            (where["file_id"] or 0, where["folder_id"] or 0, kind, cols.get("user_id") or 0, cols.get("group_id") or 0,
             cols.get("ad_group") or "")).fetchone()
        if same is not None:
            conn.execute("UPDATE item_shares SET can_write = ?, expires_at = "
                         "CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END, created_by = ? WHERE id = ?",
                         (int(can_write), expiry, expiry, user_id, same["id"]))
            return same["id"]
    values = {**where, "kind": kind, **cols, "can_write": int(can_write), "created_by": user_id}
    names = ", ".join(values) + ", expires_at"
    marks = ", ".join("?" * len(values)) + ", CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END"
    return conn.execute(f"INSERT INTO item_shares ({names}) VALUES ({marks})",
                        (*values.values(), expiry, expiry)).lastrowid


def create_link(conn: sqlite3.Connection, user_id: int, item_type: str, item_id: int, secret_key: str, *,
                expires_days="default") -> tuple[int, str]:
    """A new "anyone with the link" share; returns (its id, the token)."""
    share_id = create(conn, user_id, item_type, item_id, "link", expires_days=expires_days, secret_key=secret_key)
    nonce = conn.execute("SELECT link_nonce FROM item_shares WHERE id = ?", (share_id,)).fetchone()[0]
    return share_id, link_token(secret_key, nonce)


def may_manage(conn: sqlite3.Connection, user_id: int, share: sqlite3.Row) -> bool:
    """Who may change or remove a share: its maker, or someone who may change the item's place themselves."""
    if share["created_by"] == user_id:
        return True
    access = fs.Access.of(conn, user_id, shares=False)
    cid, fid = _place(conn, share)
    return access.can_write(cid, fid)


def remove(conn: sqlite3.Connection, user_id: int, share_id: int) -> None:
    row = conn.execute("SELECT * FROM item_shares WHERE id = ?", (share_id,)).fetchone()
    if row is None or not may_manage(conn, user_id, row):
        raise ShareRefused("Not found", 404)
    conn.execute("DELETE FROM item_shares WHERE id = ?", (share_id,))


def of_item(conn: sqlite3.Connection, user_id: int, item_type: str, item_id: int, *, secret_key: str,
            public_url: str) -> dict:
    """The share dialog: who has the item through shares, and its links (rebuilt to copy again). Only for people
    who may share it (their own rights reach it)."""
    item = _item(conn, item_type, item_id)
    _may_share(conn, user_id, item, False)
    col = "file_id" if item_type == "file" else "folder_id"
    rows = conn.execute(
        f"SELECT s.*, u.username AS user_name, g.name AS group_name, m.username AS maker_name, "
        f"{_ACTIVE} AS live FROM item_shares s LEFT JOIN users u ON u.id = s.user_id "
        f"LEFT JOIN groups g ON g.id = s.group_id JOIN users m ON m.id = s.created_by "
        f"WHERE s.{col} = ? ORDER BY s.kind = 'link', s.created_at", (item_id,)).fetchall()
    out = []
    for r in rows:
        entry = {"id": r["id"], "kind": r["kind"], "can_write": bool(r["can_write"]), "expires_at": r["expires_at"],
                 "expired": not r["live"], "by": r["maker_name"], "created_at": r["created_at"],
                 "can_remove": may_manage(conn, user_id, r),
                 "name": {"user": r["user_name"], "group": r["group_name"], "ad_group": r["ad_group"],
                          "everyone": "Everyone with an account", "link": "Anyone with the link"}[r["kind"]]}
        if r["kind"] == "link":
            entry["url"] = link_url(public_url, link_token(secret_key, r["link_nonce"])) if secret_key else None
            entry["last_used_at"] = r["last_used_at"]
        out.append(entry)
    access = fs.Access.of(conn, user_id, shares=False)
    return {"item": {"type": item_type, "id": item_id, "name": item["name"]}, "shares": out,
            "can_edit": access.can_write(item["collection_id"], item["folder_id"]),
            "links": bool(secret_key), "link_days": LINK_DAYS}


def with_me(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Files' "Shared with me": the shared items themselves (what's inside a folder comes from browsing it)."""
    access = fs.Access.of(conn, user_id)
    seen: set[tuple[str, int]] = set()
    out = []
    for row in _shares_for(conn, user_id):
        key = ("file", row["file_id"]) if row["file_id"] is not None else ("folder", row["folder_id"])
        if key in seen or not _item_live(conn, row):
            continue
        cid, fid = _place(conn, row)
        if key[0] == "file":
            if row["file_id"] not in access._granted().files and not access.can_read(cid, fid):
                continue
            f = fs.file_row(conn, row["file_id"])
            name = f["display_name"]
            can_write = access.can_write_file(f)
        else:
            if not access.can_read(cid, fid):
                continue
            name = conn.execute("SELECT name FROM folders WHERE id = ?", (fid,)).fetchone()["name"]
            can_write = access.can_write(cid, fid)
        seen.add(key)
        maker = conn.execute("SELECT username FROM users WHERE id = ?", (row["created_by"],)).fetchone()
        out.append({"type": key[0], "id": key[1], "collection_id": cid, "folder_id": fid, "name": name,
                    "can_write": can_write, "by": maker["username"] if maker else None, "shared_at": row["created_at"],
                    "expires_at": row["expires_at"]})
    return sorted(out, key=lambda x: x["name"].lower())


def domain_groups() -> list[str]:
    """The domain's groups for the share dialog's picker; none without a directory."""
    from mnemerynne import share
    rt = share.current()
    if rt is None or not getattr(rt.directory, "enabled", False):
        return []
    try:
        return sorted(rt.directory.groups(), key=str.lower)
    except Exception:
        return []


def targets(conn: sqlite3.Connection, user_id: int) -> dict:
    """Who the share dialog can pick: active accounts (not yourself), Mnemerynne's groups, the domain's groups."""
    users = [{"id": r["id"], "name": r["username"]} for r in conn.execute(
        "SELECT id, username FROM users WHERE active = 1 AND id <> ? ORDER BY username COLLATE NOCASE", (user_id,))]
    groups = [{"id": r["id"], "name": r["name"]} for r in conn.execute(
        "SELECT id, name FROM groups ORDER BY name COLLATE NOCASE")]
    return {"users": users, "groups": groups, "ad_groups": domain_groups()}
