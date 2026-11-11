-- Lumi Dungeons with friends (lumiparty.py): a party of up to three, each with their own Lumi, on one shared floor.
-- The whole party is one JSON document, like arcade_rooms: its lobby, members, the floor, the votes and the puzzle turn.
-- Who is still there (a stream open, or the TV for a couch party) is kept in memory (lobby.PRESENCE), not written.
CREATE TABLE lumi_parties (
    code TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL                              -- unix seconds of the last real change
);

-- One party per account at a time: who is in which party (the document holds the rest).
CREATE TABLE lumi_party_members (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code TEXT NOT NULL REFERENCES lumi_parties(code) ON DELETE CASCADE
);
CREATE INDEX lumi_party_members_code ON lumi_party_members(code);
