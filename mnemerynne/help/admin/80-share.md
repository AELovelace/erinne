# The SMB share

With `MNEMERYNNE_SHARE_ROOT` set, the organisation's file share becomes the home of libraries and File boxes: every **top-level folder of the share is a library**, and `Users\<username>` is that person's File box. The share is the source of truth: what people add, rename, move or delete through `\\server\share` shows up in Files within a minute, and what they do in Files happens on the share at once.

## Layout

```
/srv/storage/share/                 MNEMERYNNE_SHARE_ROOT
├── .mnemerynne-share               marker: the share is mounted (scans only run while it exists)
├── Brand Standards/                a library
├── IT-OPS/                         a library
├── Wiki/                           the organisation wiki (a library like any other)
└── Users/                          MNEMERYNNE_USERS_FOLDER
    ├── edith/                      Edith's File box (private to her domain account)
    └── rob/
/srv/storage/.mnemerynne-trash/     the recycle bin, beside the share, hidden from SMB
```

## Access follows the share

In a share library people see exactly the folders their **linked domain account** can open on the share, and change files where it may write. Mnemerynne's own grants don't apply; set permissions in Windows (folder → Properties → Security). Samba keeps POSIX ACLs in step with those, and Mnemerynne reads them (leave `acl_xattr:ignore system acls` at its default `no`). Permissions are checked **per folder**: a file with tighter permissions than its folder is still listed to everyone who can open the folder, so keep restricted files in their own folder. Unlinked users see only their File box.

The service user `mnemerynne` must be able to read and write everything; in Windows' Security tab it shows as *Unix User\mnemerynne*. Keep it (Modify) on every folder, or Mnemerynne can't see or change that folder.

## Setting it up

```sh
sudo setfacl -R -m u:mnemerynne:rwX -m d:u:mnemerynne:rwX /srv/storage/share
sudo install -d -o mnemerynne -g mnemerynne -m 700 /srv/storage/.mnemerynne-trash
sudo semanage fcontext -a -t samba_share_t '/srv/storage/\.mnemerynne-trash(/.*)?'
sudo restorecon -Rv /srv/storage/.mnemerynne-trash
echo 'MNEMERYNNE_SHARE_ROOT=/srv/storage/share' | sudo tee -a /etc/mnemerynne/mnemerynne.env
sudo systemctl restart mnemerynne-web mnemerynne-worker
sudo mnemerynne-admin share-init     # marker, Users\, trash; moves empty inboxes onto the share
sudo mnemerynne-admin share-scan     # first scan now (the worker repeats it every minute)
```

`share-init` lists File boxes and libraries that still hold files inside Mnemerynne. Move them with `share-export --user NAME`, `--library NAME` or `--all` (`--dry-run` first): each is copied while people keep working, then switched over in a moment; files keep their search index. An exported library's access then follows the share, so set its folder's Security in Windows.

## How the scan works

- The worker scans every `SHARE_SCAN_SECONDS` (60), only while the marker file exists, so an unmounted share is never mirrored as "everything deleted".
- New top-level folders become libraries; `Users\<name>` folders attach to that person's empty File box.
- Renames and moves are recognised (by inode), so a file keeps its search index. Files changed within `SHARE_SETTLE_SECONDS` wait for the next scan (still being copied).
- Office lock files (`~$…`), `Thumbs.db`, `desktop.ini`, hidden files and partial downloads are ignored.
- Names must suit Windows: no `\ / : * ? " < > |`, no trailing dot or space, no reserved names (`CON`, `PRN`…). A library folder with a bad name is skipped and listed in the log until renamed.

## Deleting and the bin

Deleting in Files moves the file out of the share into `.mnemerynne-trash`; restoring puts it back with its permissions. Deleting in Windows Explorer bypasses that bin. Deleting a library on the Access page moves its folder to the trash directory.

## Writes from the app

Uploads, the browser editor, Claude's and Erynne's edits write to the share through a temporary dot-file next to the target and one rename, so nobody ever sees half a file, and a file open in Word or Excel (a `~$` lock file) is refused. The same `mnemerynne` ACL entry that lets the service read is what lets it write.

## Backups

Files on the share are **not** in Mnemerynne's backups; back the share up with the NAS. Mnemerynne's backup covers the database (the index, users, settings) and versions.
