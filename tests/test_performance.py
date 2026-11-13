"""The performance pass of 2026-11-10: what keeps a share of 285,000 files from slowing everyone down."""

import os
import sqlite3
import time
from pathlib import Path

import pytest

from fakes import FakeDirectory, FakeShareChecker
from mnemerynne import accounts, db, editing, fs, permissions, share, users, worker
from mnemerynne.config import Settings


class CountingChecker(FakeShareChecker):
    """Remembers every folder it was asked about."""

    def __init__(self, root, rules=None) -> None:
        super().__init__(root, rules)
        self.asked = []

    def rights(self, path, uid, gids):
        self.asked.append(path)
        return super().rights(path, uid, gids)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "storage" / "share"
    path.mkdir(parents=True)
    (path / share.MARKER).write_text("")
    return path


@pytest.fixture
def settings(tmp_path, root):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)


@pytest.fixture
def checker(root):
    return CountingChecker(root)


@pytest.fixture
def rt(settings, checker):
    return share.configure(settings, FakeDirectory({"jsmith": {"Domain Users"}}), checker)


@pytest.fixture
def conn(settings, rt):
    connection = db.connect(settings.db_path)
    db.migrate(connection, settings.embed_dim)
    yield connection
    connection.close()


@pytest.fixture
def jane(conn, rt):
    uid = users.create_user(conn, "jane", "password123")
    accounts.link(conn, uid, "jsmith", rt.directory)
    return uid


