-- Automator (mnemerynne/automations.py): jobs a person schedules (a morning briefing, a security digest)
-- that Erynne, Claude with the person's key, or a Claude routine runs with the person's access, and
-- whose answer goes to their Inbox, a room, an email draft or a webhook. On/off and who may use it are
-- meta settings (automations_enabled, automations_group).

CREATE TABLE automations (
    id                INTEGER PRIMARY KEY,
    owner_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    schedule          TEXT NOT NULL,                       -- JSON, automations.check_schedule
    tz                TEXT NOT NULL DEFAULT 'UTC',
    runner            TEXT NOT NULL DEFAULT 'erynne' CHECK (runner IN ('erynne', 'claude_key', 'claude_routine')),
    tools             TEXT NOT NULL DEFAULT '[]',          -- JSON list of tool names; [] = every reading tool
    may_propose       INTEGER NOT NULL DEFAULT 0 CHECK (may_propose IN (0, 1)),
    deliver           TEXT NOT NULL DEFAULT '{"inbox": true}',   -- JSON: inbox, room_id, email_draft, destinations
    routine_url       TEXT NOT NULL DEFAULT '',
    routine_token_enc BLOB,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    paused_reason     TEXT NOT NULL DEFAULT '',
    next_run_at       TEXT,                                -- UTC, 'YYYY-MM-DD HH:MM:SS'; NULL while off
    last_run_id       INTEGER,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX automations_due ON automations(enabled, next_run_at);
CREATE INDEX automations_owner ON automations(owner_id);

CREATE TABLE automation_runs (
    id                 INTEGER PRIMARY KEY,
    automation_id      INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    trigger            TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'mcp')),
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'waiting', 'done', 'quiet', 'failed', 'timed_out')),
    started_at         TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    output             TEXT NOT NULL DEFAULT '',
    sources            TEXT NOT NULL DEFAULT '[]',
    error              TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    claude_session_url TEXT NOT NULL DEFAULT '',
    delivered          TEXT NOT NULL DEFAULT '[]'          -- JSON: where it went, and what failed
);
CREATE INDEX automation_runs_of ON automation_runs(automation_id, id);
CREATE INDEX automation_runs_status ON automation_runs(status);

-- Webhooks results may go to: admins make them, members pick one (no member-typed outbound addresses).
CREATE TABLE automation_destinations (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    url        TEXT NOT NULL,
    format     TEXT NOT NULL DEFAULT 'json' CHECK (format IN ('json', 'teams', 'slack')),
    secret_enc BLOB,
    group_ref  TEXT NOT NULL DEFAULT '',                   -- who may pick it; '' = everyone who may use Automator
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Outside security feeds (vendor advisories, CISA KEV, NVD) the security_feeds tool reads; admins only.
CREATE TABLE automation_feeds (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    url        TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('rss', 'atom', 'json_kev', 'nvd')),
    keywords   TEXT NOT NULL DEFAULT '',                   -- products to watch, comma-separated; '' = everything
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    fetched_at TEXT,
    cache      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
