-- mnemerynne:no-fk
-- Two more kinds of searchable file: 'doc' (Word, PowerPoint, Excel, CSV,
-- OpenDocument, RTF, HTML; mnemerynne/ingest/documents.py) and 'code'
-- (source and config files, cited by line). Rebuilds `blobs` for the wider
-- CHECK, so it runs with foreign keys off; db.migrate checks them before commit.

CREATE TABLE blobs_new (
    sha256       TEXT PRIMARY KEY,
    -- 'other' is stored but never parsed or indexed.
    kind         TEXT NOT NULL CHECK (kind IN ('pdf', 'md', 'txt', 'doc', 'code', 'other')),
    mime         TEXT,
    size         INTEGER NOT NULL,
    unit_count   INTEGER,
    rel_path     TEXT NOT NULL,
    ocr_rel_path TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO blobs_new(sha256, kind, mime, size, unit_count, rel_path, ocr_rel_path, created_at)
    SELECT sha256, kind, mime, size, unit_count, rel_path, ocr_rel_path, created_at FROM blobs;
DROP TABLE blobs;
ALTER TABLE blobs_new RENAME TO blobs;
