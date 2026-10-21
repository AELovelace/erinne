"""Rebuild every chunk vector with the configured embedder.

Used when switching embedding models (the vector table is recreated if the
dimension changes). Chunk text is reused; nothing is re-extracted or re-chunked.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from erinne import db
from erinne.clients.embed import pack_vector
from erinne.config import Settings
from erinne.ingest import Chunk, chunk

log = logging.getLogger("erinne.reembed")


def reembed_all(conn: sqlite3.Connection, settings: Settings, embedder, batch: int = 512,
                progress=print) -> int:
    """Re-embed all chunks; returns the number of vectors written."""
    db.drop_vector_table(conn)
    db.ensure_vector_table(conn, settings.embed_dim)
    rows = conn.execute(
        "SELECT c.id, c.collection_id, c.page_start, c.page_end, c.anchor, c.section_path, c.text, "
        "f.display_name FROM chunks c JOIN files f ON f.id = c.file_id ORDER BY c.id"
    ).fetchall()
    written = 0
    for start in range(0, len(rows), batch):
        part = rows[start : start + batch]
        texts = [chunk.embed_text(Path(r["display_name"]).stem,
                                  Chunk(r["text"], r["section_path"], r["page_start"], r["page_end"], r["anchor"]))
                 for r in part]
        vectors = embedder.embed_documents(texts)
        with db.transaction(conn):
            conn.executemany(
                "INSERT INTO chunk_vec(rowid, embedding, collection_id) VALUES (?, ?, ?)",
                [(r["id"], pack_vector(v), r["collection_id"]) for r, v in zip(part, vectors)],
            )
        written += len(part)
        progress(f"Re-embedded {written}/{len(rows)} chunks")
    return written
