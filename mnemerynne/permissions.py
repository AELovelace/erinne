"""Who may read, edit or use from Claude what: collections and the folders in them.

A user owns their inbox outright. In a library, access comes from grants on
the library and on its folders (to the user or to a group they are in). A
folder inherits its parent's access (the library's, for a top-level folder)
unless its `inherit` flag is off; its own grants add to that and pass down.
Effective access is the union of everything that reaches a folder. Claude
use additionally needs the library's (or inbox's) Claude switch.

`reach()` works this out per user and collection. Retrieval must turn it
into a search scope applied inside the search queries themselves (FTS and
vector), never filter results afterwards.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mnemerynne.db import transaction


@dataclass
class Reach:
    """What one user may do in one collection. Folder sets hold ids of folders
    in any state (binned too): callers decide separately what is live."""
    collection_id: int
    kind: str                                         # 'private' | 'library'
    name: str
    root_read: bool = False                           # files at the top level
    root_write: bool = False
    root_claude: bool = False
    read: set[int] = field(default_factory=set)       # folder ids
    write: set[int] = field(default_factory=set)
    claude: set[int] = field(default_factory=set)
    parents: dict[int, int | None] = field(default_factory=dict)   # every folder in the collection

    def can_read(self, folder_id: int | None) -> bool:
        return self.root_read if folder_id is None else folder_id in self.read

    def can_write(self, folder_id: int | None) -> bool:
        return self.root_write if folder_id is None else folder_id in self.write

    def visible(self) -> set[int]:
        """Readable folders plus their ancestors, so people can navigate down to them."""
        seen = set(self.read)
        for folder_id in self.read:
            parent = self.parents.get(folder_id)
            while parent is not None and parent not in seen:
                seen.add(parent)
                parent = self.parents.get(parent)
        return seen

    @property
    def everything(self) -> bool:
        """True when the user can read the whole collection."""
        return self.root_read and len(self.read) == len(self.parents)

    @property
    def any_write(self) -> bool:
        return self.root_write or bool(self.write)

    def for_claude(self) -> "Reach":
        """The part of this reach that may be searched from Claude, read-only."""
        return Reach(self.collection_id, self.kind, self.name, root_read=self.root_claude,
                     root_claude=self.root_claude, read=set(self.claude), claude=set(self.claude),
                     parents=self.parents)


def _group_ids(conn: sqlite3.Connection, user_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT group_id FROM group_members WHERE user_id = ?", (user_id,))]


def _principal_sql(groups: list[int], alias: str) -> tuple[str, tuple]:
    marks = ",".join("?" * len(groups))
    sql = f"({alias}.user_id = ?" + (f" OR {alias}.group_id IN ({marks})" if groups else "") + ")"
    return sql, tuple(groups)


def reach(conn: sqlite3.Connection, user_id: int) -> dict[int, Reach]:
    """Every collection the user can reach anything in, keyed by id."""
    out: dict[int, Reach] = {}
    inbox = conn.execute("SELECT id, name, claude_enabled FROM collections "
                         "WHERE kind = 'private' AND owner_user_id = ?", (user_id,)).fetchone()
    if inbox:
        parents = {r[0]: r[1] for r in conn.execute("SELECT id, parent_id FROM folders WHERE collection_id = ?",
                                                    (inbox["id"],))}
        claude = bool(inbox["claude_enabled"])
        out[inbox["id"]] = Reach(inbox["id"], "private", inbox["name"], True, True, claude, set(parents),
                                 set(parents), set(parents) if claude else set(), parents)

    groups = _group_ids(conn, user_id)
    who, group_params = _principal_sql(groups, "g")
    library_grants: dict[int, list[tuple[bool, bool]]] = {}
    for r in conn.execute(f"SELECT g.collection_id, g.can_write, g.can_claude FROM collection_grants g "
                          f"JOIN collections c ON c.id = g.collection_id WHERE c.kind = 'library' AND {who}",
                          (user_id, *group_params)):
        library_grants.setdefault(r[0], []).append((bool(r[1]), bool(r[2])))
    folder_grants: dict[int, dict[int, list[tuple[bool, bool]]]] = {}
    for r in conn.execute(f"SELECT f.collection_id, g.folder_id, g.can_write, g.can_claude FROM folder_grants g "
                          f"JOIN folders f ON f.id = g.folder_id JOIN collections c ON c.id = f.collection_id "
                          f"WHERE c.kind = 'library' AND {who}", (user_id, *group_params)):
        folder_grants.setdefault(r[0], {}).setdefault(r[1], []).append((bool(r[2]), bool(r[3])))

    for cid in sorted(set(library_grants) | set(folder_grants)):
        lib = conn.execute("SELECT name, claude_enabled FROM collections WHERE id = ?", (cid,)).fetchone()
        rows = conn.execute("SELECT id, parent_id, inherit FROM folders WHERE collection_id = ?", (cid,)).fetchall()
        out[cid] = _library_reach(cid, lib["name"], bool(lib["claude_enabled"]), library_grants.get(cid, []),
                                  folder_grants.get(cid, {}), rows)
    return out


Level = tuple[bool, bool, bool]   # (read, write, claude)
_NONE: Level = (False, False, False)


def _level(grants: list[tuple[bool, bool]], claude_on: bool) -> Level:
    if not grants:
        return _NONE
    return True, any(w for w, _ in grants), claude_on and any(c for _, c in grants)


def _union(a: Level, b: Level) -> Level:
    return a[0] or b[0], a[1] or b[1], a[2] or b[2]


def _library_reach(cid: int, name: str, claude_on: bool, library_grants: list, folder_grants: dict,
                   folders: list) -> Reach:
    root = _level(library_grants, claude_on)
    info = {r["id"]: (r["parent_id"], bool(r["inherit"])) for r in folders}
    memo: dict[int, Level] = {}

    def effective(folder_id: int) -> Level:
        if folder_id not in memo:
            parent, inherit = info[folder_id]
            inherited = (effective(parent) if parent in info else root) if inherit else _NONE
            memo[folder_id] = _union(_level(folder_grants.get(folder_id, []), claude_on), inherited)
        return memo[folder_id]

    out = Reach(cid, "library", name, *root, parents={f: p for f, (p, _) in info.items()})
    for folder_id in info:
        read, write, claude = effective(folder_id)
        if read:
            out.read.add(folder_id)
        if write:
            out.write.add(folder_id)
        if claude:
            out.claude.add(folder_id)
    return out


def claude_reach(conn: sqlite3.Connection, user_id: int) -> dict[int, Reach]:
    """What the user may search from Claude (read-only), per collection."""
    out = {}
    for cid, r in reach(conn, user_id).items():
        part = r.for_claude()
        if part.root_read or part.read:
            out[cid] = part
    return out


def readable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    """Collections the user can read anything in."""
    return {cid for cid, r in reach(conn, user_id).items() if r.root_read or r.read}


def writable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    """Collections the user can change anything in."""
    return {cid for cid, r in reach(conn, user_id).items() if r.any_write}


def claude_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    """Collections the user can search anything in from Claude (always a
    subset of readable_collection_ids)."""
    return set(claude_reach(conn, user_id))


def private_collection_id(conn: sqlite3.Connection, user_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def readable_collections(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Collections for display: the user's inbox first, then libraries by name."""
    ids = tuple(readable_collection_ids(conn, user_id))
    if not ids:
        return []
    return conn.execute(
        f"SELECT c.id, c.kind, c.name FROM collections c WHERE c.id IN ({','.join('?' * len(ids))}) "
        "ORDER BY c.kind = 'library', c.name COLLATE NOCASE", ids,
    ).fetchall()


