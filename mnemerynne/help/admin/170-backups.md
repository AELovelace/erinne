# Backups, restore and retention

## What the backup holds

`mnemerynne backup` (nightly at 02:30 through `mnemerynne-backup.timer`, keeping 14 days) writes to `MNEMERYNNE_BACKUP_DIR`:

| Folder | Contents |
|---|---|
| `db/` | One consistent database snapshot per day (SQLite's backup API, safe while the app runs): users, libraries, grants, the search index, conversations, memories, tokens, settings. |
| `config/YYYY-MM-DD/` | That day's copy of the settings file (it holds the secret key), plus `router.json`, `synonyms.txt` and `mnemerynne.mcpb`. |
| `blobs/` | Uploaded files stored inside Mnemerynne; content-addressed and never modified, so only new ones are copied each night. |
| `versions/` | Earlier versions of edited files, copied the same way. |

Everything is readable only by `mnemerynne` (folders 0700, files 0600): a backup holds every document and the key to every stored credential.

**Not included**: files on the SMB share (back the share up with the NAS) and pending proposals' content (short-lived).

## Where to put it

On a disk that outlives the boot drive, such as the NAS's storage array, and **never inside the share**: the backup refuses, because people could read the copied secret key there.

```sh
sudo install -d -o mnemerynne -g mnemerynne -m 700 /srv/storage/backups/mnemerynne
echo 'MNEMERYNNE_BACKUP_DIR=/srv/storage/backups/mnemerynne' | sudo tee -a /etc/mnemerynne/mnemerynne.env
sudo restorecon -R /srv/storage/backups
sudo systemctl start mnemerynne-backup.service && journalctl -u mnemerynne-backup -n 5 --no-pager
```

The unit may write anywhere under `/srv`; elsewhere add `ReadWritePaths=` in a drop-in. Copy the backups off the box as well.

## The secret key

`MNEMERYNNE_SECRET_KEY` encrypts stored API credentials, Microsoft 365 links, the balancer key and people's Claude keys. A backup restored without the matching key keeps every document, but those secrets are lost and must be entered or linked again. The backup copies the settings file, but keep the key off the box too (the password manager):

```sh
sudo grep '^MNEMERYNNE_SECRET_KEY=' /etc/mnemerynne/mnemerynne.env
```

## Restoring

1. Stop the services; the restore refuses to run while they're up: `sudo systemctl stop mnemerynne-web mnemerynne-worker`.
2. `sudo mnemerynne-admin restore --list`, then `sudo mnemerynne-admin restore --date YYYY-MM-DD --force` (leave out `--date` for the newest; `--from FOLDER` reads a copy brought back from off the box). `--force` is only needed when a database already exists; it is moved to `<data_dir>/pre-restore-<time>/`, not deleted. The database is integrity-checked before and after. Blobs, versions, `router.json`, `synonyms.txt` and `mnemerynne.mcpb` are copied back.
3. Read what it says about the secret key. It never writes to `/etc`. **matches**: credentials will work. **differs** (a rebuilt box): put the backed-up settings file back with the `install` command it prints, or set the key from your off-box copy.
4. Start the services.

**On a new box**: install the code and settings file, restore, then set up the services. Skip the MicroJames import; the restored database has the users and documents.

## Retention (the worker, hourly)

| What | Kept | Setting |
|---|---|---|
| Recycle-bin items | 30 days | `MNEMERYNNE_TRASH_DAYS` (`purge-trash` does it now) |
| Earlier file versions | 30 days, 20 per file | `MNEMERYNNE_VERSION_DAYS`, `VERSION_KEEP` |
| Pending proposals | 7 days, then expired | `MNEMERYNNE_PROPOSAL_DAYS` |
| Claude's tool calls | 90 days | `MNEMERYNNE_MCP_CALLS_DAYS` |
| Internal API calls (with responses) | 90 days | `MNEMERYNNE_API_CALLS_DAYS` |
| Erynne agent tool calls | 90 days | `MNEMERYNNE_AGENT_CALLS_DAYS` |
| Answer timings | 30 days | `MNEMERYNNE_GRAPH_RUNS_DAYS` |
| Unused Claude connections | 30 days | `MNEMERYNNE_OAUTH_CLIENT_DAYS` |

`0` keeps a log forever. Once an API call is deleted, older answers that cite it lose their "API call" link. Memories and conversations are kept until the person deletes them (or `forget-memories`).
