-- Bumped when a password is reset: sessions signed in before then end.
ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
