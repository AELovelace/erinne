# Mnemerynne as one Python application per feature

## Context

Mnemerynne runs as one Python process (`mnemerynne serve`, ~50k lines, 34 web routers). Python runs one
thread's Python code at a time, so on the NAS (i7-2600, 8 threads, 8 GB) the whole app had about one core:
on 2026-11-13 the probes found 23 busy threads queued behind each other, 117 connections waiting in the
listen queue, and audio streams 20 s from their first byte. Today's fixes (caches, load shedding, a backup
and a games process behind an nginx front door on the box) made it snappier; the box is still bound by one
process for everything else, and by one SQLite file (one write lock) for every write.

Goal: each feature runs as its own Python application (its own process, its own cores, its own worker
count), behind nginx, with **write-parallelism from one SQLite file per feature** and **live cross-process
events over Valkey**, carved out **one feature at a time** so the box keeps working throughout. Target
hardware later: Ryzen 9 5900X, 32 GB — the same layout with more workers.

Decisions made (2026-11-13):
- **Writes:** a SQLite file per feature (each its own write lock: features write in parallel). Not a single
  writer process: ~650 write sites read-then-write in Python inside transactions; one writer would own all
  that logic and run every write on one core again.
- **Events:** Valkey (Redis-compatible, Fedora package `valkey`) pub-sub, keys with expiry for presence.
- **Rollout:** strangler pattern — one service per phase, old in-app route kept until the new one is proven.

## Architecture

### What a "service" is

- One **shared package** (`mnemerynne`): domain modules (fs, share, permissions, rooms, media, lumi…) stay
  shared code. Services don't call each other over HTTP; they share the database files and talk through
  Valkey events. A modular monolith deployed as many processes, not separate repos.
- One **entry point per service**: `mnemerynne serve --service <name>` builds a FastAPI app from the
  service table (`mnemerynne/services/registry.py`: routers, lifespan hooks, owned DB file, worker count,
  port, single-process or not). This generalises today's `create_role_app` + `_guard` in
  `mnemerynne/web/app.py` and `sync_app`/`games_app` factories.
- **All-in-one mode stays**: `mnemerynne serve` with no service mounts every router in one process with an
  in-process event bus. Tests, development on Windows, and small installs use it; nothing else changes for them.
- Every service gets the same front: `_guard` (session cookie with the shared key, CSRF, limits, headers,
  backup-host fence, `SlowRequestLog`, JSON 503 for a busy database), `/healthz` with its name, the
  journal logging setup (`_role_logging`), and `get_db` (async, with `release_db`).

### Services

| Service | Serves (paths) | DB file it writes | Processes | Port |
|---|---|---|---|---|
| nginx (front door) | routing, `/static/` directly, file bytes via `X-Accel-Redirect`, rate limits | – | nginx workers | 38200 |
| `portal` | sign-in, shell pages, nav, Settings/Admin, themes, help, messages, notices, scratchpad | `core.db` (users, auth, settings) | 2 | 38210 |
| `files` | `/api/browse`, `/api/files*`, `/api/collections/*`, `/api/locations`, editor, comments, versions, downloads, wiki, name search | `core.db` (files/folders) | 3 | 38203 |
| `share` | no public routes: the share watcher, permission walks, the share scan | `share-rights.db`, `core.db` (folders) | 1 | 38211 (health) |
| `search` (ingest worker) | today's `mnemerynne-worker`: extraction, chunks, embeddings | `search.db` (chunks, vectors, FTS) | 1–2 | – |
| `assistant` | `/api/ask` (streaming), conversations, agent, memories, feedback, proposals | `assistant.db` (+ LangGraph checkpoints/store) | 2 | 38204 |
| `mcp` | `/mcp` (already stateless JSON), connector tools | `logs.db` (mcp_calls) | 2 | 38205 |
| `rooms` | `/api/rooms*` + its live stream | `rooms.db` | 2 | 38206 |
| `media` | Media browse/play, HLS sessions, TV/speaker commands + streams, radio, SoundCloud, signage | `media.db` | 1 (ffmpeg sessions) | 38207 |
| `games` | `/api/media/games*` (exists since 4332b9f) | `games.db` | 1 | 38202 |
| `sync` | `/api/sync/*` (exists since 4332b9f) | `sync.db` (roots, uploads), `core.db` (files) | 3 | 38201 |
| `automations` | scheduler + runs, `/api/automations*` | `automations.db` | 1 (scheduler) | 38208 |
| `remote` | `/remote/*` LAN-app proxy (websockets) | – | 1 | 38209 |

Single-process by nature (state that can't be shared): `share` (one watcher), `automations` (one
scheduler), `media` (ffmpeg session manager; HLS segments must reach the process that started them),
`games` (party/table hubs; may go multi-process later once presence/hubs move to Valkey).

### Database files (SQLite, WAL)

Grouping from the 97 tables (references checked in `mnemerynne/migrations/*.sql`):

