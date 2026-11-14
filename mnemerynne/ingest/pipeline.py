"""Ingest one file: extract (once per blob), chunk, embed, swap in atomically."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from mnemerynne import fs, search_index
from mnemerynne.clients.embed import Embedder, pack_vector
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import Segment, chunk, documents, markdown, ocr, pdf, store

log = logging.getLogger("mnemerynne.ingest")
# Shown in Files for a backed-up file that isn't read for search (fs.not_indexed).
NOT_INDEXED_NOTE = "Backed up; not searchable. Turn on Searchable for this backup to search it."


class IngestError(RuntimeError):
    """Permanent failure: retrying won't help (bad file, no text)."""


def _set(conn: sqlite3.Connection, file_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE files SET {sets}, updated_at = datetime('now') WHERE id = ?", (*fields.values(), file_id)
    )


# ------------------------------------------------------------------ extraction
def _extract_pdf(conn, settings: Settings, blob: sqlite3.Row, file_id: int) -> tuple[list[Segment], str | None]:
    path = store.content_path(conn, settings, file_id)
    try:
        doc = pdf.read_pdf(path)
    except pdf.PdfError as exc:
        raise IngestError(str(exc)) from exc

    scanned = [i for i, text in enumerate(doc.pages) if len(text.strip()) < settings.ocr_min_chars]
    note = None
    ocr_done: set[int] = set()
    if scanned and ocr.available(settings.ocr_command):
        _set(conn, file_id, progress=f"OCR on {len(scanned)} scanned page(s)")
        ocr_rel = blob["rel_path"].replace(".pdf", ".ocr.pdf")
        ocr_path = store.blob_path(settings, ocr_rel)
        try:
            ocr.run(settings.ocr_command, path, ocr_path, settings.ocr_timeout)
        except ocr.OcrError as exc:
            note = f"OCR failed, {len(scanned)} scanned page(s) are not searchable: {exc}"
        else:
            for index, text in pdf.read_pages(ocr_path, scanned).items():
                if len(text.strip()) >= settings.ocr_min_chars:
                    doc.pages[index] = text
                    ocr_done.add(index)
            conn.execute("UPDATE blobs SET ocr_rel_path = ? WHERE sha256 = ?", (ocr_rel, blob["sha256"]))
            missed = len(scanned) - len(ocr_done)
            if missed:
                note = f"{missed} page(s) had no readable text even after OCR."
    elif scanned:
        note = (f"{len(scanned)} page(s) look scanned and have no text; "
                "install ocrmypdf on the server to make them searchable.")

    segments = pdf.segments(doc, scanned_ocr=ocr_done)
    conn.execute("UPDATE blobs SET unit_count = ? WHERE sha256 = ?", (len(doc.pages), blob["sha256"]))
    return segments, note


def _extract(conn, settings: Settings, blob: sqlite3.Row, file_id: int) -> tuple[list[Segment], str | None]:
    """Segments for a blob, from the blob_units cache or by extracting now."""
    rows = conn.execute(
        "SELECT * FROM blob_units WHERE sha256 = ? ORDER BY unit_no", (blob["sha256"],)
    ).fetchall()
    if rows:
        return [Segment(r["text"], r["heading_path"], r["page_no"], r["anchor"], bool(r["ocr"])) for r in rows], None

    _set(conn, file_id, progress="Reading text")
    if blob["kind"] == "pdf":
        segments, note = _extract_pdf(conn, settings, blob, file_id)
    elif blob["kind"] in ("doc", "code"):
        try:
            segments = documents.segments(store.content_path(conn, settings, file_id), blob["display_name"],
                                          libreoffice_command=settings.libreoffice_command,
                                          timeout=settings.libreoffice_timeout)
        except documents.DocumentError as exc:
            raise IngestError(str(exc)) from exc
        note = None
        conn.execute("UPDATE blobs SET unit_count = ? WHERE sha256 = ?", (len(segments), blob["sha256"]))
    else:
        try:
            text = markdown.decode(store.content_path(conn, settings, file_id).read_bytes())
        except markdown.MarkdownError as exc:
            raise IngestError(str(exc)) from exc
        # Plain text is one unit; the chunker still splits it on paragraphs.
        segments = markdown.segments(text) if blob["kind"] == "md" else ([Segment(text)] if text.strip() else [])
        note = None
        conn.execute("UPDATE blobs SET unit_count = ? WHERE sha256 = ?", (len(segments), blob["sha256"]))

    with transaction(conn):
        conn.executemany(
            "INSERT INTO blob_units(sha256, unit_no, kind, page_no, heading_path, anchor, text, ocr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(blob["sha256"], i, "page" if blob["kind"] == "pdf" else "section", s.page_no,
              s.section_path, s.anchor, s.text, int(s.ocr)) for i, s in enumerate(segments)],
        )
    return segments, note


