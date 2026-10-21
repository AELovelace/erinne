import io

import httpx
import pytest

from mnemerynne import jobs, permissions, users, worker
from mnemerynne.ingest import ocr, pdf, store
from fakes import FakeEmbedder
from pdfgen import make_pdf

MANUAL = make_pdf(
    [["Getting Started", "Welcome to OPERA.", "Prerequisites", "You need an account."],
     ["Night Audit", "Run the night audit after midnight to roll the business date."]],
    [("Getting Started", 0, 0), ("Prerequisites", 0, 1), ("Night Audit", 1, 0)],
)
NOTES_MD = b"# Checkout\n\nCheckout time is 11am.\n\n## Late checkout\n\nCall the front desk.\n"


@pytest.fixture
def alice(conn):
    user_id = users.create_user(conn, "alice", "password123")
    return {"id": user_id, "inbox": permissions.private_collection_id(conn, user_id)}


@pytest.fixture
def embedder():
    return FakeEmbedder()


def _add(conn, settings, data: bytes, name: str, collection_id: int, user_id=None):
    received = store.receive(io.BytesIO(data), name, settings)
    return store.add_file(conn, settings, received, collection_id, user_id)


def _file(conn, file_id):
    return conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()


def _drain(conn, settings, embedder):
    while worker.run_one(conn, settings, embedder):
        pass


def test_pdf_ingested_with_pages_sections_vectors_and_fts(conn, settings, embedder, alice):
    file_id, created = _add(conn, settings, MANUAL, "OPERA Manual.pdf", alice["inbox"], alice["id"])
    assert created and _file(conn, file_id)["status"] == "queued"
    _drain(conn, settings, embedder)

    f = _file(conn, file_id)
    assert f["status"] == "ready" and f["progress"] is None and f["error"] is None
    chunks = conn.execute("SELECT * FROM chunks WHERE file_id = ? ORDER BY ord", (file_id,)).fetchall()
    assert [(c["section_path"], c["page_start"]) for c in chunks] == [
        ("Getting Started", 1), ("Getting Started > Prerequisites", 1), ("Night Audit", 2)]
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == len(chunks)
    hit = conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'midnight'").fetchone()
    assert hit[0] == chunks[2]["id"]
    assert embedder.texts[2].startswith("OPERA Manual > Night Audit\n")
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "done"


def test_markdown_chunks_have_anchors(conn, settings, embedder, alice):
    file_id, _ = _add(conn, settings, NOTES_MD, "notes.md", alice["inbox"])
    _drain(conn, settings, embedder)
    anchors = [r[0] for r in conn.execute("SELECT anchor FROM chunks WHERE file_id = ? ORDER BY ord", (file_id,))]
    assert anchors == ["checkout", "late-checkout"]


def test_duplicate_upload_to_same_collection_returns_existing(conn, settings, alice):
    first, _ = _add(conn, settings, MANUAL, "a.pdf", alice["inbox"])
    second, created = _add(conn, settings, MANUAL, "renamed.pdf", alice["inbox"])
    assert (second, created) == (first, False)
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_identical_file_in_another_collection_reuses_vectors(conn, settings, embedder, alice):
    lib = permissions.create_library(conn, "Oracle")
    _add(conn, settings, MANUAL, "manual.pdf", lib)
    _drain(conn, settings, embedder)
    calls = embedder.calls
    file_id, _ = _add(conn, settings, MANUAL, "my copy.pdf", alice["inbox"])
    _drain(conn, settings, embedder)
    assert embedder.calls == calls  # nothing re-embedded
    assert _file(conn, file_id)["status"] == "ready"
    assert conn.execute("SELECT count(*) FROM chunk_vec WHERE collection_id = ?", (alice["inbox"],)).fetchone()[0] == 3


def test_scanned_pages_without_ocr_get_a_note(conn, settings, embedder, alice, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda command: False)
    data = make_pdf([["Readable page with enough text to count."], None])
    file_id, _ = _add(conn, settings, data, "mixed.pdf", alice["inbox"])
    _drain(conn, settings, embedder)
    f = _file(conn, file_id)
    assert f["status"] == "ready"
    assert "1 page(s) look scanned" in f["notes"] and "ocrmypdf" in f["notes"]


def test_fully_scanned_pdf_without_ocr_fails_clearly(conn, settings, embedder, alice, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda command: False)
    file_id, _ = _add(conn, settings, make_pdf([None, None]), "scan.pdf", alice["inbox"])
    _drain(conn, settings, embedder)
    f = _file(conn, file_id)
    assert f["status"] == "failed" and "look scanned" in f["error"]


def test_ocr_fills_scanned_pages(conn, settings, embedder, alice, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda command: True)

    def fake_ocr(command, source, dest, timeout):
        dest.write_bytes(make_pdf([["Readable page with enough text to count."],
                                   ["Oregon lodging tax letter recovered by OCR."]]))

    monkeypatch.setattr(ocr, "run", fake_ocr)
    file_id, _ = _add(conn, settings, make_pdf([["Readable page with enough text to count."], None]),
                      "letter.pdf", alice["inbox"])
    _drain(conn, settings, embedder)
    assert _file(conn, file_id)["status"] == "ready" and _file(conn, file_id)["notes"] is None
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'lodging'").fetchone()[0] == 1
    assert conn.execute("SELECT ocr FROM blob_units WHERE page_no = 2").fetchone()[0] == 1
    assert conn.execute("SELECT ocr_rel_path FROM blobs").fetchone()[0].endswith(".ocr.pdf")


