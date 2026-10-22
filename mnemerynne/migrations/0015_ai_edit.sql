-- "AI can edit" per folder and file (Claude, and Erynne's proposals), from
-- Files: the AI edit column and the Properties dialog, for people who can
-- change that place. A folder's flags cover everything under it.
--   ai_edit:    in their own inbox, people let the AI edit here (like the
--               Agents folder). Elsewhere only an admin allows AI edits
--               (collections.claude_edit).
--   ai_no_edit: keeps it read-only for the AI even where it's allowed.

ALTER TABLE folders ADD COLUMN ai_edit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE folders ADD COLUMN ai_no_edit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN ai_edit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN ai_no_edit INTEGER NOT NULL DEFAULT 0;
