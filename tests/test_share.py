import io
import os
import time

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from mnemerynne import accounts, db, fs, permissions, share, users, worker
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "storage" / "share"
    path.mkdir(parents=True)
    (path / share.MARKER).write_text("")
    return path


@pytest.fixture
def settings(tmp_path, root):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only", embed_dim=4,
                    ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)


@pytest.fixture
def directory():
    return FakeDirectory({"jsmith": {"Domain Users"}, "boss": {"Domain Users", "Domain Admins"}})


@pytest.fixture
def checker(root):
    return FakeShareChecker(root)


@pytest.fixture
def rt(settings, directory, checker):
    return share.configure(settings, directory, checker)


@pytest.fixture
def conn(settings, rt):
    connection = db.connect(settings.db_path)
    db.migrate(connection, settings.embed_dim)
    yield connection
    connection.close()


def _write(path, text="hello", age=60):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _files(conn):
    return {r["display_name"]: r for r in conn.execute("SELECT * FROM files")}


def _ingest(conn, settings):
    while worker.run_one(conn, settings, FakeEmbedder()):
        pass


# ------------------------------------------------------------------ names, ACLs
@pytest.mark.parametrize("bad", ["a:b", "what?", "trailing.", "trailing ", "CON", "lpt1.txt", "x|y"])
def test_windows_unfriendly_names_are_refused(bad):
    with pytest.raises(ValueError):
        share.check_name(bad)


def test_acl_round_trip_and_evaluation():
    entries = [(0x01, 7, 0), (0x02, 5, 1000), (0x04, 0, 0), (0x08, 7, 5000), (0x10, 5, 0), (0x20, 0, 0)]
    acl = share.parse_acl(share.build_acl(entries))
    assert acl == entries
    rwx, rx, w = 7, 5, 2
    assert share.allowed(0o700, 1, 1, acl, 1, frozenset(), rwx)                 # owner
    assert share.allowed(0o700, 1, 1, acl, 1000, frozenset(), rx)              # named user
    assert not share.allowed(0o700, 1, 1, acl, 1000, frozenset(), w)           # ...limited by mask too
    assert share.allowed(0o700, 1, 1, acl, 2000, frozenset({5000}), rx)        # named group (mask r-x)
    assert not share.allowed(0o700, 1, 1, acl, 2000, frozenset({5000}), w)
    assert not share.allowed(0o700, 1, 1, acl, 2000, frozenset({9}), rx)       # other: nothing
    # No ACL: plain mode bits.
    assert share.allowed(0o755, 1, 1, None, 2, frozenset(), rx)
    assert not share.allowed(0o755, 1, 1, None, 2, frozenset(), w)


# ------------------------------------------------------------------ scanning
def test_sync_turns_the_share_into_libraries_and_files(conn, settings, root):
    _write(root / "Brand Standards" / "Logos" / "guide.md", "# Logo\n\nUse the pink one.")
    _write(root / "Brand Standards" / "~$guide.docx")         # Office lock file: ignored
    _write(root / "Brand Standards" / "Thumbs.db")
    _write(root / "Oracle" / "audit.txt", "Run the night audit at 2am.")
    (root / "Empty").mkdir()

    stats = share.sync(conn, settings)
    assert stats.libraries == 3 and stats.added == 2 and stats.folders_added == 1
    libs = {r["name"]: r for r in conn.execute("SELECT * FROM collections WHERE kind = 'library'")}
    assert set(libs) == {"Brand Standards", "Oracle", "Empty"}
    assert libs["Oracle"]["share_path"] == "Oracle"
    guide = _files(conn)["guide.md"]
    assert guide["status"] == "queued" and guide["folder_id"] is not None and guide["disk_inode"]

    _ingest(conn, settings)
    assert _files(conn)["guide.md"]["status"] == "ready"
    assert not share.sync(conn, settings)                     # nothing changed: nothing to do


