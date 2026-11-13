-- A version per collection that changes whenever any of its folders is added, changed or removed, kept by the
-- database itself (triggers), so it's right whichever process wrote: the web app, the worker's share scan, the
-- CLI. db.folder_rows() keeps a collection's folder rows in memory and reads them again only when its version
-- has changed: on the box, every request re-read the 28,789 folder rows of the big library, a dozen at once.
-- No row yet = version 0. Each change sets a new random version rather than adding one, so a database file
-- swapped for another (a restore while the app runs) can't land on the version that's in memory by chance.
CREATE TABLE folder_versions (
    collection_id INTEGER PRIMARY KEY,
    version       INTEGER NOT NULL DEFAULT 0
);

CREATE TRIGGER folders_version_insert AFTER INSERT ON folders BEGIN
    INSERT OR IGNORE INTO folder_versions(collection_id, version) VALUES (NEW.collection_id, 0);
    UPDATE folder_versions SET version = random() WHERE collection_id = NEW.collection_id;
END;

CREATE TRIGGER folders_version_update AFTER UPDATE ON folders BEGIN
    INSERT OR IGNORE INTO folder_versions(collection_id, version) VALUES (NEW.collection_id, 0);
    UPDATE folder_versions SET version = random() WHERE collection_id = NEW.collection_id;
    -- A folder moved to another collection changes that one too.
    INSERT OR IGNORE INTO folder_versions(collection_id, version) VALUES (OLD.collection_id, 0);
    UPDATE folder_versions SET version = random()
        WHERE collection_id = OLD.collection_id AND OLD.collection_id != NEW.collection_id;
END;

CREATE TRIGGER folders_version_delete AFTER DELETE ON folders BEGIN
    INSERT OR IGNORE INTO folder_versions(collection_id, version) VALUES (OLD.collection_id, 0);
    UPDATE folder_versions SET version = random() WHERE collection_id = OLD.collection_id;
END;
