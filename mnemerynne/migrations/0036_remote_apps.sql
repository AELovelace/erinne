-- Remote apps (mnemerynne/remote.py): LAN-only web apps (the balancer page, Cockpit, Wazuh, a NAS)
-- reached through Mnemerynne at /remote/<slug>/..., behind its sign-in, for the group an admin
-- picks (empty: admins only). Configured on Access → Remote. The certificate columns work like an
-- API's (apis): a pinned PEM for self-signed servers.

CREATE TABLE remote_apps (
    id                 INTEGER PRIMARY KEY,
    slug               TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- the address: /remote/<slug>/
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    base_url           TEXT NOT NULL,                          -- http(s)://host[:port][/base], no trailing slash
    group_ref          TEXT NOT NULL DEFAULT '',               -- group_refs; '' = admins only
    ca_pem             TEXT NOT NULL DEFAULT '',
    tls_check_hostname INTEGER NOT NULL DEFAULT 1 CHECK (tls_check_hostname IN (0, 1)),
    rewrite_html       INTEGER NOT NULL DEFAULT 0 CHECK (rewrite_html IN (0, 1)),   -- fix absolute links in pages
    embed              INTEGER NOT NULL DEFAULT 1 CHECK (embed IN (0, 1)),          -- in the Remote window, else a new tab
    enabled            INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