def test_a_wiki_folder_on_the_share_is_the_organisation_wiki(conn, settings, root):
    from mnemerynne import wiki

    assert wiki.library_row(conn) is None
    _write(root / "Wiki" / "index.md", "# Home\n\nWelcome.")
    share.sync(conn, settings)
    found = wiki.library_row(conn)
    assert found is not None and found["name"] == "Wiki" and found["share_path"] == "Wiki"


def test_renames_and_moves_on_the_share_keep_the_index(conn, settings, root):
    _write(root / "Oracle" / "audit.txt", "Run the night audit at 2am.")
    (root / "Archive").mkdir()
    share.sync(conn, settings)
    _ingest(conn, settings)
    before = _files(conn)["audit.txt"]
    chunks = conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (before["id"],)).fetchone()[0]
    assert chunks

    os.rename(root / "Oracle" / "audit.txt", root / "Archive" / "Night audit.txt")
    stats = share.sync(conn, settings)
    assert stats.moved == 1 and stats.removed == 0 and stats.added == 0
    after = _files(conn)["Night audit.txt"]
    archive = conn.execute("SELECT id FROM collections WHERE name = 'Archive'").fetchone()["id"]
    assert after["id"] == before["id"] and after["collection_id"] == archive
    assert conn.execute("SELECT count(*) FROM chunks WHERE file_id = ? AND collection_id = ?",
                        (after["id"], archive)).fetchone()[0] == chunks   # same chunks, new scope


def test_edits_and_deletions_on_the_share(conn, settings, root):
    path = _write(root / "Oracle" / "Sub" / "audit.txt", "Version one.")
    share.sync(conn, settings)
    _ingest(conn, settings)
    first = _files(conn)["audit.txt"]

    _write(path, "Version two, longer.", age=30)
    assert share.sync(conn, settings).changed == 1
    edited = _files(conn)["audit.txt"]
    assert edited["id"] == first["id"] and edited["blob_sha256"] != first["blob_sha256"]
    assert edited["status"] == "queued"
    assert conn.execute("SELECT count(*) FROM blobs WHERE sha256 = ?", (first["blob_sha256"],)).fetchone()[0] == 0

    path.unlink()
    (root / "Oracle" / "Sub").rmdir()
    stats = share.sync(conn, settings)
    assert stats.removed == 1 and stats.folders_removed == 1
    assert _files(conn) == {}


def test_names_that_cant_be_kept_are_reported_not_dropped_silently(conn, settings, root):
    _write(root / "Oracle" / "Sundry  Marketplace Policy.docx")         # two spaces
    _write(root / "Oracle" / "First Aid Policy 6.4.2026..docx")          # double dot is fine
    stats = share.sync(conn, settings)
    assert stats.added == 1 and set(_files(conn)) == {"First Aid Policy 6.4.2026..docx"}
    assert stats.skipped == ["Sundry  Marketplace Policy.docx: the name has two spaces in a row (or a tab); "
                             "rename it"]


def test_files_still_being_written_wait(conn, settings, root):
    settings.share_settle_seconds = 3600
    _write(root / "Oracle" / "big.txt", "copying…", age=0)
    assert share.sync(conn, settings).added == 0
    settings.share_settle_seconds = 0
    assert share.sync(conn, settings).added == 1


def test_files_dated_in_the_future_are_not_left_waiting(conn, settings, root):
    # Copied with timestamps from a machine whose clock is hours ahead: finished files, not ones being written.
    settings.share_settle_seconds = 3600
    _write(root / "Oracle" / "memo.docx.txt", "policy", age=-6 * 3600)
    assert share.sync(conn, settings).added == 1


