-- Collections stored on the SMB share. A collection with a share_path is the
-- folder <share_root>/<share_path>: what is on disk is the truth, and these
-- tables index it (folder and file names are the names on disk).

ALTER TABLE collections ADD COLUMN share_path TEXT;
-- The folder's inode, so a library renamed on the share keeps its index.
ALTER TABLE collections ADD COLUMN share_inode INTEGER;
CREATE UNIQUE INDEX collections_share_path ON collections(share_path COLLATE NOCASE) WHERE share_path IS NOT NULL;

-- A share file as it was when last read: changes in size or mtime mean it is
-- hashed again; the inode recognises it after a rename or move.
ALTER TABLE files ADD COLUMN disk_size INTEGER;
ALTER TABLE files ADD COLUMN disk_mtime_ns INTEGER;
ALTER TABLE files ADD COLUMN disk_inode INTEGER;

-- Where a binned share file or folder sits in the trash directory.
ALTER TABLE files ADD COLUMN trash_path TEXT;
ALTER TABLE folders ADD COLUMN trash_path TEXT;