# ------------------------------------------------------------------ indexing
def _swap(conn: sqlite3.Connection, file_id: int, write) -> bool:
    """Swap a file's chunks for new ones atomically: search sees either the old chunks or the new ones.
    `write(search, collection_id, folder_id, hidden)` inserts them; the file's scope is read inside the swap,
    as the file may have been moved or binned while it was embedding. On the connection that holds the chunks
    (search_index.on): core's own transaction before the move, with the file marked ready in it; search.db's
    after it (core read there, through its read-only attachment), and the file marked ready in core just after.
    False: the file was deleted for good meanwhile."""
    with search_index.on(conn) as search:
        with transaction(search):
            if search.execute("SELECT 1 FROM files WHERE id = ?", (file_id,)).fetchone() is None:
                return False
            collection_id, folder_id = fs.search_scope(search, file_id)
            hidden = fs.claude_hidden(search, file_id)
            search.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))      # vectors follow (trigger)
            write(search, collection_id, folder_id, hidden)
            if search is conn:
                _set(conn, file_id, status="ready", progress=None, error=None)
    if search is not conn:
        with transaction(conn):
            _set(conn, file_id, status="ready", progress=None, error=None)
    return True


def _copy_from_twin(conn, file_id: int, sha256: str) -> bool:
    """Reuse chunks+vectors from the same blob already indexed for another file."""
    twin = conn.execute(
        "SELECT id FROM files WHERE blob_sha256 = ? AND status = 'ready' AND id != ? "
        "AND EXISTS (SELECT 1 FROM chunks WHERE file_id = files.id) LIMIT 1",
        (sha256, file_id),
    ).fetchone()
    if twin is None:
        return False

    def write(search, collection_id, folder_id, hidden):
        for row in search.execute("SELECT * FROM chunks WHERE file_id = ? ORDER BY ord", (twin["id"],)).fetchall():
            new_id = search.execute(
                "INSERT INTO chunks(file_id, collection_id, folder_id, ord, page_start, page_end, anchor, "
                "section_path, text, tokens, claude_hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, collection_id, folder_id, row["ord"], row["page_start"], row["page_end"], row["anchor"],
                 row["section_path"], row["text"], row["tokens"], hidden),
            ).lastrowid
            vector = search.execute("SELECT embedding FROM chunk_vec WHERE rowid = ?", (row["id"],)).fetchone()
            search.execute(
                "INSERT INTO chunk_vec(rowid, embedding, collection_id, folder_id, claude_hidden) VALUES (?, ?, ?, ?, ?)",
                (new_id, vector["embedding"], collection_id, folder_id, hidden),
            )
    _swap(conn, file_id, write)
    return True


def process_file(conn: sqlite3.Connection, settings: Settings, embedder: Embedder, file_id: int) -> None:
    row = conn.execute(
        "SELECT f.id, f.collection_id, f.display_name, b.* FROM files f "
        "JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?", (file_id,)
    ).fetchone()
    if row is None:
        log.info("File %s was deleted before ingestion; skipping.", file_id)
        return
    if row["kind"] == "other":
        _set(conn, file_id, status="stored", progress=None, error=None)
        return
    if fs.not_indexed(conn, conn.execute("SELECT folder_id FROM files WHERE id = ?", (file_id,)).fetchone()[0]):
        # A backup that isn't Searchable (sync.py): kept and listed, never read.
        _set(conn, file_id, status="stored", progress=None, error=None, notes=NOT_INDEXED_NOTE)
        return
    _set(conn, file_id, status="processing", error=None)

    try:
        segments, note = _extract(conn, settings, row, file_id)
    except FileNotFoundError as exc:
        # Gone from the share since it was queued; the next scan removes it.
        raise IngestError("The file is no longer there.") from exc
    if note:
        _set(conn, file_id, notes=note)

    if _copy_from_twin(conn, file_id, row["sha256"]):
        log.info("File %s: reused chunks from an identical file.", file_id)
        return

    chunks = chunk.chunk_segments(
        segments, settings.chunk_target_tokens, settings.chunk_max_tokens, settings.chunk_overlap_tokens
    )
    if not chunks:
        raise IngestError(note or "No text could be extracted from this file.")

    title = Path(row["display_name"]).stem
    _set(conn, file_id, progress=f"Embedding 0/{len(chunks)}")
    vectors = embedder.embed_documents(
        [chunk.embed_text(title, c) for c in chunks],
        on_batch=lambda done, total: _set(conn, file_id, progress=f"Embedding {done}/{total}"),
    )

    def write(search, collection_id, folder_id, hidden):
        for ord_, (c, vector) in enumerate(zip(chunks, vectors)):
            chunk_id = search.execute(
                "INSERT INTO chunks(file_id, collection_id, folder_id, ord, page_start, page_end, anchor, "
                "section_path, text, tokens, claude_hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, collection_id, folder_id, ord_, c.page_start, c.page_end, c.anchor,
                 c.section_path, c.text, c.tokens, hidden),
            ).lastrowid
            search.execute(
                "INSERT INTO chunk_vec(rowid, embedding, collection_id, folder_id, claude_hidden) VALUES (?, ?, ?, ?, ?)",
                (chunk_id, pack_vector(vector), collection_id, folder_id, hidden),
            )
    if not _swap(conn, file_id, write):
        return  # deleted for good meanwhile
    log.info("File %s: %d chunks indexed.", file_id, len(chunks))