def test_nothing_happens_when_the_share_is_not_mounted(conn, settings, root):
    _write(root / "Oracle" / "audit.txt")
    share.sync(conn, settings)
    (root / share.MARKER).unlink()
    for p in sorted(root.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    stats = share.sync(conn, settings)
    assert not stats and stats.skipped and len(_files(conn)) == 1   # nothing mirrored as deleted


def test_library_renamed_on_the_share_keeps_its_files(conn, settings, root):
    _write(root / "Oracle" / "audit.txt")
    share.sync(conn, settings)
    lib = conn.execute("SELECT id FROM collections WHERE name = 'Oracle'").fetchone()["id"]
    os.rename(root / "Oracle", root / "Oracle Manuals")
    share.sync(conn, settings)
    row = conn.execute("SELECT * FROM collections WHERE id = ?", (lib,)).fetchone()
    assert row["name"] == "Oracle Manuals" and row["share_path"] == "Oracle Manuals"
    assert _files(conn)["audit.txt"]["collection_id"] == lib


# ------------------------------------------------------------------ access
def test_access_follows_share_permissions(conn, settings, root, checker, directory):
    _write(root / "Oracle" / "Manuals" / "audit.txt")
    _write(root / "Oracle" / "HR" / "salaries.txt")
    (root / "Secret").mkdir()
    share.sync(conn, settings)
    jane = users.create_user(conn, "jane", "password123")
    unlinked = users.create_user(conn, "bob", "password123")
    accounts.link(conn, jane, "jsmith", directory)
    checker.rules = {"Oracle": (True, False), "Oracle/HR": (False, False), "Secret": (False, False)}

    reach = permissions.reach(conn, jane)
    oracle = conn.execute("SELECT id FROM collections WHERE name = 'Oracle'").fetchone()["id"]
    hr = conn.execute("SELECT id FROM folders WHERE name = 'HR'").fetchone()["id"]
    manuals = conn.execute("SELECT id FROM folders WHERE name = 'Manuals'").fetchone()["id"]
    libraries = {cid for cid, r in reach.items() if r.kind == "library"}
    assert libraries == {oracle}                                   # Secret isn't readable
    r = reach[oracle]
    assert r.root_read and not r.root_write
    assert manuals in r.read and hr not in r.read and not r.write

    assert {r.kind for r in permissions.reach(conn, unlinked).values()} == {"private"}   # no domain account

    with pytest.raises(ValueError, match="set on the share"):
        permissions.grant_user(conn, oracle, unlinked)


# ------------------------------------------------------------------ file manager
@pytest.fixture
def client(settings, conn, directory, checker):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=checker))) as c:
        yield c


def _login(client, conn, directory, name="jane", account="jsmith"):
    uid = users.create_user(conn, name, "password123")
    accounts.link(conn, uid, account, directory)
    assert client.post("/login", data={"username": name, "password": "password123"},
                       follow_redirects=False).status_code == 303
    return uid


def test_new_users_get_their_inbox_on_the_share(client, conn, directory, root):
    uid = _login(client, conn, directory)
    inbox = conn.execute("SELECT * FROM collections WHERE owner_user_id = ?", (uid,)).fetchone()
    assert inbox["share_path"] == "Users/jane" and (root / "Users" / "jane").is_dir()
    r = client.post("/api/files", files={"file": ("notes.txt", io.BytesIO(b"My notes."), "text/plain")})
    assert r.status_code == 201
    assert (root / "Users" / "jane" / "notes.txt").read_bytes() == b"My notes."
    assert client.get(f"/files/{r.json()['id']}/raw").content == b"My notes."
    # The share scan doesn't take the Users folder for a library.
    share.sync(conn)
    assert conn.execute("SELECT count(*) FROM collections WHERE kind = 'library'").fetchone()[0] == 0


