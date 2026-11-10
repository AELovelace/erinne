-- Push notifications to the installed web app (mnemerynne/push.py). A subscription is one browser on one device that
-- said yes; anything may send through it later, and Lumi Pet is the first (lumipet.py sweep).

CREATE TABLE push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,                        -- the push service's address for this browser
    p256dh TEXT NOT NULL,                                 -- the browser's public key (base64url)
    auth TEXT NOT NULL,                                   -- the browser's shared secret (base64url)
    utc_offset INTEGER NOT NULL DEFAULT 0,                -- minutes east of UTC when it last subscribed, for quiet hours
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX push_subscriptions_user ON push_subscriptions(user_id);

-- One row per thing Lumi might mention (hungry since 14:02, a nap that finished at 15:10), so each is mentioned at
-- most once however often the worker looks: MommyBot's Littlepottchi kept its reminders the same way.
CREATE TABLE lumi_push_events (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                                   -- hunger, energy, joy, nap
    episode TEXT NOT NULL,                                -- when this need began
    created_at INTEGER NOT NULL,                          -- milliseconds, like the pet's own times
    sent_at INTEGER,                                      -- NULL: waiting (quiet hours, or one was sent recently)
    PRIMARY KEY (user_id, kind, episode)
);
