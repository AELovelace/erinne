# Plan: the desktop sync client

*Written 2026-10-29 by Claude for Doll; built the same day and merged in PR #1. It replaced the 2026-10-25
IT-OPS plan (Tauri/Rust, two-way sync of the File box): this one is Python, one-way, into `Backups`. The Status
section says what is built and what's left.*

**The idea:** a small app on a person's PC or laptop, on **Windows or Linux**. They add any number of folders (Desktop,
`D:\Photos`, `~/Projects`…) and the app keeps each one copied to a **Backups** folder in their File box on the NAS,
`\\mnemerynne.sadgirlsclub.local\share\Users\<username>\Backups`. It works on the office LAN and **off site**,
through `bkp.beepsquad.wtf`. It runs in the tray (or as a background service with no tray), starts at sign-in, and
shows what it's doing and anything it couldn't copy.

## Status (2026-10-29)

| Phase | State |
|---|---|
| 0 Server groundwork | Built (`fb9fa59`) |
| 1 App engine and command line | Built (`4cc7ee1`) |
| 2 Backup host, parts, Restore | Built (`4cc7ee1`); the proxy and DNS are Doll's |
| 3 Sign-in, tray app, packaging | Built (`de0fe54`); the 0.1.0 Windows installer is built (482ddf5) and on beepsquad.wtf; the AppImage builds with `PYTHON=/usr/bin/python3 packaging/build-appimage.sh` |
| 4 Searchable, Backups page, admin box, help, `sync-status` | Built; merged in PR #1. Running on beepsquad.wtf behind bkp.beepsquad.wtf (10.1.1.20 straight to the app, no Caddy) |

**Left:** the by-hand pilot checks below; locked files through shadow copies (stretch goal); Linux name mapping;
macOS.

**Testing on the dev NAS (beepsquad.wtf):**
1. Deploy the branch; migration 0057 runs on start.
2. Proxy and DNS per deploy README 5m; `MNEMERYNNE_BACKUP_URL=https://bkp.beepsquad.wtf`; restart web and worker.
3. Access → Settings → Desktop backup: on, and a group you're in.
4. On a laptop: `uv tool install './clients/desktop-sync[tray]'` (or the built AppImage/installer), then
   `mnemerynne-backup login https://beepsquad.wtf`, `mnemerynne-backup add ~/Documents`, `mnemerynne-backup sync -v`.
5. Then the by-hand checks under *Tests and checks* below, off site for the big-file one.

## Decisions (2026-10-29)

| Question | Answer |
|---|---|
| Direction | **One-way, PC → NAS** (a backup), with **Restore** in the app. Two-way sync is not planned. |
| Search | Backed-up files are **not indexed** unless the person turns on **Searchable** for that backed-up folder. |
| Stack | **Python**, so the same app runs on Windows and Linux. |
| Who gets it | Everyone in **a group the admin picks** (Access → Settings → Desktop backup). |
| Quota | **None** for now. |
| Locked files (Outlook `.pst`, running databases) | **Skipped and listed** for now. Shadow copies are a stretch goal: `docs/stretch-goals/backup-locked-files.md`. |
| Off site | **Yes**, laptops back up from anywhere, through **`bkp.beepsquad.wtf`**, a host of its own at the proxy with an nginx config made for big uploads. |
| Deletes on the PC | Moved to the **recycle bin** on the NAS, not deleted (kept `trash_days`, restorable from Files). |
| Changes on the PC | Overwrite, **keeping the old copy as a version** (`version_keep`, `version_days`), as edits through Claude do. |
| Transport | **HTTPS through Mnemerynne** (a new sync API), never SMB. It works off site and on non-domain and Linux machines, and the server checks permissions and writes atomically. |
| Layout on the NAS | `Backups\<Computer>\<Folder label>\…`, so two PCs with a `Desktop` don't collide and a reinstalled PC can pick its old folder up again. |

## What already exists (and what it means here)