def test_file_manager_changes_the_share(client, conn, directory, root, settings):
    (root / "Oracle").mkdir()
    share.sync(conn, settings)
    oracle = conn.execute("SELECT id FROM collections WHERE name = 'Oracle'").fetchone()["id"]
    _login(client, conn, directory)

    folder = client.post("/api/folders", json={"collection_id": oracle, "name": "Guides"}).json()["id"]
    assert (root / "Oracle" / "Guides").is_dir()
    assert client.post("/api/folders", json={"collection_id": oracle, "name": "what?"}).status_code == 400

    up = client.post("/api/files", data={"collection_id": oracle, "folder_id": folder},
                     files={"file": ("audit.txt", io.BytesIO(b"Night audit."), "text/plain")}).json()
    assert (root / "Oracle" / "Guides" / "audit.txt").read_bytes() == b"Night audit."

    assert client.patch(f"/api/files/{up['id']}", json={"name": "Audit.txt"}).status_code == 200
    assert (root / "Oracle" / "Guides" / "Audit.txt").is_file()
    client.patch(f"/api/folders/{folder}", json={"name": "How-to"})
    assert (root / "Oracle" / "How-to" / "Audit.txt").is_file() and not (root / "Oracle" / "Guides").exists()

    item = [{"type": "file", "id": up["id"]}]
    client.post("/api/items/copy", json={"items": item, "target": {"collection_id": oracle}})
    assert (root / "Oracle" / "Audit.txt").read_bytes() == b"Night audit."
    client.post("/api/items/move", json={"items": [{"type": "folder", "id": folder}],
                                         "target": {"collection_id": oracle, "folder_id": None}})

    trash = settings.share_trash_dir
    assert client.post("/api/items/trash", json={"items": item}).json()["ok"]
    assert not (root / "Oracle" / "How-to" / "Audit.txt").exists()
    assert (trash / f"f{up['id']}-Audit.txt").is_file()
    assert not share.sync(conn, settings).removed                 # binned, not gone
    assert client.post("/api/items/restore", json={"items": item}).json()["ok"]
    assert (root / "Oracle" / "How-to" / "Audit.txt").is_file()

    folder_item = [{"type": "folder", "id": folder}]
    client.post("/api/items/trash", json={"items": folder_item})
    assert (trash / f"d{folder}-How-to" / "Audit.txt").is_file()
    assert client.post("/api/items/purge", json={"items": folder_item}).json()["ok"]
    assert not (trash / f"d{folder}-How-to").exists()
    assert set(_files(conn)) == {"Audit.txt"}                     # the copy at the top level
    assert not share.sync(conn, settings)


def test_admin_library_lifecycle_on_the_share(client, conn, directory, root, settings):
    _login(client, conn, directory, "boss", "boss")
    made = client.post("/api/admin/libraries", json={"name": "Front Desk"}).json()
    assert (root / "Front Desk").is_dir()
    libs = client.get("/api/admin/libraries").json()["libraries"]
    assert libs[0]["share_path"] == "Front Desk" and libs[0]["grants"] == []
    assert client.put(f"/api/admin/libraries/{made['id']}/grants",
                      json={"type": "user", "id": 1, "access": "read"}).status_code == 422
    assert client.post("/api/admin/libraries", json={"name": "Users"}).status_code == 422

    client.patch(f"/api/admin/libraries/{made['id']}", json={"name": "Reception"})
    assert (root / "Reception").is_dir() and not (root / "Front Desk").exists()
    client.delete(f"/api/admin/libraries/{made['id']}")
    assert not (root / "Reception").exists()
    assert (settings.share_trash_dir / f"lib{made['id']}-Reception").is_dir()


def test_share_init_prepares_the_share_and_empty_inboxes(tmp_path, monkeypatch, capsys):
    from mnemerynne.cli import main

    root = tmp_path / "storage" / "share"
    root.mkdir(parents=True)
    plain = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4)
    conn = db.connect(plain.db_path)
    db.migrate(conn, plain.embed_dim)
    share.configure(plain, None)
    empty = users.create_user(conn, "alice", "password123")          # made before the share existed
    full = users.create_user(conn, "bob", "password123")
    inbox = permissions.private_collection_id(conn, full)
    conn.execute("INSERT INTO blobs(sha256, kind, size, rel_path) VALUES ('x', 'txt', 1, 'x')")
    conn.execute("INSERT INTO files(collection_id, blob_sha256, display_name) VALUES (?, 'x', 'a.txt')", (inbox,))

    with_share = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, share_root=root)
    monkeypatch.setattr("mnemerynne.cli.get_settings", lambda: with_share)
    main(["share-init"])
    out = capsys.readouterr().out
    assert (root / share.MARKER).is_file() and (root / "Users" / "alice").is_dir()
    assert (tmp_path / "storage" / ".mnemerynne-trash").is_dir()
    assert "bob's inbox (1 files)" in out and not (root / "Users" / "bob").exists()
    assert conn.execute("SELECT share_path FROM collections WHERE owner_user_id = ?",
                        (empty,)).fetchone()["share_path"] == "Users/alice"


