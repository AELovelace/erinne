-- T-21 security feeds (mnemerynne/security_feeds.py): default feeds that ship with Mnemerynne, the products we watch,
-- one merged item per CVE across sources, each feed's health, and the Wazuh push (once per item).
--
-- automation_feeds is rebuilt (SQLite can't widen a CHECK): more kinds (msrc, github, nginx, mozilla, html), a stable
-- `key` for the defaults Mnemerynne seeds (security_feeds.DEFAULT_FEEDS), and the polling/health columns
-- feed_status reads. Existing feeds keep their ids, names, addresses, kinds and keywords.

CREATE TABLE automation_feeds_new (
    id                INTEGER PRIMARY KEY,
    key               TEXT,                                -- a default feed's key ('cisa-kev', 'nvd', ...); NULL = an admin's own
    name              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    url               TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('rss', 'atom', 'json_kev', 'nvd', 'msrc', 'github', 'nginx',
                                                    'mozilla', 'html')),
    keywords          TEXT NOT NULL DEFAULT '',            -- products to watch, comma-separated; '' = everything
    products          TEXT NOT NULL DEFAULT '[]',          -- JSON list of security_products keys this feed speaks for
    note              TEXT NOT NULL DEFAULT '',            -- shown on the admin page (why it's off, what it needs)
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    interval_seconds  INTEGER NOT NULL DEFAULT 86400,      -- how often it's polled (NVD 2 h, KEV 6 h, vendors daily)
    secret_enc        BLOB,                                -- an API key (NVD's), encrypted with MNEMERYNNE_SECRET_KEY
    fetched_at        TEXT,                                -- legacy: the last time anything was fetched
    cache             TEXT NOT NULL DEFAULT '',            -- legacy RSS cache (kept for admins' own feeds)
    last_attempt      TEXT,                                -- UTC ISO 'YYYY-MM-DDTHH:MM:SSZ'
    last_success      TEXT,
    error             TEXT NOT NULL DEFAULT '',            -- '' when the last attempt worked
    items_last_fetch  INTEGER NOT NULL DEFAULT 0,
    failures          INTEGER NOT NULL DEFAULT 0,          -- in a row; drives the backoff
    backoff_until     TEXT,                                -- after a 403/429 (or repeated failures): not before this
    cursor            TEXT NOT NULL DEFAULT '',            -- NVD: the end of the last modified-date window fetched
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO automation_feeds_new (id, name, url, kind, keywords, enabled, fetched_at, cache, created_at)
    SELECT id, name, url, kind, keywords, enabled, fetched_at, cache, created_at FROM automation_feeds;
DROP TABLE automation_feeds;
ALTER TABLE automation_feeds_new RENAME TO automation_feeds;
CREATE UNIQUE INDEX automation_feeds_key ON automation_feeds(key) WHERE key IS NOT NULL;

-- The products we run and how a feed item is matched to them: CPE 2.3 prefixes (NVD), keywords (titles, KEV's
-- vendor/product), and the package names Wazuh's inventory lists them under (the affects_us join).
CREATE TABLE security_products (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,                       -- 'git', 'nginx', ...
    name       TEXT NOT NULL,                              -- what an item's `product` says
    vendor     TEXT NOT NULL DEFAULT '',
    cpes       TEXT NOT NULL DEFAULT '[]',                 -- JSON list of CPE 2.3 prefixes; a trailing * matches any suffix
    keywords   TEXT NOT NULL DEFAULT '[]',                 -- JSON list, matched case-insensitively as whole words
    packages   TEXT NOT NULL DEFAULT '[]',                 -- JSON list of Wazuh package-name patterns (* wildcards)
    note       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per CVE (or per advisory without one: key 'url:<link>'), merged across every source that mentioned it.
-- `data` is the merged item (security_feeds.ITEM_FIELDS, without affects_us, which is joined when read).
CREATE TABLE security_items (
    key             TEXT PRIMARY KEY,
    data            TEXT NOT NULL,
    activity_at     TEXT NOT NULL,                         -- the newest date a source gave it (KEV's dateAdded, published)
    first_seen      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    wazuh_pushed_at TEXT                                   -- set when its line went to the Wazuh log: never twice
);
CREATE INDEX security_items_activity ON security_items(activity_at);

-- What each source said about an item, so merging is redone from all of them whenever one changes.
CREATE TABLE security_sightings (
    item_key   TEXT NOT NULL REFERENCES security_items(key) ON DELETE CASCADE,
    source     TEXT NOT NULL,                              -- 'kev', 'nvd', 'msrc', 'github', 'nginx', 'mozilla', ...
    feed_id    INTEGER,
    data       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (item_key, source)
);

-- The defaults Mnemerynne has put in once ('feed:<key>', 'product:<key>'): one an admin deleted stays deleted.
CREATE TABLE security_defaults_seeded (
    key       TEXT PRIMARY KEY,
    seeded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
