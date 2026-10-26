-- mnemerynne:no-fk
-- People edit text and Markdown files in the browser (the Wiki tab and the Edit button in Files,
-- web/editor.py). Their saves keep the previous version like Claude's and Erynne's do, recorded
-- with via 'web'; the CHECK on file_versions.via didn't allow it.

CREATE TABLE file_versions_new (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore', 'erynne', 'web')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
INSERT INTO file_versions_new SELECT id, file_id, sha256, size, rel_path, created_at, created_by, via, token_id, summary
FROM file_versions;
DROP TABLE file_versions;
ALTER TABLE file_versions_new RENAME TO file_versions;
CREATE INDEX file_versions_file ON file_versions(file_id, id);