| Piece | Where | Use for sync |
|---|---|---|
| File boxes on the share | `share.py`, `Users\<name>` (help: admin/80-share) | The destination already exists per person, with an ACL that only they and `mnemerynne` can open. |
| Atomic writes to the share | `share.py` (dot temp file + one rename) | Nobody, the scanner included, ever sees half a backed-up file. |
| Recycle bin | `fs.trash` / `.mnemerynne-trash` | Where files deleted on the PC go on the NAS. |
| Versions with optimistic concurrency | `editing.replace_content(expected=…)`, `file_versions` | Keeps the old copy when a backed-up file changes; `expected` catches a race. |
| Per-folder "hidden from Claude" | `fs.set_folder_claude` | Backups start hidden from Claude, like any folder can be. |
| Personal API tokens | `api_tokens.py`, `deps.api_user_or_token` | How the app acts as the person. Needs a new **sync** scope (below). |
| OAuth with PKCE and loopback redirects | `oauth.py` (`redirect_allowed`: `http://127.0.0.1`) | Lets the app sign in through the browser instead of pasting a token. |
| A second host served by the same app | the arcade (`MNEMERYNNE_ARCADE_URL`, README 5l) | The pattern for `bkp.beepsquad.wtf`: its own host, routed by `Host` before any page runs. |
| A shipped desktop client | `clients/claude-desktop` (internal CA in `ca.pem`, download from the Claude page) | Pattern for packaging and offering the download from Mnemerynne. |

**What's missing.** The file API can't sync as it stands:
- `POST /api/files` never overwrites: a changed file with a taken name becomes `name (2).ext`.
- There's no way to ask "what's in this folder tree, with hashes" in one call.
- `/api/files*` takes only a session cookie (`api_user`), not a token.
- Uploads are capped at `max_file_bytes` (500 MB), arrive in one request, and can't resume.
- Every upload is queued for ingestion. Thousands of backed-up files would flood the worker (OCR, embeddings).
- `editing._keep_version` holds the whole old file in memory.

## On the server

### 1. Who may back up
- **Access → Settings → Desktop backup** (admin): an on/off switch and **the group** whose members may back up, as with Games.
- `backups.may_backup(conn, user_id)` is checked when a sync token is issued **and on every sync call**, so taking
  someone out of the group stops their app within the AD snapshot's few minutes.
- The same box lists each person's computers, backed-up folders, size and last sync, and can revoke a computer.

### 2. A `sync` token scope
- `api_tokens` and OAuth grants gain `scope` (`mcp` | `sync`).
  - A sync token may only touch `Users\<me>\Backups\…`. It can't read other files, chat, or reach `/mcp`.
  - MCP tokens can't sync.
- Shows on **Preferences → Connected apps** as "Backup on DESKTOP-ABC, last seen 2 minutes ago", revocable there.
- Sign-in from the app: OAuth with PKCE and a loopback redirect (already allowed). The consent page says "Back up
  folders from this computer to your Backups folder".
- The browser sign-in uses `MNEMERYNNE_PUBLIC_URL` (where people's sessions are). It is reachable from outside with
  a Let's Encrypt certificate, so a new laptop can be set up anywhere. The token and every sync call then go to
  `bkp.beepsquad.wtf`.

### 3. The Backups folder
- `Users\<name>\Backups` is created on first use, inside the File box, so it inherits the File box's ACL.
- People without a File box on the share get a clear error ("Ask an admin to set up your File box"), not a fallback
  to the database store.
- **`folders.no_index`** is set on `Backups` and inherited by everything under it. While set, files are listed,
  previewed and downloadable in Files, but never queued for ingestion or searched.
- **Searchable:** a switch per backed-up folder, in the app and on Mnemerynne's **Backups** page (under
  Preferences).
  - Turning it on clears `no_index` for that folder and queues its files for ingestion, a few at a time so the
    worker isn't swamped.
  - Turning it off sets it again and removes those files' chunks and vectors.
  - Claude's access stays a separate switch (the existing one in Files), off by default for Backups, so "Searchable"
    means Erynne can search them; Claude needs that switch too.

### 4. The sync API (`mnemerynne/web/sync.py`, logic in `mnemerynne/sync.py`)

