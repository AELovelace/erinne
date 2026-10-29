# Mnemerynne Backup

Backs folders on this computer up to your **Backups** folder in Mnemerynne, one way:
`Users\<you>\Backups\<computer>\<label>` in your File box on the NAS. Windows and Linux.

- New and changed files are sent; a changed file's old copy is kept as a version.
- Files deleted here go to the recycle bin on the NAS, not away for good.
- Renamed and moved files and folders are moved on the NAS, not sent again.
- Files still being written wait for the next pass. Files another program has locked (Outlook's
  `.pst`), online-only cloud files, links, and names the NAS can't hold are skipped and listed.
- Works off site through the backup host (`bkp.beepsquad.wtf`); big files go up in 32 MB parts and
  carry on where they stopped after a dropped connection.
- Backups aren't searchable in Erynne unless you turn **Searchable** on for that folder.

The plan behind it: `docs/plans/desktop-sync-client.md` in the erinne repo. The server side is
`mnemerynne/sync.py` and `mnemerynne/web/sync.py`.

## Install

Windows: run the installer from Mnemerynne → Preferences → Backups. Linux: the AppImage from the same
page, or

```sh
uv tool install 'mnemerynne-backup[tray] @ git+https://github.com/aelovelace/erinne#subdirectory=clients/desktop-sync'
```

## Use

The tray icon (`mnemerynne-backup-tray`) has Back up now, Pause, Settings (folders, what to leave out,
Searchable, the upload limit), Restore and Sign out. The same from the command line:

```sh
mnemerynne-backup login https://beepsquad.wtf          # signs in in your browser
mnemerynne-backup login https://beepsquad.wtf --token  # or paste a sync token (headless machines)
mnemerynne-backup add ~/Documents
mnemerynne-backup add D:\Photos --label Photos --exclude "*.tmp" --exclude Thumbs
mnemerynne-backup sync                                 # one pass now (--check: compare with the NAS too)
mnemerynne-backup run                                  # keep going (what the tray and the service do)
mnemerynne-backup status
mnemerynne-backup searchable Documents on
mnemerynne-backup restore Documents --to ~/restored [--path Reports/2026]
mnemerynne-backup versions Documents Reports/q1.xlsx   # then restore --path … --version ID
mnemerynne-backup autostart on [--service]             # --service: a systemd user service, no desktop
```

Settings live in `%APPDATA%\Mnemerynne Backup` (Windows) or `~/.config/mnemerynne-backup`; what was
backed up last in `%LOCALAPPDATA%\Mnemerynne Backup` or `~/.local/state/mnemerynne-backup`. The
sign-in is kept in Credential Manager or the Secret Service keyring (a private file where there is
none). `MNEMERYNNE_BACKUP_HOME` puts all of it in one folder.

## Build

See `packaging/README.md`. Tests run with the server's: `uv run pytest tests/test_backup_client.py`
from the repo root.