# ----------------------------------------------------------------- libraries
def create_library(conn: sqlite3.Connection, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Library name must not be empty.")
    cur = conn.execute("INSERT INTO collections(kind, name) VALUES ('library', ?)", (name,))
    return cur.lastrowid


def get_library(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM collections WHERE kind = 'library' AND name = ? COLLATE NOCASE", (name.strip(),)
    ).fetchone()


def _grant(conn: sqlite3.Connection, collection_id: int, column: str, principal_id: int,
           can_write: bool, can_claude: bool | None) -> None:
    _require_library(conn, collection_id)
    claude = None if can_claude is None else int(can_claude)
    conn.execute(
        f"INSERT INTO collection_grants(collection_id, {column}, can_write, can_claude) "
        f"VALUES (?, ?, ?, COALESCE(?, 1)) "
        f"ON CONFLICT(collection_id, {column}) WHERE {column} IS NOT NULL "
        f"DO UPDATE SET can_write = excluded.can_write, can_claude = COALESCE(?, can_claude)",
        (collection_id, principal_id, int(can_write), claude, claude),
    )


def grant_user(conn: sqlite3.Connection, collection_id: int, user_id: int, *, can_write: bool = False,
               can_claude: bool | None = None) -> None:
    """Grant (or update) a user's access to a library. `can_claude=None` keeps
    the grant's current Claude setting (on for a new grant)."""
    _grant(conn, collection_id, "user_id", user_id, can_write, can_claude)


def grant_group(conn: sqlite3.Connection, collection_id: int, group_id: int, *, can_write: bool = False,
                can_claude: bool | None = None) -> None:
    _grant(conn, collection_id, "group_id", group_id, can_write, can_claude)


def set_claude_enabled(conn: sqlite3.Connection, collection_id: int, enabled: bool, by_user_id: int) -> None:
    """Switch Claude search on or off for a whole library or one inbox."""
    if conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone() is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    conn.execute(
        "UPDATE collections SET claude_enabled = ?, claude_changed_by = ?, claude_changed_at = datetime('now') "
        "WHERE id = ?", (int(enabled), by_user_id, collection_id))


def revoke_user(conn: sqlite3.Connection, collection_id: int, user_id: int) -> None:
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = ? AND user_id = ?", (collection_id, user_id)
    )


