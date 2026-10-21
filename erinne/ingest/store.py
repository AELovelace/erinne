"""Receiving uploads, content-addressed blob storage and file records.

Blobs live at data/blobs/<ab>/<sha256>.<ext>; the database stores only that
relative path, so the data directory can move between machines.
"""

from __future__ import annotations

import hashlib
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
_READ_SIZE = 1024 * 1024


class UploadError(ValueError):
    """The upload was rejected; the message is safe to show to the user."""


@dataclass
class Received:
    sha256: str
    kind: str  # 'pdf' | 'md'
    size: int
    tmp_path: Path
    display_name: str


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
    try:
        with os.fdopen(fd, "wb") as out:
            while block := stream.read(_READ_SIZE):
                if kind is None:
                    kind = _sniff(block, display_name)
                size += len(block)
                limit = settings.max_pdf_bytes if kind == "pdf" else settings.max_md_bytes
                if size > limit:
                    raise UploadError(f"File is larger than the {limit // (1024 * 1024)} MB limit.")
                sha.update(block)
                out.write(block)
        if size == 0:
            raise UploadError("The file is empty.")
        _validate(kind, tmp_path, settings)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return Received(sha.hexdigest(), kind, size, tmp_path, display_name)


def _sniff(first_block: bytes, display_name: str) -> str:
    """Decide the type from the content; the extension only distinguishes text."""
    if first_block.lstrip()[:5] == b"%PDF-":
        return "pdf"
    if display_name.lower().endswith(_MD_SUFFIXES):
        return "md"
    raise UploadError("Only PDF and Markdown (.md) files are supported.")


def _validate(kind: str, path: Path, settings: Settings) -> None:
    if kind == "pdf":
        try:
            pages = pdf.page_count(path)
        except pdf.PdfError as exc:
            raise UploadError(str(exc)) from exc
        if pages > settings.max_pdf_pages:
            raise UploadError(f"PDF has {pages} pages; the limit is {settings.max_pdf_pages}.")
    else:
        try:
            markdown.decode(path.read_bytes())
        except markdown.MarkdownError as exc:
            raise UploadError(str(exc)) from exc


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
        "INSERT OR IGNORE INTO blobs(sha256, kind, size, rel_path) VALUES (?, ?, ?, ?)",
        (received.sha256, received.kind, received.size, rel_path),
    )
    return rel_path


def add_file(
    conn: sqlite3.Connection,
    settings: Settings,
    received: Received,
    collection_id: int,
    uploaded_by: int | None,
) -> tuple[int, bool]:
    """Store the blob, create the file record and queue ingestion.

    Returns (file_id, created). Uploading the same content to the same
    collection again returns the existing file.
    """
    with transaction(conn):
        store(conn, settings, received)
        existing = conn.execute(
            "SELECT id FROM files WHERE collection_id = ? AND blob_sha256 = ?",
            (collection_id, received.sha256),
        ).fetchone()
        if existing:
            return existing["id"], False
        file_id = conn.execute(
            "INSERT INTO files(collection_id, blob_sha256, display_name, uploaded_by) VALUES (?, ?, ?, ?)",
            (collection_id, received.sha256, received.display_name, uploaded_by),
        ).lastrowid
        jobs.enqueue(conn, "ingest", file_id)
    return file_id, True


def add_path(conn: sqlite3.Connection, settings: Settings, path: Path, collection_id: int,
             uploaded_by: int | None = None) -> tuple[int, bool]:
    """Import a file from disk (migration, CLI) through the same checks as uploads."""
    with open(path, "rb") as fh:
        received = receive(fh, path.name, settings)
    return add_file(conn, settings, received, collection_id, uploaded_by)


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
