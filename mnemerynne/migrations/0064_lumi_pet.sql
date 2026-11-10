-- Lumi Pet moves from the browser to the account (mnemerynne/lumipet.py): one companion per person, shared by their
-- phone, computer and paired TVs. Everything about her (meters, memories, pantry, toys) is one JSON document, the way
-- MommyBot's Littlepottchi kept its dolls, so new features need no new columns.

CREATE TABLE lumi_pets (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL,                                   -- the pet document; lumipet.py owns its shape
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
