-- Indexes for the slow paths the performance audit of 2026-11-10 found on a share of 285,000 files.

-- The home page's "is there anything to ask about?": straight to a ready file, past every stored one.
CREATE INDEX files_ready ON files(collection_id) WHERE status = 'ready';
-- A folder's contents and item counts, and what happens to them when a folder goes.
CREATE INDEX files_folder ON files(folder_id);
CREATE INDEX folders_parent_id ON folders(parent_id);
CREATE INDEX files_listing ON files(collection_id, COALESCE(folder_id, 0), display_name COLLATE NOCASE)
    WHERE deleted_at IS NULL;
-- Whose poster is this? (each poster's permission check, and clearing out unused art on every catalog pass)
CREATE INDEX media_items_artwork ON media_items(artwork_id);
CREATE INDEX media_items_fanart ON media_items(fanart_id);
-- Removing a file removes its watch progress.
CREATE INDEX media_progress_file ON media_progress(file_id);