def test_embedder_outage_retries_with_backoff_then_fails(conn, settings, embedder, alice):
    embedder.fail_with = httpx.ConnectError("connection refused")
    file_id, _ = _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])
    assert worker.run_one(conn, settings, embedder)
    f = _file(conn, file_id)
    assert f["status"] == "queued" and "Retrying" in f["progress"]
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["status"] == "queued" and job["run_after"] > job["created_at"]
    assert not worker.run_one(conn, settings, embedder)  # not due yet

    for _ in range(settings.job_max_attempts - 1):
        conn.execute("UPDATE jobs SET run_after = datetime('now', '-1 second')")
        worker.run_one(conn, settings, embedder)
    f = _file(conn, file_id)
    assert f["status"] == "failed" and "ConnectError" in f["error"]
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "failed"


def test_bad_file_fails_without_retrying(conn, settings, embedder, alice, monkeypatch):
    file_id, _ = _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])

    def broken(path):
        raise pdf.PdfError("The PDF could not be opened (damaged).")

    monkeypatch.setattr(pdf, "read_pdf", broken)
    worker.run_one(conn, settings, embedder)
    assert _file(conn, file_id)["status"] == "failed"
    assert conn.execute("SELECT status, attempts FROM jobs").fetchone()[:] == ("failed", 1)


def test_reindex_uses_stored_text_without_re_reading(conn, settings, embedder, alice, monkeypatch):
    file_id, _ = _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])
    _drain(conn, settings, embedder)
    before = [r[0] for r in conn.execute("SELECT text FROM chunks ORDER BY ord")]
    monkeypatch.setattr(pdf, "read_pdf", lambda path: pytest.fail("should not re-extract"))
    jobs.enqueue(conn, "ingest", file_id)
    _drain(conn, settings, embedder)
    assert [r[0] for r in conn.execute("SELECT text FROM chunks ORDER BY ord")] == before
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == len(before)


def test_delete_removes_chunks_and_unshared_blob(conn, settings, embedder, alice):
    lib = permissions.create_library(conn, "Oracle")
    mine, _ = _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])
    shared, _ = _add(conn, settings, MANUAL, "manual.pdf", lib)
    _drain(conn, settings, embedder)
    blob = settings.blob_dir / conn.execute("SELECT rel_path FROM blobs").fetchone()[0]

    store.delete_file(conn, settings, mine)
    assert blob.exists()  # still used by the library copy
    assert conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (mine,)).fetchone()[0] == 0

    store.delete_file(conn, settings, shared)
    assert not blob.exists()
    for table in ("chunks", "chunk_vec", "blobs", "blob_units", "jobs"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'audit'").fetchone()[0] == 0


def test_file_deleted_while_queued_is_skipped(conn, settings, embedder, alice):
    file_id, _ = _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])
    job = jobs.claim(conn)
    store.delete_file(conn, settings, file_id)  # job row cascades away too
    from mnemerynne.ingest import pipeline
    pipeline.process_file(conn, settings, embedder, job["file_id"])  # no exception
    assert embedder.calls == 0


def test_stale_running_jobs_are_requeued(conn, settings, alice):
    _add(conn, settings, MANUAL, "manual.pdf", alice["inbox"])
    jobs.claim(conn)
    conn.execute("UPDATE jobs SET locked_at = datetime('now', '-2 hours')")
    assert jobs.recover_stale(conn, 30) == 1
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "queued"


@pytest.mark.parametrize("data, name, kind, note", [
    (b"MZ\x90\x00 not a pdf", "invoice.pdf", "other", None),
    (b"plain text", "notes.txt", "txt", None),
    ("café".encode("latin-1"), "notes.md", "other", "UTF-8"),
    (b"<svg/>", "logo.svg", "other", None),
])
def test_any_file_is_received(settings, data, name, kind, note):
    received = store.receive(io.BytesIO(data), name, settings)
    assert received.kind == kind
    assert (note in received.note) if note else received.note is None
    received.tmp_path.unlink()


def test_rejected_uploads_leave_nothing_behind(settings):
    with pytest.raises(store.UploadError, match="empty"):
        store.receive(io.BytesIO(b""), "empty.md", settings)
    settings.max_file_bytes = 10
    with pytest.raises(store.UploadError, match="larger than"):
        store.receive(io.BytesIO(b"# " + b"x" * 100), "big.md", settings)
    assert not any(settings.tmp_dir.iterdir())


def test_index_limits_store_instead_of_reject(settings):
    settings.max_md_bytes = 10
    received = store.receive(io.BytesIO(b"# " + b"x" * 100), "big.md", settings)
    assert received.kind == "other" and "Not searchable" in received.note
    received.tmp_path.unlink()


def test_plain_text_is_indexed(conn, settings):
    from mnemerynne import users, worker
    from fakes import FakeEmbedder
    alice = users.create_user(conn, "alice", "pw-alice-123")
    inbox = conn.execute("SELECT id FROM collections WHERE owner_user_id = ?", (alice,)).fetchone()[0]
    received = store.receive(io.BytesIO(b"Pool hours are 7am to 10pm.\n\nTowels at the desk."), "pool.txt", settings)
    file_id, _ = store.add_file(conn, settings, received, inbox, alice)
    while worker.run_one(conn, settings, FakeEmbedder()):
        pass
    assert conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()[0] == "ready"
    assert "Pool hours" in conn.execute("SELECT text FROM chunks WHERE file_id = ?", (file_id,)).fetchone()[0]


def test_display_name_is_sanitised():
    assert store.clean_display_name("C:\\fakepath\\..\\Guide\x07 2026.pdf") == "Guide 2026.pdf"
    assert store.clean_display_name("") == "untitled"
