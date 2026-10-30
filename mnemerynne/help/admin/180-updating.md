# Updating

```sh
sudo mnemerynne-admin check-updates     # what's new on GitHub and what an update would change
sudo mnemerynne-admin update            # asks first; --yes to skip the question
```

**Access → Settings → Updates** shows the same: whether a newer version exists, how many commits, whether it includes a database update (a backup is taken first) or changes service files (they restart), and how the last update went. The page can never start an update; that stays with a person at a shell.

## What `update` does

`deploy/linux/update.sh`, run as root:

1. Backs up the database to `<data_dir>/update/before-*.db` (the last five are kept).
2. Fast-forwards `/opt/mnemerynne` to the branch in `MNEMERYNNE_UPDATE_BRANCH` (`main`) from `MNEMERYNNE_UPDATE_REPO` or the checkout's origin. It refuses, changing nothing, if the checkout has local edits or commits GitHub doesn't have.
3. `uv sync --frozen --no-dev --compile-bytecode`, compile the application's Python files, and `restorecon`. Updates and rollbacks build bytecode caches as root because the services cannot write them under their read-only `/opt` directory.
4. Installs service files that changed (and the `mnemerynne-admin` command), then restarts what changed: the generator or embedder if their files did, then `mnemerynne-web` (which applies any database migration at start), and once it reports healthy, `mnemerynne-worker`.
5. If any step fails or the web app doesn't come back healthy, it undoes all of it: previous code and packages, previous service files, and the database backup if the schema had changed. Then it shows the end of the web app's log.

Keep your own changes to service files in drop-ins (`sudo systemctl edit mnemerynne-generator`): an update replaces the unit files in `/etc/systemd/system` but never touches drop-ins.

The web health check waits up to **300 seconds**, including during rollback. It prints the address it checks and progress every 30 seconds. It reads the running service's host and port, including systemd overrides, and bypasses HTTP proxies. A slower machine can set `MNEMERYNNE_UPDATE_HEALTH_TIMEOUT=600` in its service env file (1–3600 seconds; an environment variable passed to the updater takes precedence).

If an update fails, keep the printed health-check address and error along with `journalctl -u mnemerynne-web -n 100 --no-pager`. A service marked running may still be starting; `/healthz` must report `status: ok`. Updating the updater itself does not change the script already running: the deployment README's Updating section shows how to run the fetched script for that first update.

## Database migrations

Numbered SQL files in `mnemerynne/migrations/` are applied in order at start by whichever process starts first (`init-db` does it by hand). `/healthz` reports the schema version. A migration that recreates a table (for new CHECK constraints) runs with foreign keys off and copies every row; the update script's backup is the way back.

## Updating by hand (or the first time)

```sh
cd /opt/mnemerynne && sudo git pull --ff-only && sudo uv sync --frozen --no-dev --compile-bytecode
sudo .venv/bin/python -m compileall -q mnemerynne
sudo restorecon -R /opt/mnemerynne
sudo install -m 755 deploy/linux/mnemerynne-admin /usr/local/bin/
sudo systemctl restart mnemerynne-web mnemerynne-worker
```

## After an update

- Browsers fetch new scripts and styles automatically (the file's modification time is in the URL), so no cache clearing.
- Some versions add file types to search: `sudo mnemerynne-admin reclassify` makes files uploaded earlier searchable.
- A new embedding model needs `reembed`; new chunking needs `reindex`. The release notes say so.
- The Claude Desktop extension is versioned separately: re-pack and replace `mnemerynne.mcpb` only when its relay changed.