def test_nothing_is_created_under_an_unmounted_share(client, conn, directory, root):
    (root / share.MARKER).unlink()
    uid = users.create_user(conn, "carol", "password123")
    assert not (root / "Users").exists()
    assert conn.execute("SELECT share_path FROM collections WHERE owner_user_id = ?", (uid,)).fetchone()[0] is None
    root.joinpath(share.MARKER).touch()
    _login(client, conn, directory, "boss", "boss")
    root.joinpath(share.MARKER).unlink()
    assert client.post("/api/admin/libraries", json={"name": "Front Desk"}).status_code == 503
    assert not (root / "Front Desk").exists()


# ------------------------------------------------------------------ review fixes
def test_rename_never_replaces_another_file(root):
    a, b = _write(root / "Lib" / "a.txt", "keep me"), _write(root / "Lib" / "b.txt", "other")
    with pytest.raises(share.ShareError):
        share.rename(b, a)
    with pytest.raises(share.ShareError):
        share.copy_file(b, a)
    assert a.read_text() == "keep me" and b.read_text() == "other"


def test_nothing_is_written_while_the_share_is_unmounted(client, conn, directory, root, settings):
    (root / "Oracle").mkdir()
    share.sync(conn, settings)
    oracle = conn.execute("SELECT id FROM collections WHERE name = 'Oracle'").fetchone()["id"]
    _login(client, conn, directory)
    (root / share.MARKER).unlink()
    r = client.post("/api/files", data={"collection_id": oracle},
                    files={"file": ("audit.txt", io.BytesIO(b"x"), "text/plain")})
    assert r.status_code == 503 and not (root / "Oracle" / "audit.txt").exists()
    assert client.post("/api/folders", json={"collection_id": oracle, "name": "New"}).status_code == 503
    assert not list(settings.tmp_dir.glob("*"))           # the upload's temp file is gone too


def test_binned_folders_keep_their_own_rights(conn, settings, root, checker, directory):
    _write(root / "Lib" / "Secret" / "x.txt")
    share.sync(conn, settings)
    jane = users.create_user(conn, "jane", "password123")
    accounts.link(conn, jane, "jsmith", directory)
    secret = conn.execute("SELECT id FROM folders WHERE name = 'Secret'").fetchone()["id"]
    checker.rules = {"Lib/Secret": (False, False), f"d{secret}-Secret": (False, False)}
    root_user = users.create_user(conn, "root", "password123", role="admin")
    lib = conn.execute("SELECT id FROM collections WHERE name = 'Lib'").fetchone()["id"]
    fs.trash(conn, fs.Access(root_user, {lib: permissions.Reach(lib, "library", "Lib", True, True, False,
                                                                {secret}, {secret}, set(), {secret: None})}),
             [fs.Item("folder", secret)])
    reach = permissions.reach(conn, jane)[lib]
    assert secret not in reach.read and secret not in reach.write   # not the library's rights


def test_a_move_with_new_content_is_new_content(conn, settings, root):
    path = _write(root / "Lib" / "a.txt", "one")
    (root / "Lib" / "Sub").mkdir()
    share.sync(conn, settings)
    first = _files(conn)["a.txt"]
    os.rename(path, root / "Lib" / "Sub" / "b.txt")
    _write(root / "Lib" / "Sub" / "b.txt", "two, edited", age=30)
    share.sync(conn, settings)
    b = _files(conn)["b.txt"]
    assert b["blob_sha256"] != first["blob_sha256"] and "a.txt" not in _files(conn)


def test_names_the_file_manager_would_change_are_left_alone(conn, settings, root):
    _write(root / "Lib" / "Q1  notes" / "x.txt")
    stats = share.sync(conn, settings)
    assert any("Q1  notes" in s for s in stats.skipped)
    assert not (root / "Lib" / "Q1 notes").exists()           # no look-alike folder created
    assert not share.sync(conn, settings).folders_added       # and no churn


