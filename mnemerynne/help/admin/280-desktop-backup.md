# Desktop backup

People install **Mnemerynne Backup** (Windows or Linux) and pick folders on their computer. The app copies each one, one way, to `Users\<name>\Backups\<computer>\<label>` in their File box on the share (see [The SMB share](share.md)). Code: `mnemerynne/sync.py`, `mnemerynne/web/sync.py`, and the app in `clients/desktop-sync`.

## Turning it on

1. **The backup host.** Laptops send through `MNEMERYNNE_BACKUP_URL` (e.g. `https://bkp.beepsquad.wtf`), a host of its own whose proxy takes big uploads: the reverse proxy at 10.1.1.20 runs `deploy/linux/nginx-bkp.conf`, and the box's Caddy gets `Caddyfile-mnemerynne-backup`. On that host only `/api/sync/*` and `/oauth/token|revoke` answer. Steps: deploy README, section 5m. Without it the app uses the main address and its upload limit.
2. **Access → Settings → Desktop backup.** Switch it on and name the group (AD or Entra, separated by `;`) whose members may back up. The check runs on every request, so someone taken out of the group stops at once; switching Backups off stops every app.
3. **The app.** Build it (`clients/desktop-sync/packaging/README.md`) and copy the installer (`Mnemerynne-Backup-Setup-<version>.exe`) and AppImage (`Mnemerynne_Backup-<version>-x86_64.AppImage`) into `<data dir>/backup-client/`. Preferences → Backups offers the newest of each, and running apps see a new version within a day.

## How it behaves

- **Sign-in:** in the browser (OAuth, scope `sync`, only for an app on the person's own computer), or a sync token made on Preferences → Backups for computers without a browser. Sync sign-ins only work on `/api/sync/*`, and Claude's tokens don't work there.
- **What it may do:** add, overwrite, move and delete inside the person's own Backups folder. Overwrites need the version the app last saw (If-Match), and keep the old copy as a version (not for files over `MNEMERYNNE_SYNC_VERSION_MAX_BYTES`, 2 GB). Deletes go to the recycle bin.
- **Search:** Backups and each new backed-up folder are marked *not indexed*: files are listed and previewed but never read for search, and they're hidden from Claude. A person can make one Searchable.
- **Sizes:** whole files up to 32 MB per request; bigger ones in 32 MB parts that resume after a dropped connection, up to `MNEMERYNNE_MAX_SYNC_FILE_BYTES` (50 GB). Unfinished uploads are swept after a day. There's no quota.
- **Logging:** sign-ins, new backed-up folders, deletes and Searchable changes go in the log (Access → Recent changes); single uploads don't.

## Checking

- Access → Settings → Desktop backup lists every backed-up folder: whose, which computer, files, size and the last sync.
- `sudo mnemerynne-admin sync-status [USER]` prints the same.
- On a laptop: `mnemerynne-backup status` and `mnemerynne-backup sync -v` show what the last pass did and every skipped file with its reason; the app's log is `backup.log` in `%LOCALAPPDATA%\Mnemerynne Backup` or `~/.local/state/mnemerynne-backup`.

## Protecting the copies

The backups live on the share, which Mnemerynne's own backup doesn't cover (see [Backups](backups.md)): they're as safe as the NAS. Locked files (Outlook's `.pst`) are skipped for now; backing them up through shadow copies is a stretch goal (`docs/stretch-goals/backup-locked-files.md`).
