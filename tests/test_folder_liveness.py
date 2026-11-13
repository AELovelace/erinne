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
