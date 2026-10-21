-- Folder-level access in libraries.
-- A folder inherits access from its parent (the library itself for top-level
-- folders) unless `inherit` is 0; grants on a folder add to what it inherits
-- and pass down to its subfolders. Turning inheritance off hides the folder
-- from everyone who only had access through its parents. Inbox folders never
-- take grants: inboxes stay private.

ALTER TABLE folders ADD COLUMN inherit INTEGER NOT NULL DEFAULT 1 CHECK (inherit IN (0, 1));

CREATE TABLE folder_grants (
    id         INTEGER PRIMARY KEY,
    folder_id  INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    group_id   INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    can_write  INTEGER NOT NULL DEFAULT 0 CHECK (can_write IN (0, 1)),
    can_claude INTEGER NOT NULL DEFAULT 1 CHECK (can_claude IN (0, 1)),
    CHECK ((user_id IS NULL) != (group_id IS NULL))
);
CREATE UNIQUE INDEX folder_grants_user  ON folder_grants(folder_id, user_id)  WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX folder_grants_group ON folder_grants(folder_id, group_id) WHERE group_id IS NOT NULL;
CREATE INDEX folder_grants_by_user  ON folder_grants(user_id);
CREATE INDEX folder_grants_by_group ON folder_grants(group_id);
