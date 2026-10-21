import io
import struct

import pytest

from erinne import db, fs, permissions, retrieval, users, worker
from erinne.ingest import store
from fakes import KeywordEmbedder
from pdfgen import make_pdf


class CountingEmbedder(KeywordEmbedder):
    def __init__(self):
        super().__init__()
        self.document_calls = 0

    def embed_documents(self, texts, on_batch=None):
        self.document_calls += 1
        return super().embed_documents(texts, on_batch=on_batch)


@pytest.fixture
def embedder():
    return CountingEmbedder()


@pytest.fixture
def world(conn, settings, embedder):
    """alice: an inbox, plus write access to Oracle; bob: read access to Oracle."""
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, oracle, alice, can_write=True)
    permissions.grant_user(conn, oracle, bob)
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    return {"alice": alice, "bob": bob, "oracle": oracle, "inbox": permissions.private_collection_id(conn, alice),
            "a": fs.Access.of(conn, alice), "b": fs.Access.of(conn, bob)}


def _add(conn, settings, collection, name, text, folder=None, parts=None):
    received = store.receive(io.BytesIO(f"# {name}\n\n{text}\n".encode()), name, settings)
    return store.add_file(conn, settings, received, collection, None, folder_id=folder, folder_parts=parts)[0]


def _drain(conn, settings, embedder):
    while worker.run_one(conn, settings, embedder):
        pass


def _found(conn, settings, embedder, user, question, scope=None):
    result = retrieval.search_for_user(conn, settings, embedder, user, question, scope=scope)
    return {h.file_name for h in result.candidates}


def _item(kind, id_):
    return fs.Item(kind, id_)


# ---------------------------------------------------------------- folders
def test_folders_names_and_paths(conn, settings, world):
    a, lib = world["a"], world["oracle"]
    reports = fs.create_folder(conn, a, lib, None, "Reports")
    q1 = fs.create_folder(conn, a, lib, reports, "Q1")
    with pytest.raises(fs.Conflict):
        fs.create_folder(conn, a, lib, None, "reports")      # names are case-insensitive
    with pytest.raises(fs.FsError):
        fs.create_folder(conn, a, lib, None, "..")
    with pytest.raises(fs.Forbidden):
        fs.create_folder(conn, world["b"], lib, None, "Mine")  # bob can only read
    with pytest.raises(fs.NotFound):
        fs.create_folder(conn, world["b"], world["inbox"], None, "Peek")  # not even visible
    assert [p["name"] for p in fs.folder_path(conn, q1)] == ["Reports", "Q1"]
    assert fs.ensure_path(conn, lib, None, ["Reports", "Q1", "Week 2"], None) not in (reports, q1)
    assert fs.ensure_path(conn, lib, None, ["reports", "q1"], None) == q1

    first = _add(conn, settings, lib, "audit.md", "night audit steps", folder=q1)
    second = _add(conn, settings, lib, "audit.md", "different audit text", folder=q1)
    assert first != second
    names = sorted(r["display_name"] for r in conn.execute("SELECT display_name FROM files"))
    assert names == ["audit (2).md", "audit.md"]
    listing = fs.browse(conn, world["b"], lib, q1)
    assert [r["display_name"] for r in listing["files"]] == ["audit (2).md", "audit.md"]
    assert listing["can_write"] is False


def test_same_content_dedupes_per_folder_only(conn, settings, world):
    lib = world["oracle"]
    a = fs.create_folder(conn, world["a"], lib, None, "A")

    def add(name, folder=None):
        received = store.receive(io.BytesIO(b"# Same\n\nsame text\n"), name, settings)
        return store.add_file(conn, settings, received, lib, None, folder_id=folder)[0]

    one = add("x.md", a)
    assert add("x copy.md", a) == one
    assert add("x.md") != one       # root: a new file


def test_upload_parts_cannot_escape(conn, settings, world):
    assert fs.split_relative_path("../../etc/passwd") == ["etc"]
    assert fs.split_relative_path("Reports\\2026/./q1.pdf") == ["Reports", "2026"]
    assert fs.split_relative_path("q1.pdf") == []


