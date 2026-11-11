-- TV guests (media/screens.py): people whose remotes may drive another person's TVs. Every household TV is paired
-- to one account; a guest's own remote (paired to the guest's account) drives those TVs too, and in the Lumi apps
-- the guest plays their own pet there ("Who's playing?"). One row covers all of the owner's TVs. Removing the row
-- stops the guest's remote on its next press.
CREATE TABLE media_screen_guests (
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- whose TVs
    guest_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- whose remotes may drive them
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner_id, guest_id)
);
CREATE INDEX media_screen_guests_guest ON media_screen_guests(guest_id);
