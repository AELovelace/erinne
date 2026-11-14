-- search.db: search's index, moved out of the core database (docs/plans/services-rewrite.md, Phase 6a).
-- chunks is the table core migrations 0001, 0004 and 0014 left, without its reference to core's files
-- (SQLite can't follow a reference into another file: db.FEATURE_DBS['search'].file_columns lists file_id,
-- and sweep_orphans clean a deleted file's chunks; search_index.drop does it at once). Its ids now never come
-- back once used (AUTOINCREMENT): an old answer's source (core's message_sources.chunk_id, which can't follow
-- a deleted chunk any more) must never point at another file's text. The keyword index and its triggers are
-- the same; chunk_vec (sqlite-vec) is made by db.ensure_vector_table, its size from the settings, and keeps
-- its size and layout in search_meta. db.move_tables copies the rows and vectors over.

CREATE TABLE search_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL,                       -- core's files.id
    collection_id INTEGER NOT NULL,                       -- where the file is, for search's scope (fs.search_scope)
    ord           INTEGER NOT NULL,
    page_start    INTEGER,
    page_end      INTEGER,
    anchor        TEXT,
    section_path  TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL,
    tokens        INTEGER,
    folder_id     INTEGER NOT NULL DEFAULT 0,             -- 0: the collection's top
    claude_hidden INTEGER NOT NULL DEFAULT 0              -- files.claude_hidden, copied
);
CREATE INDEX chunks_file ON chunks(file_id);
CREATE INDEX chunks_collection ON chunks(collection_id);

-- Keyword index over chunks (external content; kept in sync by triggers).
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    section_path, text,
    content = 'chunks', content_rowid = 'id',
    tokenize = 'porter unicode61'
);
CREATE TRIGGER chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, section_path, text) VALUES (new.id, new.section_path, new.text);
END;
CREATE TRIGGER chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, section_path, text)
        VALUES ('delete', old.id, old.section_path, old.text);
END;
CREATE TRIGGER chunks_fts_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, section_path, text)
        VALUES ('delete', old.id, old.section_path, old.text);
    INSERT INTO chunks_fts(rowid, section_path, text) VALUES (new.id, new.section_path, new.text);
END;
