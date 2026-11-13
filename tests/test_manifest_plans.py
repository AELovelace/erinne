"""sync.manifest finds a root's files one of two ways (by folder for a small root, in id order for a big one).
Either way, every page must hold exactly what it did: the backup app decides what to upload and delete from it."""

import json

import pytest

from mnemerynne import db, permissions, sync, users


def _setup(conn, in_root_every: int):
    """A backed-up folder (three levels), a folder elsewhere, and files spread so that one in `in_root_every` is
    the root's; some of the root's are deleted. Returns (root, the expected live files in id order)."""
    uid = users.create_user(conn, "jane", "password123")
    cid = permissions.private_collection_id(conn, uid)
    with db.transaction(conn):
        def folder(parent, name):
            return conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, ?, ?)",
                                (cid, parent, name)).lastrowid
        top = folder(None, "Backup")
        a, elsewhere = folder(top, "a"), folder(None, "Elsewhere")
        b = folder(a, "b")
        binned = folder(top, "binned")
        conn.execute("UPDATE folders SET deleted_at = datetime('now') WHERE id = ?", (binned,))
        mine = [top, a, b]
        expected = []
        for n in range(300):
            sha = f"{n:064x}"
            conn.execute("INSERT INTO blobs(sha256, kind, size, rel_path) VALUES (?, 'md', ?, ?)", (sha, n, f"b/{n}"))
            in_root = n % in_root_every == 0
            where = mine[n % 3] if in_root else (binned if n % 7 == 0 else elsewhere)
            fid = conn.execute("INSERT INTO files(collection_id, folder_id, blob_sha256, display_name) "
                               "VALUES (?, ?, ?, ?)", (cid, where, sha, f"f{n}.md")).lastrowid
            if in_root and n % 11 == 0:
                conn.execute("UPDATE files SET deleted_at = datetime('now') WHERE id = ?", (fid,))
            elif in_root:
                path = {top: "", a: "a/", b: "a/b/"}[where] + f"f{n}.md"
                expected.append((fid, path))
    return sync.Root(1, uid, "PC", "Backup", cid, top), expected


def _all_pages(conn, root, limit):
    after, got = 0, []
    while True:
        page = sync.manifest(conn, root, after, limit)
        got += [f["path"] for f in page["files"]]
        if page["next"] is None:
            return got, page
        after = page["next"]


@pytest.mark.parametrize("in_root_every, way", [(1, "in id order"), (2, "in id order"), (40, "by folder")])
def test_every_page_holds_the_same_files_either_way(conn, in_root_every, way):
    root, expected = _setup(conn, in_root_every)
    sql = sync._manifest_files_sql(conn, {root.folder_id: None})
    assert ("+f.folder_id" in sql) == (way == "in id order")     # the case really takes the way it's named for
    got, last = _all_pages(conn, root, limit=7)
    assert got == [path for _, path in expected]                  # every live file, in id order, none twice
    assert last["next"] is None


def test_the_ids_handed_to_the_query_are_the_roots_folders(conn):
    root, _ = _setup(conn, 2)
    tree = {r["id"] for r in conn.execute(f"WITH RECURSIVE {sync._TREE} SELECT id FROM t", (root.folder_id,))}
    rows = conn.execute(sync._manifest_files_sql(conn, dict.fromkeys(tree)), (0, json.dumps(sorted(tree)), 1000))
    assert {r["folder_id"] for r in rows} <= tree                 # nothing from Elsewhere or the bin