def revoke_group(conn: sqlite3.Connection, collection_id: int, group_id: int) -> None:
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = ? AND group_id = ?", (collection_id, group_id)
    )


# ------------------------------------------------------------------- folders
def library_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
    """A folder that may take grants: one in a library (inbox folders never do)."""
    row = conn.execute("SELECT f.*, c.kind, c.name AS library_name FROM folders f "
                       "JOIN collections c ON c.id = f.collection_id WHERE f.id = ?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError(f"Folder {folder_id} does not exist.")
    if row["kind"] != "library":
        raise ValueError("Only folders in libraries can be shared; inboxes stay private.")
    return row


def grant_folder(conn: sqlite3.Connection, folder_id: int, kind: str, principal_id: int, *,
                 can_write: bool = False, can_claude: bool | None = None) -> None:
    """Grant (or update) a user's or group's access to a folder and what is below it.
    `can_claude=None` keeps the grant's current Claude setting (on for a new grant)."""
    library_folder(conn, folder_id)
    column = {"user": "user_id", "group": "group_id"}[kind]
    claude = None if can_claude is None else int(can_claude)
    conn.execute(
        f"INSERT INTO folder_grants(folder_id, {column}, can_write, can_claude) VALUES (?, ?, ?, COALESCE(?, 1)) "
        f"ON CONFLICT(folder_id, {column}) WHERE {column} IS NOT NULL "
        f"DO UPDATE SET can_write = excluded.can_write, can_claude = COALESCE(?, can_claude)",
        (folder_id, principal_id, int(can_write), claude, claude))


def revoke_folder(conn: sqlite3.Connection, folder_id: int, kind: str, principal_id: int) -> None:
    column = {"user": "user_id", "group": "group_id"}[kind]
    conn.execute(f"DELETE FROM folder_grants WHERE folder_id = ? AND {column} = ?", (folder_id, principal_id))


def set_folder_inherit(conn: sqlite3.Connection, folder_id: int, inherit: bool, *, copy_inherited: bool = False) -> None:
    """Turn a folder's inheritance on or off. With `copy_inherited`, turning it
    off first turns what the folder currently inherits into its own grants, so
    nobody loses access until those grants are removed one by one."""
    library_folder(conn, folder_id)
    with transaction(conn):
        if not inherit and copy_inherited:
            for g in inherited_grants(conn, folder_id):
                kind = "user" if g["user_id"] else "group"
                principal = g["user_id"] or g["group_id"]
                existing = conn.execute(
                    f"SELECT can_write FROM folder_grants WHERE folder_id = ? AND {kind}_id = ?",
                    (folder_id, principal)).fetchone()
                if existing is None or (g["can_write"] and not existing["can_write"]):
                    grant_folder(conn, folder_id, kind, principal, can_write=bool(g["can_write"]),
                                 can_claude=bool(g["can_claude"]))
        conn.execute("UPDATE folders SET inherit = ? WHERE id = ?", (int(inherit), folder_id))


def _grants_on(conn: sqlite3.Connection, table: str, column: str, target_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT g.user_id, g.group_id, g.can_write, g.can_claude, u.username, gr.name AS group_name "
        f"FROM {table} g LEFT JOIN users u ON u.id = g.user_id LEFT JOIN groups gr ON gr.id = g.group_id "
        f"WHERE g.{column} = ? ORDER BY g.group_id IS NULL, COALESCE(u.username, gr.name) COLLATE NOCASE",
        (target_id,)).fetchall()


def folder_grants(conn: sqlite3.Connection, folder_id: int) -> list[sqlite3.Row]:
    """Grants made on the folder itself."""
    return _grants_on(conn, "folder_grants", "folder_id", folder_id)


def library_grants(conn: sqlite3.Connection, collection_id: int) -> list[sqlite3.Row]:
    return _grants_on(conn, "collection_grants", "collection_id", collection_id)


def inherited_grants(conn: sqlite3.Connection, folder_id: int) -> list[dict]:
    """Grants that reach a folder from above, nearest first, each with where it
    comes from: [{user_id, group_id, can_write, can_claude, username,
    group_name, source: {kind: 'folder'|'library', id, name}}]."""
    row = library_folder(conn, folder_id)
    out: list[dict] = []
    if not row["inherit"]:
        return out
    parent = row["parent_id"]
    while parent is not None:
        folder = conn.execute("SELECT id, name, parent_id, inherit FROM folders WHERE id = ?", (parent,)).fetchone()
        source = {"kind": "folder", "id": folder["id"], "name": folder["name"]}
        out += [{**dict(g), "source": source} for g in folder_grants(conn, folder["id"])]
        if not folder["inherit"]:
            return out
        parent = folder["parent_id"]
    source = {"kind": "library", "id": row["collection_id"], "name": row["library_name"]}
    return out + [{**dict(g), "source": source} for g in library_grants(conn, row["collection_id"])]


# -------------------------------------------------------------------- groups
def create_group(conn: sqlite3.Connection, name: str, member_ids: list[int] = ()) -> int:
    with transaction(conn):
        group_id = conn.execute("INSERT INTO groups(name) VALUES (?)", (name.strip(),)).lastrowid
        conn.executemany(
            "INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", [(group_id, u) for u in member_ids]
        )
    return group_id


def set_group_members(conn: sqlite3.Connection, group_id: int, member_ids: list[int]) -> None:
    """Replace a group's members with exactly `member_ids`."""
    with transaction(conn):
        conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)",
                         [(group_id, u) for u in dict.fromkeys(member_ids)])


