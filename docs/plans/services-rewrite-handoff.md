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
