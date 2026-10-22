-- People with Claude connected can take a folder or a file out of what Claude
-- sees, from the Claude column in Files (people who can change that place).
-- It only removes: what an admin shares with Claude stays the upper limit.
-- A hidden folder hides everything under it. Erynne in chat is unaffected.
-- files.claude_hidden is copied onto the file's chunks and vectors so search
-- filters it inside the query (chunk_vec gains the column: vec layout 3).

ALTER TABLE folders ADD COLUMN claude_hidden INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN claude_hidden INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks ADD COLUMN claude_hidden INTEGER NOT NULL DEFAULT 0;