| File | Tables |
|---|---|
| `core.db` | users, groups, group_members, collections, collection_grants, folders, folder_grants, folder_versions, files, blobs, blob_units, file_comments, file_versions, jobs, meta, themes, api_tokens, oauth_*, user_claude, user_messages, announcements*, m365_*, entra_*, sharepoint_*, claude_links |
| `search.db` | chunks + sqlite-vec table + FTS5 (written by the ingest worker; chunk bursts no longer hold core's lock) |
| `assistant.db` | conversations, messages, message_sources, feedback, feedback_archive, agent_calls, agent_proposals, graph_runs, LangGraph checkpoints and store |
| `rooms.db` | chat_rooms, chat_members, chat_messages, chat_events, chat_mentions, chat_reactions, chat_attachments |
| `media.db` | media_*, signage_* |
| `games.db` | games, lumi_*, lumisoft_*, coin_accounts, coin_transactions, arcade_*, casino_rooms, prism_runs, runner_runs, push_subscriptions |
| `sync.db` | sync_roots, sync_uploads |
| `automations.db` | automations, automation_runs, automation_feeds, automation_destinations, scratchpad_* (if the scratchpad stays with the agents) |
| `logs.db` | access_log, mcp_calls, api_calls, agent_calls (append-only, high volume) + apis, api_* |
| `share-rights.db` | exists (cache) |

Mechanics (new in `mnemerynne/db.py`):
- `db.connect(feature)` opens the feature's file and `ATTACH`es `core.db` **read-only**
  (`file:...?mode=ro`, uri=True) as `core`. SQLite finds unqualified names in attached files, so queries
  like `JOIN users` keep working unchanged; a stray write to a core table fails loudly instead of
  splitting a transaction across files (WAL keeps each file atomic, not the pair).
- Per-file migrations: `mnemerynne/migrations/<file>/NNNN_*.sql`, each file with its own `user_version`;
  today's 75 become `core`'s history. One **split migration per phase** moves that feature's tables:
  backup, `ATTACH`, `INSERT … SELECT`, count check, drop from core.
- Foreign keys to `users`/`files`/`folders` across files (81 to users, 27 to files, 12 to folders) aren't
  enforced by SQLite any more: user and file deletion publish an event (`user.deleted`, `files.deleted`)
  and each service deletes its rows; a nightly `mnemerynne check-orphans` repairs anything missed.
  `users.delete_user` (`mnemerynne/users.py`) becomes the publisher.
- Folder caches keep working: `db.folder_rows` / `db.folder_derived` (version-checked by triggers) are
  already process-safe.
- Backups: `mnemerynne/backup.py` backs up every file with the SQLite backup API, core first;
  restore restores the set. `update.sh` snapshots all of them.

### Live events (Valkey)

New `mnemerynne/events.py`: `publish(channel, data)`, `subscribe(*channels)` (async), presence keys with
TTL, counters with TTL. Backends: Valkey (`MNEMERYNNE_VALKEY_URL`, unix socket on the box) or in-process
(all-in-one mode, tests). What moves onto it:

| Today (in one process) | Becomes |
|---|---|
| `rooms_bus.Bus` wake + 5 s DB poll (`mnemerynne/rooms_bus.py`) | `rooms:{room}` channel; the DB stays the record |
| TV/speaker command `screens.Hub` (`mnemerynne/media/screens.py`, `web/media.py hub()`) | `screen:{id}` channel; the media service holds the SSE |
| share watcher updates (`share.apply_changes`) | `share:rights` (library, folders) → services reload those folders from `share-rights.db` |
| theme changes (`app.state.looks`, `web/theme.py`) | `theme:changed` → reload |
| `LoginThrottle` (in memory), `security.PublicRateLimit`/`RateLimiter` | Valkey counters with TTL (or nginx `limit_req` for the public limits) |
| `lobby.PRESENCE`, `lumiparty.HUB`, `showdown.HUB` | stay in the games process first; Valkey later if games go multi-process |
| nav badges (unread messages, mentions) | `user:{id}` channel for live badges; counts stay DB queries |
| `diagnostics.install_log_capture` (per-process ring) | each service's journal (`journalctl -u mnemerynne@*`); the `diagnose` tool reads the journal or a Valkey stream |
| user/file deletion cascades | `user.deleted`, `files.deleted` |

### nginx

Generalise `deploy/linux/nginx-mnemerynne-front.conf` (+ `nginx-mnemerynne.conf`, Caddyfiles for other
boxes) from the service table:
- path prefix → service upstream (keepalive), streaming settings per route (SSE, websockets, uploads).
- `/static/` served by nginx from disk (no Python at all).
- **`X-Accel-Redirect` for file bytes**: the app checks access and answers with an internal path; nginx
  sends the file with sendfile and Range. Covers the 18 `FileResponse` sites (files, media streams and
  HLS segments, rooms attachments, sync downloads, theme assets, backup client downloads). Audio/video
  and downloads stop using Python threads entirely. Needs internal locations aliasing the share, the
  data dir and the media cache, and SELinux read access for nginx to them.
- Public rate limits move to `limit_req` (Valkey for per-user limits).

### Deployment

- One systemd **template** `deploy/linux/mnemerynne@.service` (`%i` = service), hardening as today
  (`LimitNOFILE`, `ProtectSystem`, `ReadWritePaths`), a per-service env file
  `/etc/mnemerynne/services/%i.env` for port/workers; drop-ins apply to all instances.
  `mnemerynne-sync`/`mnemerynne-games` units fold into it.
- `deploy/linux/front-door.sh` grows `services on|off|status` and writes the nginx config from the table.
- `update.sh` migrates every DB file (from one process, before restarts), then restarts services in
  order (share → files → others → portal), checking each `/healthz`.
- Two worker profiles in the registry: **nas** (8 GB) and **ryzen** (32 GB). Heavy imports (langgraph,
  agent, transcode) load lazily so lean services stay small; measure RSS per service in Phase 0.

## Phases (one feature at a time)

Each phase: build the service → split migration for its tables → move its in-memory cross-process state
to events → nginx route → soak with the old in-app route still mounted → remove it from the portal. Measure
before/after with `python/bench_*.py`, `python/memprobe_sample.py` per service, and the slow-request log.

0. **Foundations.** Service registry + factory (from `create_role_app`/`_guard`), `events.py` with both
   backends, Valkey on the box, multi-file `db.connect`/`migrate`/backup, deletion events, the systemd
   template, front-door generator, all-in-one mode kept. Baseline measurements. No behaviour change.
1. **nginx offloads.** `/static/` and `X-Accel-Redirect` for all file bytes; `limit_req`. (Fixes the
   set-top box's 20 s audio starts at the root.)
2. **Games** (process exists): `games.db` split, coins included; deletion events; drop the games router
   from the portal.
3. **Rooms:** `rooms.db`, rooms service with its stream over Valkey; Erynne's room answers move to the
   assistant service and publish to `rooms:{room}`.
4. **Media:** `media.db` (+ signage), media service with HLS sessions and the TV/speaker hub over Valkey,
   radio/SoundCloud relays.
5. **Share:** the share service owns the watcher, walks and the scan; it publishes `share:rights`; the
   others only read `share-rights.db` (no service walks on its own any more).
6. **Files + search:** files service (3 workers); `search.db` split with the ingest worker as its writer.
7. **Assistant:** `assistant.db` (+ LangGraph files), `/api/ask`, agent, memories; one shared balancer
   poller via Valkey-held node health instead of one per process.
8. **MCP:** stateless, 2 workers; `logs.db` for mcp_calls/api_calls/agent_calls/access_log.
9. **Automations:** scheduler singleton, `automations.db`.
10. **Remote** proxy service; `sync.db` split for the existing backup service.
11. **Portal slims down** to pages, sign-in, admin, settings; everything else served by its service.

## Critical files

- `mnemerynne/web/app.py` — `create_app`, `create_role_app`, `_guard` → `mnemerynne/services/` (registry,
  factory, per-service lifespans); keep `create_app` as all-in-one.
- `mnemerynne/cli.py` — `serve --service <name>`, `check-orphans`, `migrate --all`.
- `mnemerynne/db.py` — feature files, read-only core attach, per-file migrations, `folder_rows` unchanged.
- `mnemerynne/migrations/` → `migrations/<file>/`, plus one split migration per phase.
- `mnemerynne/backup.py`, `deploy/linux/update.sh`, `deploy/linux/setup.sh` — all DB files.
- `mnemerynne/users.py` (`delete_user`), file deletion in `mnemerynne/fs.py` — deletion events.
- `mnemerynne/rooms_bus.py`, `mnemerynne/media/screens.py`, `mnemerynne/share.py` (`apply_changes`,
  watcher), `mnemerynne/web/theme.py`, `mnemerynne/web/security.py`, `mnemerynne/diagnostics.py` — events.
- Every `FileResponse` site in `mnemerynne/web/{files,media,rooms,sync,theme,tokens,games}.py` — X-Accel.
- `deploy/linux/mnemerynne@.service`, `nginx-mnemerynne-front.conf`, `front-door.sh`, README, and the admin
  help pages under `mnemerynne/help/admin/` (keep the wiki/help current with each phase).

## Verification

- **Every phase:** the full suite in all-in-one mode (`.venv\Scripts\python -m pytest tests`); service
  tests in the style of `tests/test_roles.py` (each service serves only its paths, accepts the portal's
  sign-in, answers like all-in-one); split-migration tests (row counts, read-only core attach refuses
  writes, deletion events clean every file); event tests against the in-process bus plus one against a
  real Valkey where available.
- **Load:** `python/bench_files.py`, `python/bench_manifest.py`, a new multi-process load script hitting
  the front door concurrently (Files + uploads + backup manifests + game polls + an audio stream), before
  and after; on the box, `memprobe_sample.py` per service and
  `journalctl -u 'mnemerynne@*' | grep "Slow request"`.
- **On the box after each phase:** `front-door.sh status`, every `/healthz`, the listen-queue column in
  `ss -ltn` near 0, audio on the set-top box starting at once (X-Accel), real client addresses in logs.
