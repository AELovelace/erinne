# Plan: the desktop sync client

*Written 2026-10-29 by Claude for Doll. A plan: nothing here is built yet. Decisions marked **(proposed)**
are defaults to argue with; the open questions at the end need an answer before Phase 1.*

**The idea:** a small app on a person's PC. They add any number of folders (Desktop, `D:\Photos`,
a project folder…) and the app keeps each one copied to a **Backups** folder in their File box on
the NAS, `\\mnemerynne.sadgirlsclub.local\share\Users\<username>\Backups`. It runs in the tray,
starts with Windows, and shows what it's doing and anything it couldn't copy.

## What already exists (and what it means here)

| Piece | Where | Use for sync |
|---|---|---|
| File boxes on the share | `share.py`, `Users\<name>` (help: admin/80-share) | The destination already exists per person, with an ACL that only they and `mnemerynne` can open. |
| Atomic writes to the share | `share.py` (dot temp file + one rename) | Nobody, the scanner included, ever sees half a backed-up file. |
| The share scan | worker, every `SHARE_SCAN_SECONDS` | Picks up files the client writes over SMB, if we go that way (see Transport). |
| Recycle bin | `fs.trash` / `.mnemerynne-trash` | Where files deleted on the PC go on the NAS, so a sync never destroys anything outright. |
| Versions with optimistic concurrency | `editing.replace_content(expected=…)`, `file_versions`, `version_keep` | Keeps the old copy when a backed-up file changes; `expected` catches two PCs racing. |
| Personal API tokens | `api_tokens.py`, `deps.api_user_or_token` | How the app acts as the person. Needs a new **sync** scope (below). |
| OAuth with PKCE and loopback redirects | `oauth.py` (`redirect_allowed`: `http://127.0.0.1`) | Lets the app sign in through the browser instead of pasting a token. |
| A shipped desktop client | `clients/claude-desktop` (internal CA in `ca.pem`, download from the Claude page) | Pattern for packaging, trusting the internal CA and offering the download from Mnemerynne. |

**What's missing.** The file API can't sync as it stands:
- `POST /api/files` never overwrites: a changed file with a taken name becomes `name (2).ext`.
- There's no way to ask "what's in this folder tree, with hashes" in one call.
- `/api/files*` takes only a session cookie (`api_user`), not a token.
- Uploads are capped at `max_file_bytes` (500 MB) and arrive in one request, with no resuming.
- Every upload is queued for ingestion. Thousands of backed-up files would flood the worker (OCR, embeddings),
  and would put someone's whole Desktop into Erynne's and Claude's search.

## Decisions (proposed)