def test_inbox_folders_with_someone_elses_files_are_not_adopted(conn, settings, root, rt):
    _write(root / "Users" / "mallory" / "planted.txt")
    share.configure(Settings(_env_file=None), None)
    uid = users.create_user(conn, "mallory", "password123")    # made while the share was off
    share.configure(settings, rt.directory, rt.checker)
    stats = share.sync(conn, settings)
    assert any("not empty" in s for s in stats.skipped)
    assert conn.execute("SELECT share_path FROM collections WHERE owner_user_id = ?", (uid,)).fetchone()[0] is None
    # A new user with a leftover folder gets a fresh one; the old files go to the trash.
    _write(root / "Users" / "trudy" / "old.txt")
    users.create_user(conn, "trudy", "password123")
    assert list((root / "Users" / "trudy").iterdir()) == []
    assert any(p.name.startswith("inbox") for p in settings.share_trash_dir.iterdir())


# ------------------------------------------------------------------ export
def _classic_world(tmp_path, root):
    """A library and an inbox stored inside Mnemerynne, made before the share."""
    plain = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4)
    conn = db.connect(plain.db_path)
    db.migrate(conn, plain.embed_dim)
    share.configure(plain, None)
    edith = users.create_user(conn, "edith", "password123")
    lib = permissions.create_library(conn, "Oracle")
    root_access = fs.Access.of(conn, edith)
    permissions.grant_user(conn, lib, edith, can_write=True)
    root_access = fs.Access.of(conn, edith)
    guides = fs.create_folder(conn, root_access, lib, None, "Guides")
    odd = fs.create_folder(conn, root_access, lib, guides, "Q1: plans")
    src = tmp_path / "src"
    for name, text in [("audit.txt", "Night audit."), ("binned.txt", "Old."), ("notes.md", "# Notes")]:
        _write(src / name, text)
    from mnemerynne.ingest import store
    audit, _ = store.add_path(conn, plain, src / "audit.txt", lib, edith, folder_parts=["Guides", "Q1: plans"])
    binned, _ = store.add_path(conn, plain, src / "binned.txt", lib, edith)
    notes, _ = store.add_path(conn, plain, src / "notes.md", permissions.private_collection_id(conn, edith), edith)
    fs.trash(conn, fs.Access.of(conn, edith), [fs.Item("file", binned)])
    return conn, plain, {"edith": edith, "lib": lib, "audit": audit, "binned": binned, "notes": notes, "odd": odd}


def test_export_moves_libraries_and_inboxes_onto_the_share(tmp_path, root, directory, checker):
    conn, plain, ids = _classic_world(tmp_path, root)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, share_root=root,
                        share_settle_seconds=0)
    share.configure(settings, directory, checker)
    settings.share_trash_dir.mkdir()

    dry = share.export(conn, ids["lib"], dry_run=True)
    assert dry.files == 2 and dry.renamed and not (root / "Oracle").exists()

    result = share.export(conn, ids["lib"])
    assert result.done and result.renamed == ["folder “Q1: plans” → “Q1_ plans”"]
    assert (root / "Oracle" / "Guides" / "Q1_ plans" / "audit.txt").read_text() == "Night audit."
    assert (settings.share_trash_dir / f"f{ids['binned']}-binned.txt").read_text() == "Old."
    lib = conn.execute("SELECT * FROM collections WHERE id = ?", (ids["lib"],)).fetchone()
    assert lib["share_path"] == "Oracle"
    from mnemerynne.ingest import store
    assert store.content_path(conn, settings, ids["audit"]) == root / "Oracle" / "Guides" / "Q1_ plans" / "audit.txt"
    blob = conn.execute("SELECT b.rel_path FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                        (ids["audit"],)).fetchone()["rel_path"]
    assert not store.blob_path(settings, blob).exists()            # the internal copy is gone

    inbox = share.export(conn, permissions.private_collection_id(conn, ids["edith"]))
    assert inbox.done and (root / "Users" / "edith" / "notes.md").read_text() == "# Notes"

    # The scan finds everything in place: nothing added, moved or removed.
    assert not share.sync(conn, settings)
    # The bin still works after the export.
    fs.restore(conn, fs.Access(ids["edith"], {ids["lib"]: permissions.Reach(
        ids["lib"], "library", "Oracle", True, True)}), [fs.Item("file", ids["binned"])])
    assert (root / "Oracle" / "binned.txt").read_text() == "Old."


def test_export_refuses_a_folder_that_already_has_files(tmp_path, root, directory, checker):
    conn, plain, ids = _classic_world(tmp_path, root)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, share_root=root)
    share.configure(settings, directory, checker)
    settings.share_trash_dir.mkdir()
    _write(root / "Oracle" / "someone else's.txt")
    result = share.export(conn, ids["lib"])
    assert result.problem and "isn't empty" in result.problem and not result.done
    assert conn.execute("SELECT share_path FROM collections WHERE id = ?", (ids["lib"],)).fetchone()[0] is None