def _write(path, text="hello"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - 60
    os.utime(path, (old, old))                       # old enough that the scan doesn't wait for it to settle
    return path


def _folder(conn, name):
    return conn.execute("SELECT id, collection_id FROM folders WHERE name = ?", (name,)).fetchone()


@pytest.fixture
def locks(monkeypatch):
    """Every write transaction taken, as they happen."""
    taken = []
    real = db.transaction

    def counted(connection):
        taken.append(1)
        return real(connection)

    monkeypatch.setattr(db, "transaction", counted)
    return taken


# ------------------------------------------------------------------ the scan
def test_a_scan_that_finds_nothing_leaves_the_write_lock_alone(conn, settings, root, locks):
    _write(root / "Docs" / "Guides" / "a.md")
    share.sync(conn, settings)
    locks.clear()
    assert not share.sync(conn, settings)
    assert len(locks) == 1                           # only the moment spent looking for new top-level folders


def test_a_scan_still_applies_every_kind_of_change(conn, settings, root):
    changed = _write(root / "Docs" / "Guides" / "a.md")
    gone = _write(root / "Docs" / "Guides" / "gone.md")
    _write(root / "Docs" / "Empty" / "x.md").unlink()
    moved_from = _write(root / "Docs" / "m.md", "will move")
    share.sync(conn, settings)

    _write(changed, "changed text")
    gone.unlink()
    (root / "Docs" / "Empty").rmdir()
    _write(root / "Docs" / "New" / "b.md")
    moved_from.rename(root / "Docs" / "New" / "m.md")
    stats = share.sync(conn, settings)

    assert (stats.changed, stats.removed, stats.added, stats.moved) == (1, 1, 1, 1)
    names = {r["display_name"] for r in conn.execute("SELECT display_name FROM files WHERE deleted_at IS NULL")}
    assert names == {"a.md", "b.md", "m.md"}
    assert _folder(conn, "Empty") is None and _folder(conn, "New") is not None


def test_a_slow_scan_cannot_run_back_to_back():
    """The wait is counted from when a scan ends, so the worker has time for everything else."""
    text = Path(worker.__file__).read_text(encoding="utf-8")
    scan = text.index("share.sync(conn, settings)")
    assert text.index("media_catalog(conn, settings)", scan) < text.index("last_scan = time.monotonic()", scan)


# ----------------------------------------------------------------- permissions
def test_lists_reuse_the_shares_permissions_for_a_while(conn, settings, root, checker, rt, jane):
    _write(root / "Docs" / "Guides" / "a.md")
    share.sync(conn, settings)
    assert permissions.reach(conn, jane)
    first = len(checker.asked)
    assert first > 0
    permissions.reach(conn, jane)
    assert len(checker.asked) == first               # the second look never touched the disk

    for entry in rt.rights.values():
        entry.at -= settings.share_rights_seconds + 1
    permissions.reach(conn, jane)                    # too old: answered from the last walk, and read again...
    _background_walks_done()
    assert len(checker.asked) == first * 2           # ...in the background


def _background_walks_done():
    """Wait for share permission walks running on their own threads (share._walk_in_background)."""
    import threading
    for thread in [t for t in threading.enumerate() if t.name == "share-rights-walk"]:
        thread.join(10)


class SlowChecker(CountingChecker):
    """A share whose permission reads take as long as the test says (a big library on a busy disk)."""

    def __init__(self, root) -> None:
        super().__init__(root)
        import threading
        self.go = threading.Event()
        self.go.set()

    def rights(self, path, uid, gids):
        self.go.wait(10)                             # holds every read while the test keeps it cleared
        return super().rights(path, uid, gids)


def test_a_due_walk_never_makes_a_request_wait(conn, settings, root, rt, jane):
    """Only the first look waits for a walk of the share. On the box (28,789 folders) every request waited for a
    whole walk each time the 30 s were up: Files loaded, just really slowly."""
    _write(root / "Docs" / "Guides" / "a.md")
    share.sync(conn, settings)
    slow = SlowChecker(root)
    rt.checker = slow
    before = permissions.reach(conn, jane)           # the first look: walks, and waits for it
    assert before and slow.asked
    for entry in rt.rights.values():
        entry.at -= settings.share_rights_seconds + 1   # the walk is due again
    slow.go.clear()                                  # from now on the share takes "forever" to answer
    asked = len(slow.asked)
    began = time.monotonic()
    again = permissions.reach(conn, jane)            # answered from the last walk, at once
    assert time.monotonic() - began < 2
    assert set(again) == set(before)
    slow.go.set()                                    # let the background walk finish
    _background_walks_done()
    assert len(slow.asked) > asked                   # it did read the share again, off the request's time


def test_a_new_folder_costs_one_look_not_a_whole_walk(conn, settings, root, checker, jane):
    _write(root / "Docs" / "Guides" / "a.md")
    share.sync(conn, settings)
    permissions.reach(conn, jane)
    _write(root / "Docs" / "Fresh" / "b.md")
    share.sync(conn, settings)
    checker.asked.clear()
    fresh = _folder(conn, "Fresh")
    assert fs.Access.of(conn, jane).can_read(fresh["collection_id"], fresh["id"])   # usable straight away
    assert [p.name for p in checker.asked] == ["Fresh"]


def test_taking_access_away_stops_opening_at_once_even_while_lists_lag(conn, settings, root, checker, jane):
    _write(root / "Docs" / "Secret" / "a.md")
    share.sync(conn, settings)
    row = fs.file_row(conn, conn.execute("SELECT id FROM files WHERE display_name = 'a.md'").fetchone()[0])
    assert fs.Access.of(conn, jane).can_read_file(row)

    checker.rules = {"Docs/Secret": (False, False)}
    assert fs.Access.of(conn, jane).can_read(row["collection_id"], row["folder_id"])       # a list may lag...
    assert not fs.Access.of(conn, jane).can_read_file(row)                                 # ...opening it may not
    assert not fs.Access.of(conn, jane).can_read(row["collection_id"], row["folder_id"])   # and the lists catch up


def test_writing_asks_the_share_right_then(conn, settings, root, checker, jane):
    _write(root / "Docs" / "Shared" / "a.md")
    share.sync(conn, settings)
    place = _folder(conn, "Shared")
    fs.Access.of(conn, jane).write(place["collection_id"], place["id"])
    checker.rules = {"Docs/Shared": (True, False)}
    with pytest.raises(fs.Forbidden):
        fs.Access.of(conn, jane).write(place["collection_id"], place["id"])


def test_a_recheck_never_widens_what_claude_may_see(conn, settings, root, jane):
    _write(root / "Docs" / "Hidden" / "a.md")
    _write(root / "Docs" / "Open" / "b.md")
    share.sync(conn, settings)
    hidden = _folder(conn, "Hidden")
    conn.execute("UPDATE collections SET claude_enabled = 1 WHERE id = ?", (hidden["collection_id"],))
    conn.execute("UPDATE folders SET claude_hidden = 1 WHERE id = ?", (hidden["id"],))
    claude = fs.Access.for_claude(conn, jane)
    claude.confirm(hidden["collection_id"], hidden["id"])
    assert not claude.can_read(hidden["collection_id"], hidden["id"])
    assert not claude.can_write(hidden["collection_id"], hidden["id"])


def test_a_listing_works_out_ai_edit_with_the_access_it_already_has(conn, settings, root, jane, monkeypatch):
    _write(root / "Docs" / "Guides" / "a.md")
    share.sync(conn, settings)
    place = _folder(conn, "Guides")
    access, ai = fs.Access.of(conn, jane), editing.ai_access(conn, jane, "erynne")
    built = []
    real = fs.Access.of
    monkeypatch.setattr(fs.Access, "of", classmethod(lambda cls, c, u: built.append(1) or real(c, u)))
    state = editing.edit_state(conn, jane, place["collection_id"], place["id"], None, 0, 0, access=access, ai=ai)
    assert built == [] and set(state) == {"on", "can_change", "shared", "why"}


# ----------------------------------------------------------------- connections
def test_connections_are_reused_unless_something_is_wrong_with_them(tmp_path):
    pool = db.Pool(tmp_path / "x.db", keep=1)
    first = pool.get()
    pool.put(first)
    assert pool.get() is first                       # the same one back

    second = pool.get()                              # none spare: never waits, just opens another
    assert second is not first
    pool.put(first)
    pool.put(second)                                 # over `keep`: closed
    with pytest.raises(sqlite3.ProgrammingError):
        second.execute("SELECT 1")

    stuck = pool.get()
    assert stuck is first
    stuck.execute("BEGIN")
    pool.put(stuck)                                  # mid-transaction: nobody else inherits that
    with pytest.raises(sqlite3.ProgrammingError):
        stuck.execute("SELECT 1")

    last = pool.get()
    pool.close()
    pool.put(last)                                   # back after shutdown: closed, not kept
    with pytest.raises(sqlite3.ProgrammingError):
        last.execute("SELECT 1")


# --------------------------------------------------------------------- indexes
@pytest.mark.parametrize("sql, index", [
    ("SELECT 1 FROM files WHERE status = 'ready' AND collection_id IN (1, 2) LIMIT 1", "files_ready"),
    ("SELECT id FROM files f WHERE f.collection_id = 1 AND COALESCE(f.folder_id, 0) = 5 AND f.deleted_at IS NULL "
     "ORDER BY f.display_name COLLATE NOCASE", "files_listing"),
    ("SELECT count(*) FROM files x WHERE x.folder_id = 5 AND x.deleted_at IS NULL", "files_folder"),
    ("SELECT count(*) FROM folders y WHERE y.parent_id = 5 AND y.deleted_at IS NULL", "folders_parent_id"),
    ("SELECT file_id FROM media_items WHERE artwork_id = 5 OR fanart_id = 5", "media_items_fanart"),
])
def test_the_slow_queries_use_their_indexes(conn, sql, index):
    plan = " ".join(r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
    assert index in plan, plan
