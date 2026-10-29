-- Media speakers (mnemerynne/media/speakers.py): a remote panel with a speaker (the FNK0104B build of
-- Mnemerynne-tab) is also one of its owner's screens, so the phone can cast music to it. The screen is made
-- when the remote pairs as a speaker and removed with the remote. Its key stays the remote's: it never opens
-- Media, and the speaker plays only what is cast to it, through short-lived tickets.

ALTER TABLE media_remotes ADD COLUMN screen_id INTEGER REFERENCES media_screens(id) ON DELETE SET NULL;
ALTER TABLE media_screens ADD COLUMN speaker INTEGER NOT NULL DEFAULT 0 CHECK (speaker IN (0, 1));
