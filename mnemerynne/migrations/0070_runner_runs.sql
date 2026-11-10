-- StickBob Runner's tickets (mnemerynne/runner.py): when the server saw each run start, so the distance a finished run
-- claims can be checked against the time it could have taken before it pays coins. At most one per person: starting a
-- run replaces the last ticket, and finishing uses it up. AUTOINCREMENT so a run number is never reused, because the
-- coins it pays are recorded once against that number (coin_transactions.ref).
CREATE TABLE runner_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at REAL NOT NULL                              -- seconds since 1970, by the server's clock
);
CREATE INDEX runner_runs_user ON runner_runs(user_id);