| Call | Does |
|---|---|
| `GET /api/sync/roots` | This person's backed-up folders: `{computer, label, folder_id, searchable, files, bytes, last_sync}`. |
| `POST /api/sync/roots` | Register `{computer, label}`; creates `Backups\<computer>\<label>`. Idempotent. |
| `PATCH /api/sync/roots/{id}` | Rename the label, turn **Searchable** on or off. |
| `GET /api/sync/roots/{id}/manifest?after=<cursor>` | Every live file and folder under the root: relative path, size, sha256, mtime, version. Paged by a cursor (a file id). It always lists everything; a feed of only what changed is for later, if full listings get slow. |
| `PUT /api/sync/roots/{id}/files?path=…` | Write one file (up to 32 MB) at that path. Headers `If-Match: <version>` (or `If-None-Match: *` for new), `X-Sha256`, `X-Mtime`. Returns the new version; `412` if the server's copy changed since. |
| `POST /api/sync/uploads` → `PUT …/{upload_id}/parts/{n}` → `POST …/{upload_id}/finish` | Resumable upload for bigger files, in **32 MB parts**, each with its own sha256. `GET …/{upload_id}` says which parts arrived, so a laptop that lost Wi-Fi carries on where it stopped. Parts are kept in `tmp_dir`, swept after a day. |
| `POST /api/sync/roots/{id}/delete` | Move a batch of paths to the recycle bin. |
| `POST /api/sync/roots/{id}/move` | Batch renames and moves (a renamed folder isn't re-uploaded). |
| `GET /api/sync/roots/{id}/files?path=…` | Download, for Restore, with Range requests. |
| `GET /api/sync/client` | The latest app version and download addresses (Windows `.exe`, Linux AppImage). |

- **Writes** go through the same path as other share writes (dot temp file, one rename, `files` row in the same
  transaction), so the share scan finds nothing to do afterwards.
- **Versions:** change `_keep_version` to move or copy the old file on disk instead of reading it into memory,
  before big files go through it.
- **Size:** `max_sync_file_bytes` (default 50 GB), separate from `max_file_bytes`.
- **Logging:** sign-ins, new roots, deletes and admin changes go in `access_log` (uploads don't: a first backup would be thousands of rows; `sync_roots.last_seen_at` shows activity). `mnemerynne-admin sync-status [USER]` prints the
  computers, folders and last sync.
- **Names:** the server enforces the share's Windows name rules (`share.py`) on every path, and refuses `..`,
  absolute paths, and anything that would land outside the root.

### 5. Migration 0057 (`0057_desktop_sync.sql`, reserved on the scratchpad)
- `api_tokens.scope`, `oauth_grants.scope`;
- `folders.no_index`;
- `sync_roots(id, user_id, computer, label, folder_id, searchable, created_at, last_seen_at)`;
- `sync_uploads(id, user_id, root_id, path, size, sha256, parts_received, created_at)`;
- `file_versions.via` gains `'sync'`.

The Backups switch and group are meta `backups_settings`, like Games', so they need no table.

### 6. `bkp.beepsquad.wtf`
A host of its own, so the big-upload settings don't loosen the main site:
- **DNS and certificate:** an A record to the proxy, Let's Encrypt at the proxy.
- **Settings:** `MNEMERYNNE_BACKUP_URL=https://bkp.beepsquad.wtf`. Like the arcade's host, the app checks it differs
  from `MNEMERYNNE_PUBLIC_URL` and refuses to start otherwise.
- **The app serves only sync there:** on that host every path except `/api/sync/*`, `/oauth/token` and
  `/oauth/revoke` answers 404, before any page runs. No sign-in page, no cookies, no admin. Sync calls on the
  main host keep working for PCs in the office.
- **The path a request takes:** the internet → **the reverse proxy at 10.1.1.20** (nginx, Let's Encrypt) →
  **Caddy on Mnemerynne's box** (443) → the app on `127.0.0.1:38200`.
  - Going through the box's Caddy, not straight to port 38200, keeps the app listening only on loopback, as it does
    now, and keeps `FORWARDED_ALLOW_IPS=127.0.0.1` as it is.
  - Caddy is told to trust `X-Forwarded-For` from 10.1.1.20 only, so rate limits and the access log still see the
    laptop's real address, not the proxy's.
- **The nginx config at 10.1.1.20** (new file `deploy/linux/nginx-bkp.conf`; the README gets a section like 5l):

  ```nginx
  # http {}: limit_req_zone $binary_remote_addr zone=bkp:10m rate=50r/s;
  server {
      listen 443 ssl;
      server_name bkp.beepsquad.wtf;
      # ssl_certificate / ssl_certificate_key: Let's Encrypt (certbot) on this proxy

      # One part (32 MiB) or one small file per request, plus headroom.
      client_max_body_size 40m;

      location ~ ^/(api/sync/|oauth/(token|revoke)$) {
          proxy_pass https://MNEMERYNNE-BOX;          # the box's Caddy, not port 38200
          proxy_ssl_server_name on;
          proxy_ssl_name bkp.beepsquad.wtf;
          proxy_ssl_verify on;
          proxy_ssl_trusted_certificate /etc/nginx/mnemerynne-caddy-root.crt;   # Caddy's internal CA
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_set_header Authorization $http_authorization;
          proxy_http_version 1.1;
          # Stream bodies to Mnemerynne instead of spooling each one to the proxy's disk first,
          # and stream downloads (Restore) back the same way.
          proxy_request_buffering off;
          proxy_buffering off;
          proxy_read_timeout 600s;
          proxy_send_timeout 600s;
          client_body_timeout 120s;
          limit_req zone=bkp burst=200 nodelay;
      }
      location / { return 404; }
  }
  ```

  Parts keep each request small, so a dropped connection costs at most 32 MB, and no single upload has to beat a
  timeout.
- **The block in the box's Caddyfile** (added to `Caddyfile-mnemerynne` and `Caddyfile-mnemerynne-public`):

  ```caddy
  {
      servers {
          trusted_proxies static 10.1.1.20/32   # the reverse proxy: believe its X-Forwarded-For
      }
  }

  bkp.beepsquad.wtf {
      tls internal                    # the proxy checks this against Caddy's root certificate
      @not_proxy not remote_ip 10.1.1.20
      respond @not_proxy 403          # only the proxy talks to this host
      request_body {
          max_size 40MiB
      }
      header -Server
      reverse_proxy 127.0.0.1:38200 {
          flush_interval -1
      }
  }
  ```

  Caddy then hands the app the laptop's address as `X-Forwarded-For`, from 127.0.0.1, which the app already trusts.
  A test from 10.1.1.20 checks it: an `access_log` row for a sync call shows the outside address.

## The app (`clients/desktop-sync/`, Python 3.14)

### Parts
- **`engine/`**: pure logic, no UI, fully unit-tested:
  - scanning a folder;
  - the local state DB (path, size, mtime, sha256, server version);
  - working out what to upload, move or delete;
  - name checks (shared with `share.py`'s `_BAD_CHARS` and `_RESERVED`);
  - retries and backoff.
- **`transport.py`**: the sync API over HTTPS (`httpx`), always through `bkp.beepsquad.wtf`, on the LAN and off
  site alike. Its Let's Encrypt certificate is trusted everywhere, so the app bundles no CA.
- **`app/`**: the tray icon (`pystray`), a small settings window (Tk), notifications.
- **`mnemerynne-backup` command line**: `sync`, `status`, `add`, `remove`, `restore`, so it can also run headless
  (a Linux box with no desktop, or a systemd user service).

### Per platform

| | Windows | Linux |
|---|---|---|
| Package | One `.exe` (PyInstaller), per-user install, no admin rights | AppImage; also `uv tool install mnemerynne-backup` |
| Starts at sign-in | Run key under HKCU | XDG autostart `.desktop`, or `systemctl --user enable mnemerynne-backup` for headless |
| Token kept in | Credential Manager (`keyring`) | Secret Service (`keyring`); a `0600` file if there's no keyring (headless) |
| State DB | `%LOCALAPPDATA%\Mnemerynne Backup\state.db` | `~/.local/state/mnemerynne-backup/state.db` |
| Watching | `watchdog` (ReadDirectoryChangesW) | `watchdog` (inotify). If it runs out of watches (`fs.inotify.max_user_watches`), it says so and falls back to rescans |
| Tray | Always there | Where the desktop has one (KDE, most others; GNOME needs the AppIndicator extension). Without one, the settings window opens from the app menu |
| Computer name | `COMPUTERNAME` | `hostname` |

### Names that don't fit the share (mostly Linux)
Linux allows names the share can't hold: `:`, `?`, `*`, `"`, `<`, `>`, `|` and `\`, trailing dots or spaces, reserved
names like `CON`, and two names that differ only in case (`Notes.txt` and `notes.txt`). In the first version these are
**skipped and listed** in the app, with the reason, and never silently renamed. Mapping them to look-alike characters
that come back unchanged on restore (as Samba's `catia` does for Macs) is in the later list.

### How a sync runs
1. **Watch:** file events mark paths dirty. There's also a full rescan every hour, on start and on waking from sleep
   (events get missed).
2. **Settle:** a file changed in the last 30 s waits, as `SHARE_SETTLE_SECONDS` does on the server.
3. **Compare** with the state DB by size and mtime; hash only what changed. A same-hash file that disappeared in one
   place and appeared in another is a **move**.
4. **Push:** moves first, then uploads (small files first, 4 at a time), then deletes. Each write carries
   `If-Match`. On `412`, re-read the manifest for that path and retry once, then report it.
5. **Check the server** every so often: compare its manifest with the state DB, to catch anything changed or
   deleted on the NAS by hand, and re-upload what's missing.
6. **Offline:** no network means the app waits and says "Offline, N files waiting". It never treats an unreachable
   server as "everything deleted".

### Files it skips
- Office lock files, `Thumbs.db`, `desktop.ini`, `.DS_Store`, `.tmp`/`.part`/`.crdownload` (same list as
  `share.py`).
- **Files Windows has locked** (Outlook `.pst`, running databases): retried at the next pass and listed as "in use,
  not backed up".
- Cloud placeholders (OneDrive "online-only") aren't downloaded just to back them up.
- Symlinks and junctions aren't followed.
- The exclusions set for that folder.

### Settings window
- **Folders:** the list, with **Add folder…**, a label for each, and per folder: Pause, Remove, **Searchable**, and
  exclusions (glob patterns, with `node_modules`, `.git` and `__pycache__` offered as ticks).
- **Network:** a bandwidth limit, and "pause on metered connections" (Windows reports metered; on Linux,
  NetworkManager does).
- **Restore…:** pick a backed-up folder (or a file and one of its versions) and a place to put it. It never
  overwrites without asking.
- **Removing a folder** stops syncing it. The copy on the NAS stays until the person deletes it in Files.

### Updates
The app asks `GET /api/sync/client` once a day and offers the new version. The Windows `.exe` is code-signed if we
have a certificate, as with the `.mcpb`.

## Phases

| Phase | What | Size |
|---|---|---|
| 0 | Server groundwork: token scope, Backups switch and group, migration, `no_index`, Backups folder creation, `roots`, `manifest`, `PUT`, `delete`, `move`, streaming versions, tests | 2–3 evenings |
| 1 | App engine and the `mnemerynne-backup` command line (no UI): one folder, full scan, upload/move/delete, state DB. Pilot on aedith's PC and one Linux machine. | 2 evenings |
| 2 | `bkp.beepsquad.wtf`: host routing in the app, `nginx-bkp.conf` and the Let's Encrypt certificate on 10.1.1.20, the Caddy block on the box, DNS; resumable uploads in parts; Restore. Pilot a laptop off site. (Until this phase, the Phase 1 pilot talks to the main address on the LAN.) | 2 evenings |
| 3 | Tray app and settings window on both platforms: folder list, watcher, OAuth sign-in, keyring, notifications, start at sign-in, the `.exe` and the AppImage | 3 evenings |
| 4 | **Searchable** switch (ingestion queued gently, chunks removed when turned off), the Backups page under Preferences, the admin box, help pages, `sync-status`, update check | 1–2 evenings |
| later | Locked files through shadow copies (stretch goal), round-trip mapping for Linux names, macOS | — |

## Tests and checks
- **Server (pytest):**
  - only the chosen group can get a sync token or sync, and removal from the group stops an issued token;
  - a sync token can't leave `Backups\` (`..`, absolute paths, other users' ids) or reach `/mcp`, and an MCP token
    can't sync;
  - on `bkp.beepsquad.wtf`'s host, everything but `/api/sync/*` and `/oauth/token|revoke` is 404;
  - `If-Match` conflicts return `412`, and an overwrite keeps a version without reading the old file into memory;
  - deletes land in the recycle bin, and a move keeps the file id (and its versions);
  - `no_index` files are never queued or searched; Searchable on queues them, off removes their chunks;
  - resumable uploads survive a missing part, refuse a part with the wrong hash, and are swept when abandoned;
  - the share scan after a sync write changes nothing.
- **App (pytest, no network, run on Windows and Linux):** the engine against a fake server:
  - rename and move detection;
  - settle time;
  - names the share can't hold, including case-only collisions;
  - locked files;
  - a lost state DB (rebuilt from the manifest without re-uploading what matches);
  - going offline mid-upload;
  - clock skew.
- **By hand (pilot):**
  - back up a real Desktop and Documents on Windows, and a home folder on Linux;
  - rename a folder of 1,000 files and check it's one move;
  - upload a 5 GB file from a laptop off site through `bkp.beepsquad.wtf`, drop the Wi-Fi halfway, and check it
    resumes;
  - restore a deleted file from Files;
  - check `getfacl` on `Backups\` matches the File box.

## Settled later (2026-10-29)
1. **The reverse proxy** for `bkp.beepsquad.wtf` is **10.1.1.20**. It forwards to the box's Caddy (section 6).
2. **Signing in off site** works: `MNEMERYNNE_PUBLIC_URL` is public, with a Let's Encrypt certificate.
3. **Protection of the copies:** the share sits on RAID 5, which is enough for this. No separate backup of
   `Backups\` is planned.

Nothing is open; Phase 0 can start.