def test_export_undoes_itself_if_the_collection_changes_meanwhile(tmp_path, root, directory, checker, monkeypatch):
    conn, plain, ids = _classic_world(tmp_path, root)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, share_root=root)
    share.configure(settings, directory, checker)
    settings.share_trash_dir.mkdir()
    real_copy = share.copy_file

    def copy_then_someone_renames(src, dest):
        real_copy(src, dest)
        conn.execute("UPDATE files SET display_name = 'renamed.txt' WHERE id = ?", (ids["audit"],))

    monkeypatch.setattr(share, "copy_file", copy_then_someone_renames)
    with pytest.raises(share.ShareError, match="run the export again"):
        share.export(conn, ids["lib"])
    assert not (root / "Oracle").exists()
    assert not list(settings.share_trash_dir.iterdir())
    assert conn.execute("SELECT share_path FROM collections WHERE id = ?", (ids["lib"],)).fetchone()[0] is None


def test_share_export_cli(tmp_path, root, monkeypatch, capsys):
    from mnemerynne.cli import main

    conn, plain, ids = _classic_world(tmp_path, root)
    (root / share.MARKER).touch()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, share_root=root)
    settings.share_trash_dir.mkdir()
    monkeypatch.setattr("mnemerynne.cli.get_settings", lambda: settings)
    main(["share-export", "--all", "--dry-run"])
    out = capsys.readouterr().out
    assert "edith's inbox: would move 1 file(s)" in out and "library Oracle: would move 2 file(s)" in out
    assert not (root / "Oracle").exists()
    main(["share-export", "--user", "edith"])
    assert "edith's inbox: moved 1 file(s)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Not found, or already on the share: edith"):
        main(["share-export", "--user", "edith"])


def test_copies_take_only_the_bytes_and_modified_time(tmp_path):
    src = _write(tmp_path / "a" / "x.txt", "content", age=3600)
    os.chmod(src, 0o400)
    dest = tmp_path / "b" / "x.txt"
    share.copy_bytes(src, dest)
    assert dest.read_text() == "content"
    assert dest.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert os.access(dest, os.W_OK)          # the source's read-only mode didn't come along
    with pytest.raises(FileExistsError):
        share.copy_bytes(src, dest)


def test_inbox_permissions_keep_the_server_in(tmp_path, monkeypatch):
    written = {}
    monkeypatch.setattr(share.os, "getuid", lambda: 990, raising=False)
    monkeypatch.setattr(share.os, "setxattr", lambda path, name, raw: written.__setitem__(name, raw), raising=False)
    share.make_private(tmp_path, 2000123)
    acl = share.parse_acl(written["system.posix_acl_default"])
    assert (0x02, 7, 990) in acl and (0x02, 7, 2000123) in acl
    assert acl == sorted(acl, key=lambda e: (e[0], e[2]))
    # Files the owner adds over SMB are theirs, yet the server can still read them.
    assert share.allowed(0o660, 2000123, 1, acl, 990, frozenset(), 4)
