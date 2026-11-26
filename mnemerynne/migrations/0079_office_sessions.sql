-- mnemerynne:no-fk
-- ONLYOFFICE Docs (mnemerynne/office.py): Word, Excel and PowerPoint files opened in the browser editor.
--
-- office_sessions: one row per file someone has open in the editor. Everyone who opens it while the row lives gets
-- the same document key, so they edit together; the row ends when the Document Server says everyone closed it
-- (status 2 or 4), and while it lives Claude, the drive and the web editor are refused (editing.py's lock check),
-- so nobody overwrites what's being typed. base_sha256 is the version the editor started from (advanced by each
-- force-save); a save whose base no longer matches the file becomes a conflict copy instead.
CREATE TABLE office_sessions (
    file_id        INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    doc_key        TEXT NOT NULL UNIQUE,
    base_sha256    TEXT NOT NULL,
    opened_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    opened_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_status_at TEXT NOT NULL DEFAULT (datetime('now')),
    users_json     TEXT NOT NULL DEFAULT '[]'
);

-- Saving from the editor keeps the old content as a version: via 'onlyoffice'. SQLite can't change a CHECK in
-- place, so the table is rebuilt (same columns, same order, same index), as 0078 did.
CREATE TABLE file_versions_new (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore', 'erynne', 'web', 'sync', 'drive', 'onlyoffice')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
INSERT INTO file_versions_new SELECT id, file_id, sha256, size, rel_path, created_at, created_by, via, token_id, summary
FROM file_versions;
DROP TABLE file_versions;
ALTER TABLE file_versions_new RENAME TO file_versions;
CREATE INDEX file_versions_file ON file_versions(file_id, id);