# ------------------------------------------------------- moves and scope
def test_move_across_collections_moves_search_scope(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    assert "pool.md" not in _found(conn, settings, embedder, world["bob"], "pool")

    archive = fs.create_folder(conn, world["a"], world["oracle"], None, "Archive")
    [result] = fs.move(conn, world["a"], [_item("file", file_id)], world["oracle"], archive)
    assert result.ok
    assert "pool.md" in _found(conn, settings, embedder, world["bob"], "pool")
    vec = conn.execute("SELECT collection_id, folder_id FROM chunk_vec WHERE rowid IN "
                       "(SELECT id FROM chunks WHERE file_id = ?)", (file_id,)).fetchall()
    assert {tuple(r) for r in vec} == {(world["oracle"], archive)}

    # Bob can read Oracle but not change it.
    with pytest.raises(fs.Forbidden):
        fs.move(conn, world["b"], [_item("file", file_id)], world["oracle"], None)
    [refused] = fs.trash(conn, world["b"], [_item("file", file_id)])
    assert not refused.ok and refused.status == 403


def test_folder_cannot_move_into_itself_and_moves_its_subtree(conn, settings, embedder, world):
    a, lib = world["a"], world["oracle"]
    top = fs.create_folder(conn, a, lib, None, "Top")
    sub = fs.create_folder(conn, a, lib, top, "Sub")
    file_id = _add(conn, settings, lib, "parking.md", "Parking is in lot B.", folder=sub)
    _drain(conn, settings, embedder)
    [bad] = fs.move(conn, a, [_item("folder", top)], lib, sub)
    assert not bad.ok and "itself" in bad.error

    [ok] = fs.move(conn, a, [_item("folder", top)], world["inbox"], None)
    assert ok.ok
    assert conn.execute("SELECT collection_id FROM files WHERE id = ?", (file_id,)).fetchone()[0] == world["inbox"]
    assert "parking.md" not in _found(conn, settings, embedder, world["bob"], "parking")
    assert "parking.md" in _found(conn, settings, embedder, world["alice"], "parking")


def test_folder_scoped_search(conn, settings, embedder, world):
    a, lib = world["a"], world["oracle"]
    front = fs.create_folder(conn, a, lib, None, "Front desk")
    nights = fs.create_folder(conn, a, lib, front, "Nights")
    _add(conn, settings, lib, "nights.md", "Run the night audit at 2am.", folder=nights)
    _add(conn, settings, lib, "root.md", "The audit binder is in the office.")
    _add(conn, settings, world["inbox"], "mine.md", "My audit notes.")
    _drain(conn, settings, embedder)

    everything = _found(conn, settings, embedder, world["alice"], "audit")
    assert everything == {"nights.md", "root.md", "mine.md"}
    folder = fs.scope_folders(conn, a, lib, front)
    assert folder == {front, nights}
    scoped = _found(conn, settings, embedder, world["alice"], "audit",
                    {"collection_id": lib, "folders": sorted(folder)})
    assert scoped == {"nights.md"}
    library = _found(conn, settings, embedder, world["alice"], "audit", {"collection_id": lib, "folders": None})
    assert library == {"nights.md", "root.md"}
    # A scope can only narrow what the user may read.
    assert _found(conn, settings, embedder, world["bob"], "audit",
                  {"collection_id": world["inbox"], "folders": None}) == set()
    with pytest.raises(fs.NotFound):
        fs.scope_folders(conn, world["b"], world["inbox"], None)


# ------------------------------------------------------------------ copy
def test_copy_reuses_vectors_without_embedding(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "checkout.md", "Checkout is at 11am.")
    _drain(conn, settings, embedder)
    calls = embedder.document_calls
    [result] = fs.copy(conn, world["a"], [_item("file", file_id)], world["oracle"], None)
    assert result.ok
    _drain(conn, settings, embedder)
    assert embedder.document_calls == calls
    assert "checkout.md" in _found(conn, settings, embedder, world["bob"], "checkout")
    assert conn.execute("SELECT count(*) FROM files WHERE status = 'ready'").fetchone()[0] == 2

    # Copying into the same folder makes "name (2)".
    fs.copy(conn, world["a"], [_item("file", file_id)], world["inbox"], None)
    assert conn.execute("SELECT count(*) FROM files WHERE display_name = 'checkout (2).md'").fetchone()[0] == 1


def test_copy_folder_recursively(conn, settings, embedder, world):
    a, inbox = world["a"], world["inbox"]
    top = fs.create_folder(conn, a, inbox, None, "Top")
    sub = fs.create_folder(conn, a, inbox, top, "Sub")
    _add(conn, settings, inbox, "one.md", "pool one", folder=top)
    _add(conn, settings, inbox, "two.md", "pool two", folder=sub)
    [result] = fs.copy(conn, a, [_item("folder", top)], world["oracle"], None)
    assert result.ok and result.extra["name"] == "Top"
    tree = fs.folder_tree(conn, a, world["oracle"])
    assert sorted(f["name"] for f in tree) == ["Sub", "Top"]
    assert conn.execute("SELECT count(*) FROM files WHERE collection_id = ?", (world["oracle"],)).fetchone()[0] == 2


# ---------------------------------------------------------- recycle bin
def test_trash_hides_from_search_and_restore_brings_it_back(conn, settings, embedder, world):
    a, inbox = world["a"], world["inbox"]
    folder = fs.create_folder(conn, a, inbox, None, "Policies")
    file_id = _add(conn, settings, inbox, "pets.md", "Pets are welcome.", folder=folder)
    _drain(conn, settings, embedder)
    assert "pets.md" in _found(conn, settings, embedder, world["alice"], "pets")

    fs.trash(conn, a, [_item("folder", folder)])
    assert "pets.md" not in _found(conn, settings, embedder, world["alice"], "pets")
    assert fs.browse(conn, a, inbox, None)["folders"] == []
    assert fs.search_names(conn, a, "pets")["files"] == []
    listing = fs.list_trash(conn, a, inbox)
    assert [f["name"] for f in listing["folders"]] == ["Policies"] and listing["files"] == []
    with pytest.raises(fs.NotFound):
        fs.browse(conn, a, inbox, folder)

    # Meanwhile a new folder takes the name; restoring renames instead of clashing.
    fs.create_folder(conn, a, inbox, None, "Policies")
    calls = embedder.document_calls
    [result] = fs.restore(conn, a, [_item("folder", folder)])
    assert result.ok and result.extra["name"] == "Policies (2)"
    assert "pets.md" in _found(conn, settings, embedder, world["alice"], "pets")
    assert embedder.document_calls == calls  # nothing re-embedded


def test_restore_into_root_when_folder_is_gone_and_purge(conn, settings, embedder, world):
    a, inbox = world["a"], world["inbox"]
    folder = fs.create_folder(conn, a, inbox, None, "Old")
    file_id = _add(conn, settings, inbox, "old.md", "Old pool rules.", folder=folder)
    _drain(conn, settings, embedder)
    fs.trash(conn, a, [_item("file", file_id)])
    fs.trash(conn, a, [_item("folder", folder)])
    fs.purge(conn, settings, a, [_item("folder", folder)])
    assert conn.execute("SELECT count(*) FROM folders").fetchone()[0] == 0

    [result] = fs.restore(conn, a, [_item("file", file_id)])
    assert result.ok
    row = conn.execute("SELECT folder_id, deleted_at FROM files WHERE id = ?", (file_id,)).fetchone()
    assert row["folder_id"] is None and row["deleted_at"] is None

    # Purging needs the item to be in the bin, then removes the blob too.
    [refused] = fs.purge(conn, settings, a, [_item("file", file_id)])
    assert not refused.ok
    fs.trash(conn, a, [_item("file", file_id)])
    blob = conn.execute("SELECT b.rel_path FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
                        "WHERE f.id = ?", (file_id,)).fetchone()["rel_path"]
    fs.purge(conn, settings, a, [_item("file", file_id)])
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert not store.blob_path(settings, blob).exists()


def test_purge_expired_only_takes_old_items(conn, settings, world):
    a, inbox = world["a"], world["inbox"]
    old = _add(conn, settings, inbox, "old.md", "old")
    new = _add(conn, settings, inbox, "new.md", "new")
    fs.trash(conn, a, [_item("file", old), _item("file", new)])
    conn.execute("UPDATE files SET deleted_at = datetime('now', '-40 days') WHERE id = ?", (old,))
    assert fs.purge_expired(conn, settings, 30) == 1
    assert [r[0] for r in conn.execute("SELECT id FROM files")] == [new]


def test_file_trashed_while_indexing_stays_hidden(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "late.md", "Late checkout costs extra.")
    fs.trash(conn, world["a"], [_item("file", file_id)])   # before the worker gets to it
    _drain(conn, settings, embedder)
    assert "late.md" not in _found(conn, settings, embedder, world["alice"], "checkout")
    fs.restore(conn, world["a"], [_item("file", file_id)])
    assert "late.md" in _found(conn, settings, embedder, world["alice"], "checkout")


# ------------------------------------------------------------- migration
def test_v3_database_migrates_with_data_and_vectors(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    for number, path in db._migration_files()[:3]:
        conn.executescript(f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("INSERT INTO meta(key, value) VALUES ('embed_dim', '4')")
    conn.execute("CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[4] distance_metric=cosine, "
                 "collection_id integer)")
    conn.execute("CREATE TRIGGER chunks_vec_ad AFTER DELETE ON chunks BEGIN "
                 "DELETE FROM chunk_vec WHERE rowid = old.id; END")
    conn.execute("INSERT INTO users(username, pw_hash) VALUES ('alice', 'x')")
    conn.execute("INSERT INTO collections(kind, name, owner_user_id) VALUES ('private', 'inbox', 1)")
    conn.execute("INSERT INTO blobs(sha256, kind, size, rel_path) VALUES ('ab', 'pdf', 3, 'a/ab.pdf')")
    conn.execute("INSERT INTO files(collection_id, blob_sha256, display_name, status) VALUES (1, 'ab', 'g.pdf', 'ready')")
    conn.execute("INSERT INTO chunks(file_id, collection_id, ord, section_path, text) VALUES (1, 1, 0, 'S', 'pool')")
    conn.execute("INSERT INTO chunk_vec(rowid, embedding, collection_id) VALUES (1, ?, 1)",
                 (struct.pack("4f", 1, 0, 0, 0),))
    conn.execute("INSERT INTO conversations(id, user_id) VALUES ('c1', 1)")
    conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'assistant', 'hi')")
    conn.execute("INSERT INTO message_sources(message_id, n, chunk_id, file_id) VALUES (1, 1, 1, 1)")

    assert db.migrate(conn, 4) == len(db._migration_files())
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    f = conn.execute("SELECT * FROM files").fetchone()
    assert (f["display_name"], f["status"], f["folder_id"], f["deleted_at"]) == ("g.pdf", "ready", None, None)
    assert conn.execute("SELECT mime FROM blobs").fetchone()[0] == "application/pdf"
    assert conn.execute("SELECT rowid, collection_id, folder_id FROM chunk_vec").fetchall()[0][:] == (1, 1, 0)
    # References into the rebuilt table still cascade.
    conn.execute("DELETE FROM files WHERE id = 1")
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0
    assert conn.execute("SELECT file_id FROM message_sources").fetchone()[0] is None
    conn.close()


def test_pdf_and_other_files_in_one_folder(conn, settings, embedder, world):
    folder = fs.create_folder(conn, world["a"], world["inbox"], None, "Mixed")
    pdf = make_pdf([["Pool", "The pool is heated."]], [("Pool", 0, 0)])
    store.add_file(conn, settings, store.receive(io.BytesIO(pdf), "pool.pdf", settings), world["inbox"], None,
                   folder_id=folder)
    store.add_file(conn, settings, store.receive(io.BytesIO(b"PK\x03\x04"), "photos.zip", settings),
                   world["inbox"], None, folder_id=folder)
    _drain(conn, settings, embedder)
    listing = fs.browse(conn, world["a"], world["inbox"], folder)
    assert {(r["display_name"], r["status"]) for r in listing["files"]} == {("pool.pdf", "ready"),
                                                                           ("photos.zip", "stored")}
    assert fs.search_names(conn, world["a"], "PHOTO")["files"][0]["display_name"] == "photos.zip"
