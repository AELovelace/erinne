"""Receiving uploads, content-addressed blob storage and file records.

Any file type is accepted. PDF, Markdown and plain text are indexed for
search; everything else (and anything that can't be read, such as an
encrypted PDF) is stored as kind 'other' with status 'stored'.

Blobs live at data/blobs/<ab>/<sha256>.<ext>; the database stores only that
relative path, so the data directory can move between machines.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import sqlite3
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from erinne import jobs
from erinne.config import Settings
from erinne.db import transaction
from erinne.ingest import markdown, pdf

_MD_SUFFIXES = (".md", ".markdown")
_TXT_SUFFIXES = (".txt", ".text", ".log")
_READ_SIZE = 1024 * 1024


class UploadError(ValueError):
    """The upload was rejected; the message is safe to show to the user."""


@dataclass
class Received:
    sha256: str
    kind: str  # 'pdf' | 'md' | 'txt' | 'other'
    size: int
    tmp_path: Path
    display_name: str
    mime: str = "application/octet-stream"
    note: str | None = None  # why an indexable-looking file is stored only


def clean_display_name(filename: str) -> str:
    name = Path((filename or "").replace("\\", "/")).name
    name = "".join(ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:200] or "untitled"


def receive(stream: BinaryIO, filename: str, settings: Settings) -> Received:
    """Copy an upload to a temp file while hashing and validating it."""
    display_name = clean_display_name(filename)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=settings.tmp_dir, suffix=".upload")
    tmp_path = Path(tmp_name)
    sha = hashlib.sha256()
    size = 0
    kind = None
    limit = settings.max_file_bytes
    try:
        with os.fdopen(fd, "wb") as out:
            while block := stream.read(_READ_SIZE):
                if kind is None:
                    kind = _sniff(block, display_name)
                size += len(block)
                if size > limit:
                    raise UploadError(f"File is larger than the {limit // (1024 * 1024)} MB limit.")
                sha.update(block)
                out.write(block)
        if size == 0:
            raise UploadError("The file is empty.")
        note = _indexable(kind, tmp_path, size, settings)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if note:
        kind = "other"
    return Received(sha.hexdigest(), kind, size, tmp_path, display_name, _mime(kind, display_name), note)


def _sniff(first_block: bytes, display_name: str) -> str:
    """Decide the type from the content; the extension only distinguishes text."""
    if first_block.lstrip()[:5] == b"%PDF-":
        return "pdf"
    name = display_name.lower()
    if name.endswith(_MD_SUFFIXES):
        return "md"
    if name.endswith(_TXT_SUFFIXES):
        return "txt"
    return "other"


def _mime(kind: str, display_name: str) -> str:
    if kind == "pdf":
        return "application/pdf"
    if kind == "md":
        return "text/markdown"
    if kind == "txt":
        return "text/plain"
    return mimetypes.guess_type(display_name)[0] or "application/octet-stream"


def _indexable(kind: str, path: Path, size: int, settings: Settings) -> str | None:
    """None if the file can be indexed as `kind`, else why it will only be stored."""
    if kind == "pdf":
        if size > settings.max_pdf_bytes:
            return f"Not searchable: PDFs over {settings.max_pdf_bytes // (1024 * 1024)} MB aren't indexed."
        try:
            pages = pdf.page_count(path)
        except pdf.PdfError as exc:
            return f"Not searchable: {exc}"
        if pages > settings.max_pdf_pages:
            return f"Not searchable: the PDF has {pages} pages; the limit is {settings.max_pdf_pages}."
    elif kind in ("md", "txt"):
        if size > settings.max_md_bytes:
            return f"Not searchable: text files over {settings.max_md_bytes // (1024 * 1024)} MB aren't indexed."
        try:
            markdown.decode(path.read_bytes())
        except markdown.MarkdownError as exc:
            return f"Not searchable: {exc}"
    return None


def blob_rel_path(sha256: str, kind: str) -> str:
    return f"{sha256[:2]}/{sha256}.{kind}"


def blob_path(settings: Settings, rel_path: str) -> Path:
    return settings.blob_dir / Path(rel_path)


def store(conn: sqlite3.Connection, settings: Settings, received: Received) -> str:
    """Move the received file into blob storage; returns its relative path."""
    rel_path = blob_rel_path(received.sha256, received.kind)
    dest = blob_path(settings, rel_path)
    if dest.exists():
        received.tmp_path.unlink(missing_ok=True)  # identical content already stored
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(received.tmp_path, dest)
    conn.execute(
        "INSERT OR IGNORE INTO blobs(sha256, kind, mime, size, rel_path) VALUES (?, ?, ?, ?, ?)",
        (received.sha256, received.kind, received.mime, received.size, rel_path),
    )
    return rel_path


def add_file(
    conn: sqlite3.Connection,
    settings: Settings,
    received: Received,
    collection_id: int,
    uploaded_by: int | None,
    folder_id: int | None = None,
    folder_parts: list[str] | None = None,
) -> tuple[int, bool]:
    """Store the blob, create the file record and queue ingestion.

    `folder_parts` (from a folder upload) are created under `folder_id` as
    needed. Returns (file_id, created). Uploading the same content into the
    same folder again returns the existing file; a different file with a
    taken name gets "name (2).ext".
    """
    from erinne import fs  # fs builds on this module

    with transaction(conn):
        if folder_parts:
            folder_id = fs.ensure_path(conn, collection_id, folder_id, folder_parts, uploaded_by)
        store(conn, settings, received)
        existing = conn.execute(
            "SELECT id FROM files WHERE collection_id = ? AND COALESCE(folder_id, 0) = ? AND blob_sha256 = ? "
            "AND deleted_at IS NULL",
            (collection_id, folder_id or 0, received.sha256),
        ).fetchone()
        if existing:
            return existing["id"], False
        # The blob's kind is whatever it was first stored as.
        kind = conn.execute("SELECT kind FROM blobs WHERE sha256 = ?", (received.sha256,)).fetchone()["kind"]
        name = fs.unique_name(conn, collection_id, folder_id, received.display_name, "file")
        stored_only = kind == "other"
        file_id = conn.execute(
            "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, uploaded_by, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (collection_id, folder_id, received.sha256, name, uploaded_by,
             "stored" if stored_only else "queued", received.note),
        ).lastrowid
        if not stored_only:
            jobs.enqueue(conn, "ingest", file_id)
    return file_id, True


def add_path(conn: sqlite3.Connection, settings: Settings, path: Path, collection_id: int,
             uploaded_by: int | None = None, folder_parts: list[str] | None = None) -> tuple[int, bool]:
    """Import a file from disk (migration, CLI) through the same checks as uploads."""
    with open(path, "rb") as fh:
        received = receive(fh, path.name, settings)
    return add_file(conn, settings, received, collection_id, uploaded_by, folder_parts=folder_parts)


def delete_file(conn: sqlite3.Connection, settings: Settings, file_id: int) -> bool:
    """Delete a file (its chunks, vectors and jobs cascade). The blob goes too
    once no collection uses it."""
    orphan = None
    with transaction(conn):
        row = conn.execute(
            "SELECT f.blob_sha256, b.rel_path, b.ocr_rel_path FROM files f "
            "JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?", (file_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        still_used = conn.execute(
            "SELECT 1 FROM files WHERE blob_sha256 = ? LIMIT 1", (row["blob_sha256"],)
        ).fetchone()
        if not still_used:
            conn.execute("DELETE FROM blobs WHERE sha256 = ?", (row["blob_sha256"],))
            orphan = row
    if orphan:
        for rel in (orphan["rel_path"], orphan["ocr_rel_path"]):
            if rel:
                blob_path(settings, rel).unlink(missing_ok=True)
    return True
