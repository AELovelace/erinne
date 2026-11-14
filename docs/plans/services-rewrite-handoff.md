# Services rewrite: handoff (paused 2026-11-13)

Everything needed to pick up the rewrite (`docs/plans/services-rewrite.md`) on another machine.

## 1. Getting the code ready

```sh
git pull                          # main; last pushed work is listed in the plan's Progress table
uv sync                           # new since this morning: redis==8.1.0 (runtime), fakeredis==2.40.0 (dev)
.venv\Scripts\python -m pytest tests -n auto      # Windows; ~4 min. Linux: .venv/bin/python
```

Expected on Windows: everything passes except 4 tests in `tests/test_backup_client.py`
(symlinks, `:` in file names, chmod bits, Linux AppImage). They fail on Windows only; ignore them.
Valkey tests run against fakeredis always; set `MNEMERYNNE_TEST_VALKEY_URL=redis://...` to also test a real one.

Uncommitted in the working tree and **not part of this work**: `lumi-codex/` (staged). Leave it alone.

## 2. What's live on the NAS right now

Deployed: everything up to the front door (commit 56d49c3). Nothing from Phase 0 is deployed yet.

| Piece | Where | Notes |
|---|---|---|
| nginx front door | `192.168.1.5:38200` (`/etc/nginx/conf.d/mnemerynne-front.conf`) | site-chan still sends everything here; the firewall only lets 38200 through |
| web app | `127.0.0.1:38210` (`mnemerynne-web`) | `.env`: `MNEMERYNNE_HOST=127.0.0.1`, `MNEMERYNNE_PORT=38210`, `FORWARDED_ALLOW_IPS=127.0.0.1,...` |
| backup API | `127.0.0.1:38201`, 3 processes (`mnemerynne-sync`) | drop-in `mnemerynne-sync.service.d/home-nas.conf` |
| games API | `127.0.0.1:38202` (`mnemerynne-games`) | drop-in `mnemerynne-games.service.d/home-nas.conf` |
| the .env before the front door | `/etc/mnemerynne/mnemerynne.env.before-front-door` | `front-door.sh off` puts it back |

`sudo bash /opt/mnemerynne/deploy/linux/front-door.sh status` shows all of it.

### Deploying Phase 0 (safe whenever you like)

`sudo mnemerynne-admin update --yes`. What it changes on the box:
- the two units' `ExecStart` becomes `serve --service sync|games` (update.sh installs changed units itself);
- services now **wait** for the web app's migrations instead of migrating (closes a start-up race);
- `share-rights.db`-style feature files: none exist yet (`db.FEATURE_DBS` is empty), so no data moves;
- events stay in-process (no Valkey yet): each service logs "no Valkey ... events stay in this process". Expected.
- the web app sweeps orphan feature rows at start (nothing to sweep yet).

### Still to do on the NAS (independent of the rewrite)

