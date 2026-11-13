"""fs.is_live_folder walks up from the folder instead of loading the whole collection.

It runs on every upload, every Files page and once per backup root whenever the backup app asks. Loading every folder
of a big collection each time filled the web app's worker threads, and Files stopped loading. The answer must stay the
same as live_folder_ids gives for the whole collection."""

from mnemerynne import fs, permissions, users


def _folder(conn, collection_id, parent_id, name, binned=False):
    """A folder straight into the table; binned = put in the bin itself (its contents are hidden by it)."""
    return conn.execute(
        "INSERT INTO folders(collection_id, parent_id, name, deleted_at) VALUES (?, ?, ?, ?)",
        (collection_id, parent_id, name, "2026-11-13 12:00:00" if binned else None)).lastrowid


def test_walking_up_agrees_with_the_whole_collection_for_every_folder(conn):
    cid = permissions.private_collection_id(conn, users.create_user(conn, "jane", "password123"))
    top = _folder(conn, cid, None, "Top")
    kept = _folder(conn, cid, top, "Kept")
    deep = _folder(conn, cid, kept, "Deep")
    binned = _folder(conn, cid, top, "Binned", binned=True)          # in the bin itself
    under_bin = _folder(conn, cid, binned, "Under")                   # hidden: its parent is in the bin
    way_under = _folder(conn, cid, under_bin, "WayUnder")             # hidden two levels down
    root_binned = _folder(conn, cid, None, "OldTop", binned=True)     # a top-level folder in the bin

    live = fs.live_folder_ids(conn, cid)                              # the whole-collection answer
    for fid in (top, kept, deep, binned, under_bin, way_under, root_binned):
        assert fs.is_live_folder(conn, fid) == (fid in live), fid
    assert fs.is_live_folder(conn, deep) and not fs.is_live_folder(conn, way_under)
    assert fs.is_live_folder(conn, None)                              # the collection's top: always there
    assert not fs.is_live_folder(conn, 999_999)                       # no such folder


def test_it_reads_one_row_per_level_not_the_whole_collection(conn):
    cid = permissions.private_collection_id(conn, users.create_user(conn, "mina", "password123"))
    parent = None
    for depth in range(5):                                            # a chain 5 deep...
        parent = _folder(conn, cid, parent, f"level{depth}")
    for n in range(2000):                                             # ...beside 2,000 folders it has no need to read
        _folder(conn, cid, None, f"other{n}")
    reads = []
    conn.set_trace_callback(reads.append)
    try:
        assert fs.is_live_folder(conn, parent)
    finally:
        conn.set_trace_callback(None)
    assert len(reads) == 1 and "WITH RECURSIVE up" in reads[0]        # one query, walking up; no full load


def test_folder_rows_come_from_memory_until_any_connection_changes_a_folder(settings, conn):
    """db.folder_rows: on the box every request re-read the big library's 28,789 folder rows. Now they're read
    once and again only after a change, whichever process made it (the worker's scan writes on its own connection)."""
    from mnemerynne import db

    cid = permissions.private_collection_id(conn, users.create_user(conn, "tess", "password123"))
    first = _folder(conn, cid, None, "First")
    rows = db.folder_rows(conn, cid)
    assert set(rows) == {first}
    assert db.folder_rows(conn, cid) is rows                          # unchanged: the very same rows, from memory

    other = db.connect(settings.db_path)                              # "another process": its own connection
    try:
        second = _folder(other, cid, None, "Second")                  # added
        assert set(db.folder_rows(conn, cid)) == {first, second}
        other.execute("UPDATE folders SET name = 'Renamed' WHERE id = ?", (first,))   # changed
        assert db.folder_rows(conn, cid)[first]["name"] == "Renamed"
        other.execute("DELETE FROM folders WHERE id = ?", (second,))  # removed
        assert set(db.folder_rows(conn, cid)) == {first}
    finally:
        other.close()


def test_folder_rows_never_mix_up_two_databases(tmp_path):
    from mnemerynne import db

    seen = []
    for name in ("one.db", "two.db"):                                 # same collection id, different files
        c = db.connect(tmp_path / name)
        db.migrate(c, 4)
        cid = permissions.private_collection_id(c, users.create_user(c, "zed", "password123"))
        _folder(c, cid, None, f"in-{name}")
        seen.append({r["name"] for r in db.folder_rows(c, cid).values()})
        c.close()
    assert seen == [{"in-one.db"}, {"in-two.db"}]