| Question | Proposal | Why |
|---|---|---|
| Direction | **One-way, PC → NAS** (a mirror), with **Restore** in the app. | "Backups" says one-way. Two-way sync means conflict handling on every file and can spread a mistake (or ransomware) from the NAS to every PC. |
| Deletes on the PC | Moved to the **recycle bin** on the NAS, not deleted. | Kept `trash_days`, restorable from Files. A wiped folder can always come back. |
| Changes on the PC | Overwrite, **keeping the old copy as a version** (`version_keep`, `version_days`). | Same as edits through Claude today; previous versions show in Files. |
| Transport | **HTTPS through Mnemerynne** (a new sync API), not SMB. | Works off the LAN and on non-domain PCs, needs no drive mapping, and the server checks permissions and writes atomically. SMB would only work on domain PCs on site and bypass the app's checks. |
| Layout on the NAS | `Backups\<Computer>\<Folder label>\…` | Two PCs with a `Desktop` don't collide; a reinstalled PC can pick its old folder up again. |
| Search | The Backups folder is **not indexed** (no chunks, no embeddings) and **hidden from Claude** by default; a person can turn indexing on per backed-up folder. | Keeps the worker free and search clean. Files still list, preview and download in Files. |
| Client stack | **Python 3.14** (the repo's language), packaged with PyInstaller into one signed `.exe`, tray via `pystray`, file events via `watchdog`, state in SQLite. | Shares name rules and tests with the server; no new language to maintain. (Alternatives under open questions.) |
| Platforms | **Windows first**. macOS/Linux later, same code. | That's where the staff are. |

## Shape of it

### On the server

**1. A `sync` token scope.**
- `api_tokens` and OAuth grants gain `scope` (`mcp` | `sync`). A sync token may only touch
  `Users\<me>\Backups\…`; it can't read other files, chat, or reach `/mcp`. MCP tokens can't sync.
- Shows on **Preferences → Connected apps** as "Backup on DESKTOP-ABC, last seen 2 minutes ago", revocable there.
- Sign-in from the app: OAuth with PKCE, loopback redirect (already allowed), consent page says
  "Back up folders from this computer to your Backups folder".

**2. Backups folder setup.** `Users\<name>\Backups` is created on first use, inside the existing File box, so it
inherits the File box's ACL (only the person and `mnemerynne`). A flag on the folder (`no_index`) is set at
creation and inherited by everything under it (see Search above). People without a File box on the share get a
clear error ("Ask an admin to set up your File box"), not a fallback to the database store.

**3. The sync API** (`mnemerynne/web/sync.py`, logic in `mnemerynne/sync.py`):

| Call | Does |
|---|---|
| `GET /api/sync/roots` | This person's backed-up roots: `{computer, label, folder_id, files, bytes, last_sync}`. |
| `POST /api/sync/roots` | Register `{computer, label}`; creates `Backups\<computer>\<label>`. Idempotent. |
| `GET /api/sync/roots/{id}/manifest?after=<cursor>` | Every live file and folder under the root: relative path, size, sha256, mtime, version. Paged, with a cursor so later calls only return changes. |
| `PUT /api/sync/roots/{id}/files?path=…` | Write one file at that path. Headers `If-Match: <version>` (or `If-None-Match: *` for new files), `X-Sha256`, `X-Mtime`. Small files in one request. Returns the new version; `412` if the server's copy changed since. |
| `POST /api/sync/uploads` → `PATCH …/{upload_id}` → `POST …/{upload_id}/finish` | Resumable upload in 8 MB parts for big files, then the same write as `PUT`. Parts kept in `tmp_dir`, swept after a day. |
| `POST /api/sync/roots/{id}/delete` | Move a batch of paths to the recycle bin. |
| `POST /api/sync/roots/{id}/move` | Batch renames/moves (so a renamed folder isn't re-uploaded). |
| `GET /api/sync/roots/{id}/files?path=…` | Download, for Restore. Range requests for big files. |

- Writes go through the same path as other share writes (dot temp file, one rename, `files` row in the same
  transaction), so the share scan sees nothing new to do.
- Overwrites keep the old copy as a version. `editing._keep_version` holds the whole file in memory: change it to
  stream (a rename or copy on disk) before big files go through it.
- Size: sync gets its own `max_sync_file_bytes` (default 10 GB), separate from `max_file_bytes`.
- **Quota:** `sync_quota_bytes` per person (0 = none). The app shows usage; `507` when it's full.
- Every call is logged in `access_log` with the token id; `mnemerynne-admin` gains `sync-status [USER]`.

**4. Migration 0057 or later** (check the scratchpad board first):
- `api_tokens.scope`, `oauth_grants.scope`;
- `folders.no_index`;
- `sync_roots(id, user_id, computer, label, folder_id, created_at, last_seen_at)`;
- `sync_uploads(id, user_id, root_id, path, size, sha256, received, created_at)`.

**5. Admin.** An **Access → Backups** box: on/off switch, who may use it (a group, like Games), quota, max file
size, and a per-person table of computers, roots, size and last sync. Help pages: `help/user/…-desktop-backup.md`,
`help/admin/…-desktop-backup.md`.

### The app (`clients/desktop-sync/`)

**Parts:**
- `engine/`: pure logic, no UI, fully unit-tested:
  - scanning a folder;
  - the local state DB (`%LOCALAPPDATA%\Mnemerynne Backup\state.db`: path, size, mtime, sha256, server version);
  - working out what to upload, move or delete;
  - Windows name rules (shared with `share.py`'s `_BAD_CHARS` and `_RESERVED`);
  - retries.
- `transport.py`: the sync API over HTTPS. It trusts the internal CA (`ca.pem`, as the Claude Desktop relay does)
  and keeps tokens in Windows Credential Manager (`keyring`).
- `app/`: the tray icon, a small settings window (Tk is enough), notifications.

**How a sync runs:**
1. **Watch:** `watchdog` events mark paths dirty, plus a full rescan every hour and on start (events get missed:
   sleep, network drives, too many at once).
2. **Settle:** a file changed in the last 30 s waits, as `SHARE_SETTLE_SECONDS` does on the server.
3. **Compare** with the state DB by size and mtime; hash only what changed. A same-hash file that disappeared in one
   place and appeared in another is a **move**.
4. **Push:** moves first, then uploads (small files first, 4 at a time), then deletes. Each write carries
   `If-Match`; on `412`, re-read the manifest for that path and retry once, then report it.
5. Every so often, **check the server's manifest** against the state DB, to catch anything changed or deleted on
   the NAS by hand, and re-upload what's missing.

**Files Windows won't let us read:**
- Office lock files, `Thumbs.db`, `desktop.ini`, `.tmp`/`.part` are skipped (same list as `share.py`).
- Files open with an exclusive lock (Outlook `.pst`, a running database) are retried later and listed as "in use".
  A later phase can use a **Volume Shadow Copy** snapshot for these (needs the app's elevated helper; see open questions).
- Cloud placeholders (OneDrive "online-only") are skipped, not downloaded.
- Symlinks and junctions aren't followed.

**Settings window:**
- the folder list, with **Add folder…**, a label per folder, and Pause/Remove;
- exclusions per folder (glob patterns; `node_modules`, `.git` offered as ticks);
- a bandwidth limit, and "only on unmetered networks";
- **Restore…**: pick a backed-up folder (or a file and one of its versions) and a place to put it. It never
  overwrites without asking.

**Removing a folder** stops syncing it. The copy on the NAS stays until the person deletes it in Files.

**Install and updates:**
- A per-user install (no admin rights), starting at sign-in.
- The **Backups** page in Mnemerynne offers the download and is where the app checks for updates
  (`GET /api/sync/client` → version and URL).
- The `.exe` is code-signed if we have a certificate, as with the `.mcpb`.

## Phases

| Phase | What | Size |
|---|---|---|
| 0 | Server groundwork: token scope, migration, `no_index`, Backups folder creation, `roots` + `manifest` + `PUT` + `delete` + `move`, streaming versions, tests | 2–3 evenings |
| 1 | App engine and a command-line `mnemerynne-backup sync` (no UI): one folder, full scan, upload/move/delete, state DB. Pilot on aedith's PC. | 2 evenings |
| 2 | Tray app: folder list, watcher, OAuth sign-in, Credential Manager, notifications, start at sign-in, installer | 2–3 evenings |
| 3 | Resumable big-file uploads, bandwidth limit, quota, Restore | 2 evenings |
| 4 | Admin box, help pages, `sync-status`, the download page and update check | 1 evening |
| later | Shadow copies for locked files, macOS/Linux, two-way sync (if ever), opting a folder into search | — |

## Tests and checks
- **Server (pytest):**
  - a sync token can't leave `Backups\` (path traversal, `..`, absolute paths, other users' ids) or reach `/mcp`;
  - an MCP token can't sync;
  - `If-Match` conflicts return `412`, and an overwrite keeps a version;
  - deletes land in the recycle bin;
  - a move keeps the file id (and so its versions);
  - `no_index` files are never queued for ingestion and never searched;
  - the quota and size limits;
  - resumable uploads survive a dropped part and are swept when abandoned;
  - the share scan after a sync write changes nothing.
- **App (pytest, no network):** the engine against a fake server:
  - rename and move detection;
  - settle time;
  - Windows name rules;
  - locked files;
  - a state DB that was deleted (it rebuilds from the manifest without re-uploading what matches);
  - clock skew.
- **By hand (pilot):**
  - back up a real Desktop and Documents;
  - rename a folder of 1,000 files and check it's one move;
  - pull the network cable mid-upload;
  - sleep the laptop;
  - restore a deleted file from Files;
  - check `getfacl` on `Backups\` matches the File box.

## Open questions for Doll
1. **One-way backup or two-way sync?** The proposal is one-way with Restore. If you want the same folder live on
   two PCs (edit at home, see it at work), that's two-way, and it's a much bigger job.
2. **Should backed-up files be searchable** by Erynne and Claude? Proposed: off by default, opt-in per folder.
3. **Stack:** Python + PyInstaller (proposed), or something with a nicer native feel (.NET/WinUI, or Tauri)? Python
   keeps one language and shared rules; .NET would make shadow copies and the tray more natural on Windows.
4. **Who gets it?** Everyone with a File box on the share, or a group to start with?
5. **Quota:** a limit per person? The NAS's free space is the real limit, and the share isn't in Mnemerynne's own
   backups, so whatever lands in `Backups\` has exactly as much protection as the NAS's backups. Is that enough
   for people's only copy of their Desktop?
6. **Locked files (Outlook PSTs and similar):** worth an elevated helper for shadow copies, or skip them and say so?
7. **Off-site use:** will laptops back up from home? If so, Mnemerynne's public address (Caddyfile-mnemerynne-public)
   needs to allow `/api/sync/*`, and big uploads through it.