1. **Bin moves fail with "Invalid cross-device link"** (the backup app's deletions retry every few minutes).
   Cause: `home-nas.conf` lists three separate `ReadWritePaths`, which systemd mounts separately. Fix:
   ```sh
   for u in web sync games; do sudo sed -i 's#^ReadWritePaths=.*#ReadWritePaths=/srv/share#' /etc/systemd/system/mnemerynne-$u.service.d/home-nas.conf; done
   sudo systemctl daemon-reload && sudo systemctl restart mnemerynne-web mnemerynne-sync mnemerynne-games
   ```
2. Optional memory cap (was discussed, not applied): `MemoryHigh=2G`, `MemoryMax=3G` in a `mnemerynne-web.service.d/memory.conf`.
3. Valkey: not needed until a phase uses events (Phase 3 Rooms at the latest). `valkey-setup.sh` is ready for it.

### Deploying Phase 0.5 (after Phase 0; never run on the box yet)

Do item 1 above (the `ReadWritePaths` fix) first, so the drop-in copied to the template is the fixed one.

```sh
sudo mnemerynne-admin update --yes
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services     # sync and games move to mnemerynne@
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh status
# Only when a phase needs live events between processes (or to try it now; `off` undoes it):
sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh on
sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh status
```

`services` stops at the first service that doesn't answer and puts its old unit back; the ones before it stay
on `mnemerynne@`. Rolling a moved one back by hand: `systemctl disable --now mnemerynne@sync && systemctl enable
--now mnemerynne-sync`.

### Deploying Phase 1 (after 0.5; never run on the box yet)

```sh
sudo mnemerynne-admin update --yes
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh accel on      # B
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh status        # "accel: on (nginx has its locations)"
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh accel off     # back to A at any time
```

If `accel on` stops at a folder nginx can't read, it puts nginx back by itself; `sudo ausearch -m avc -c nginx
-ts recent` shows an SELinux denial. Compare A and B with the set-top box's audio start, a film seek, a big
download, and `journalctl -u mnemerynne-web | grep "Slow request"`.

### Deploying Phase 2 (games.db; never run on the box yet)

`sudo mnemerynne-admin update --yes`. The web app's start copies the 18 games tables into
`/var/lib/mnemerynne/games.db` (rows counted on both sides) and drops them from core; the log says
"Moved into games.db: coin_accounts (N rows), ...". The updater's snapshot (`update/before-*.db` and its
`.feature.*` files) is taken first. Check: coins and Lumi look the same, a Lumi party plays, a casino table
deals, reminders still arrive.

**Back to A** (the code before Phase 2), if it misbehaves:

```sh
sudo systemctl stop mnemerynne-web mnemerynne-worker 'mnemerynne@*' mnemerynne-sync mnemerynne-games
sudo mnemerynne-admin move-back games                                               # rows back into core, games.db set aside
sudo git -C /opt/mnemerynne checkout 1267fc9                                       # Phase 1's code
sudo systemctl start mnemerynne-web mnemerynne-worker                               # then the services
```

`move-back` refuses while the services run. To return to B: `sudo git -C /opt/mnemerynne checkout main` and
`sudo mnemerynne-admin update --yes`; its start moves the rows out again.

### Deploying Phase 3 (rooms.db and the rooms service; never run on the box yet)

`sudo mnemerynne-admin update --yes` moves Rooms' tables into `/var/lib/mnemerynne/rooms.db` at the web app's
start ("Moved into rooms.db: ..."). Nothing else changes until Valkey is on; Rooms keeps working from the web app.
Then, for B:

```sh
sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh on
sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services    # starts mnemerynne@rooms, routes /api/rooms to it
```

Check: two people in a channel see each other's messages and typing at once, @here reaches both, @Erynne answers,
an automation posts into a room, Claude's `post_to_room` and `read_room` work. Back to A for the service only:
`valkey-setup.sh off` then `front-door.sh services` (rooms off, its paths back to the web app). Back to the code
before Phase 3: as for games, `sudo mnemerynne-admin move-back rooms` with everything stopped, then the older code.

### Deploying Phase 4a (media.db; never run on the box yet)

`sudo mnemerynne-admin update --yes`: the web app's start moves Media's 13 tables into
`/var/lib/mnemerynne/media.db` ("Moved into media.db: ..."). Nothing else changes: the web app still serves Media.
Check: films, TV, music and pictures list and play; TVs and remotes pair and take commands; signage shows play;
the next share scan's catalog pass adds a new file and drops a deleted one; TV guests and Who's playing? still work
(the games read TVs and remotes from media.db now). Back to A: `sudo mnemerynne-admin move-back media` with
everything stopped, then the older code.

### Deploying Phase 4b (the media service; never run on the box yet)

After the update, `sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services` starts `mnemerynne@media` and
routes Media's paths to it (no Valkey needed). Check: the Media page and a TV's page draw as before (theme,
top bar); a film plays and seeks (an HLS one too); a TV pairs and takes a remote's commands; a phone pairs as a
remote; signage commands reach a screen; Settings → Media saves; Lumi's game pages still open (they stay with the
web app). Back to A: `MNEMERYNNE_SERVICES_OFF=media` in the .env and `front-door.sh services` again.

### Deploying Phase 5 (the share service; never run on the box yet)

After the update, `sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services` starts `mnemerynne@share`, sets
`MNEMERYNNE_SHARE_WALKS=service` and restarts the app's units: from then on only the share service walks the share's
permissions (and watches it). Best with Valkey on (requests for walks and change notices travel by it); without it
the others re-read share-rights.db every 30 s and walk a never-walked library themselves after 20 s. Check: Files
lists libraries as before; a permission changed on the share shows within seconds; `journalctl -u mnemerynne@share`
shows the walks; the web app's log has no walks of its own. Back to A: `MNEMERYNNE_SERVICES_OFF=share` and
`front-door.sh services` (it clears the setting and restarts the app).

### Deploying Phase 6a (search.db; never run on the box yet)

`sudo mnemerynne-admin update --yes`: the first of the web app and the worker to start moves search's index into
`/var/lib/mnemerynne/search.db` (the other waits on `mnemerynne.db.migrate.lock`): "Moved into search.db: chunks
(N rows), chunk_vec (N rows)". The keyword index is rebuilt there as the rows arrive, so that first start can take a
few minutes on the box's ~100k chunks; the web app answers once it's done. Check: Ask and the MCP `search_documents`
find what they did; a file moved into a folder bob can't read stops showing in his searches at once; Claude-hide
takes a file out of Claude's answers; uploading a new PDF makes it searchable; Settings → Diagnose on a file shows
its chunks and vectors. Back to A: everything stopped, `sudo mnemerynne-admin move-back search` (chunks, vectors,
keyword index and triggers back into core; search.db set aside), then the older code.

## 3. Phase 0.5: done in code (2026-11-13)

The front-door and Valkey work below is committed and tested with the box stood in for
(`tests/test_front_door.py`); it hasn't run on the NAS. Next is **Phase 1** (end of this section).

Done and tested first (committed in "Services rewrite, phase 0 step 5 (paused)"):
- `deploy/linux/mnemerynne@.service`: one template unit for every service (`mnemerynne@sync`, ...).
  Drop-ins in `/etc/systemd/system/mnemerynne@.service.d/` apply to all of them.
- `mnemerynne/services/nginx.py`: `python -m mnemerynne.services.nginx --listen 192.168.1.5:38200` prints the
  front-door config from the registry (an upstream per service sized to its processes, a location per path,
  `/remote/` websockets, everything else to the web app).
- `update.sh` `service_units`: restarts/stops the old named units **and** enabled `mnemerynne@` instances
  (disabled ones stay off); used in the normal restart and in undo. Test: `test_update_restarts_only_...`.
- `backup.running_services` also sees running `mnemerynne@` instances.

Then done 2026-11-13, as listed here (kept as the record of what each piece was asked to do; `valkey-setup.sh`
also got `off`, and `services` checks that `/healthz` answers with the service's own role, not just "ok"):
1. **`deploy/linux/front-door.sh`**:
   - `on`: replace the `sed ... "$SRC" > "$CONF"` line with the generator:
     `(cd /opt/mnemerynne && .venv/bin/python -m mnemerynne.services.nginx --listen "$HOST:$PORT") > "$CONF"`.
   - `running()` and `status`: include `mnemerynne@*.service` instances (`systemctl list-units --plain ...`).
   - new **`services`** subcommand: install `mnemerynne@.service` into `/etc/systemd/system`; copy
     `mnemerynne-web.service.d/*.conf` into `mnemerynne@.service.d/` (`cp -n`); `daemon-reload`; for each name in
     `registry.SERVICES` (get them with `.venv/bin/python -c "from mnemerynne.services import registry; ..."`):
     disable `mnemerynne-<name>` if enabled, `enable --now mnemerynne@<name>`, wait for
     `http://127.0.0.1:<port>/healthz`; **if it fails, disable the instance and re-enable the old unit** and stop.
     Then, if the front door is on (`$ENV.before-front-door` exists), regenerate nginx with the listen address
     **from that saved file** (the live `.env` says 127.0.0.1:38210), `nginx -t`, `systemctl reload nginx`.
   - Long lines get cut when pasted into the NAS terminal: keep everything in scripts, run them by path.
2. **`deploy/linux/valkey-setup.sh on|status`**: `dnf install -y valkey`; in `/etc/valkey/valkey.conf` set
   `unixsocket /run/valkey/valkey.sock`, `unixsocketperm 770`, `port 0`; `usermod -aG valkey mnemerynne`;
   `systemctl enable --now valkey`; set `MNEMERYNNE_VALKEY_URL=unix:///run/valkey/valkey.sock` in the .env;
   check with `runuser -u mnemerynne -- /opt/mnemerynne/.venv/bin/python -c "import redis; print(redis.Redis(unix_socket_path='/run/valkey/valkey.sock').ping())"`;
   restart the web app and services. Fedora's package paths are from memory: make the script check and say so.
3. **README** (`deploy/linux/README.md`): sections for the template unit / `front-door.sh services`, and Valkey.
   Also the admin help pages under `mnemerynne/help/admin/` if they describe deployment.
4. **Tests**: `tests/test_roles.py` or a new file: the nginx renderer (each service's paths go to its upstream,
   the catch-all and `/remote/` go to the web app, `keepalive` follows the process count).
5. Full suite, update the Progress table in the plan, commit, push.

Then **Phase 1** (biggest felt win next): nginx serves `/static/` from disk and file bytes via
`X-Accel-Redirect` (the 18 `FileResponse` sites in `mnemerynne/web/{files,media,rooms,sync,theme,tokens,games}.py`).
Audio/video and downloads stop using Python threads. Needs internal nginx locations aliasing the share, the data
dir and the media cache, plus SELinux read access for nginx to them; keep `FileResponse` as the fallback when the
app runs without the front door (all-in-one, tests).

## 4. Rules the new code relies on

- **Feature files** (`db.FEATURE_DBS`): a feature's tables live in `<data dir>/<name>.db` with migrations in
  `mnemerynne/migrations/<name>/`. They must **not** declare `REFERENCES` to core tables (users, files, folders):
  SQLite can't follow a reference into another file. Declare those columns in `FeatureDb.user_columns` /
  `file_columns` instead; `forget_user` and `sweep_orphans` clean them.
- A feature connection (`db.connect_feature`) attaches core **read-only**. Never make it writable: `BEGIN
  IMMEDIATE` would then take core's write lock too (tested: `test_a_feature_write_does_not_hold_cores_write_lock`).
- Only the web app (and the CLI) migrate (`db.migrate_all`); services call `db.wait_migrated`.
- Moving tables out of core: `db.move_tables` (copy, count, then drop; redo-safe). A phase adds its `FeatureDb`
  with `moves=(...)` and a migration creating those tables without cross-file references.
- **Services**: one row in `mnemerynne/services/registry.py` (routers, paths, port, workers per profile, single,
  hooks). `mnemerynne serve --service <name>`. The web app keeps serving every path (all-in-one) until the phase
  removes it.
- **Events** (`mnemerynne/events.py`): JSON on channels; `publish_sync` from worker threads; the DB stays the
  record, an event only says "look now".
- `get_db` must stay an **async** generator (see `mnemerynne/web/deps.py`); long-lived streams call
  `release_db(request)` before returning.

## 5. Tools for measuring on the NAS

All read-only, run while it's slow (the probes inject into the live app with Python 3.14 `sys.remote_exec`):

```sh
cd /opt/mnemerynne && sudo git -c safe.directory=/opt/mnemerynne fetch -q origin
for p in memprobe memprobe_tasks memprobe_threads memprobe_sample; do sudo git -c safe.directory=/opt/mnemerynne show origin/main:python/$p.py | sudo install -o mnemerynne -m 644 /dev/stdin /var/lib/mnemerynne/$p.py; done
sudo /opt/mnemerynne/.venv/bin/python -c "import sys; sys.remote_exec($(systemctl show -p MainPID --value mnemerynne-web), '/var/lib/mnemerynne/memprobe_sample.py')"
sleep 30; sudo cat /var/lib/mnemerynne/memprobe_sample.txt | head -30          # a 20 s CPU profile
sudo journalctl -u mnemerynne-web --since "-5min" --no-pager | grep "Slow request" | awk '{print $(NF-6), $(NF-5), $(NF-3)}' | sort | uniq -c | sort -rn | head
sudo ss -ltn | grep 3820                                                        # Recv-Q on LISTEN: connections waiting
```

Benchmarks on the dev machine: `python/bench_files.py`, `python/bench_manifest.py`, `python/bench_reach.py`.