def group_member_ids(conn: sqlite3.Connection, group_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))]


# ------------------------------------------------------------------ overview
def _top_folders(folders: set[int], parents: dict[int, int | None]) -> list[int]:
    """The folders in `folders` that have no ancestor in it."""
    def has_ancestor(folder_id: int) -> bool:
        parent = parents.get(folder_id)
        while parent is not None:
            if parent in folders:
                return True
            parent = parents.get(parent)
        return False
    return [f for f in folders if not has_ancestor(f)]


def _folder_path(conn: sqlite3.Connection, folder_id: int) -> str:
    names = []
    while folder_id is not None:
        row = conn.execute("SELECT name, parent_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        names.append(row["name"])
        folder_id = row["parent_id"]
    return "/".join(reversed(names))


def _describe(conn: sqlite3.Connection, root: bool, folders: set[int], parents: dict, everything: bool) -> str:
    if everything:
        return "everything"
    parts = (["top level"] if root else []) + sorted(_folder_path(conn, f) for f in _top_folders(folders, parents))
    return ", ".join(parts) or "nothing"


def access_summary(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Every collection a user can reach (inbox first, then libraries): what
    they can read, edit and use from Claude there, and which grants give it."""
    groups = _group_ids(conn, user_id)
    who, group_params = _principal_sql(groups, "g")
    out = []
    for cid, r in sorted(reach(conn, user_id).items(), key=lambda kv: (kv[1].kind != "private", kv[1].name.lower())):
        if not (r.root_read or r.read):
            continue
        all_folders = set(r.parents)
        if r.kind == "private":
            via = ["own inbox"]
        else:
            via = [row["name"] or "direct" for row in conn.execute(
                f"SELECT gr.name FROM collection_grants g LEFT JOIN groups gr ON gr.id = g.group_id "
                f"WHERE g.collection_id = ? AND {who} ORDER BY gr.name IS NOT NULL, gr.name", (cid, user_id, *group_params))]
            via += [f"{_folder_path(conn, row['folder_id'])}: {row['name'] or 'direct'}" for row in conn.execute(
                f"SELECT g.folder_id, gr.name FROM folder_grants g JOIN folders f ON f.id = g.folder_id "
                f"LEFT JOIN groups gr ON gr.id = g.group_id WHERE f.collection_id = ? AND {who}",
                (cid, user_id, *group_params))]
        out.append({
            "id": cid, "name": r.name, "kind": r.kind,
            "read": _describe(conn, r.root_read, r.read, r.parents, r.everything),
            "edit": _describe(conn, r.root_write, r.write, r.parents, r.root_write and r.write == all_folders),
            "claude": _describe(conn, r.root_claude, r.claude, r.parents, r.root_claude and r.claude == all_folders),
            "via": via,
        })
    return out


def _require_library(conn: sqlite3.Connection, collection_id: int) -> None:
    row = conn.execute("SELECT kind FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    if row["kind"] != "library":
        # Private inboxes are never shared; publish a library instead.
        raise ValueError("Only libraries can be granted; private inboxes stay private.")
