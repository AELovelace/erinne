# Mnemerynne/Erynne on Fedora (pilot next to MicroJames)

The box has two GPUs. MicroJames keeps the GTX 1050 Ti, with its CUDA
llama.cpp in `/opt/llama.cpp` and its `llama-generator` (:8081) and
`llama-embedder` (:8082, jina) services. Mnemerynne's generator runs on the
RX 580 (8 GB) with its own llama.cpp built for Vulkan in
`/opt/llama.cpp-vulkan`; its embedder shares the 1050 Ti and the CUDA build
there (embedding on the RX 580 hung the GPU under load). Keep `/opt/llama.cpp`
when MicroJames is switched off.

| Service | Listens on | Does |
|---|---|---|
| `mnemerynne-web` | 127.0.0.1:38200 | web app, answers questions, MCP for Claude |
| `mnemerynne-worker` | — | turns uploads into searchable chunks (OCR, embeddings) |
| `mnemerynne-generator` | 127.0.0.1:8086 | LFM2.5-2.6B (llama-server, Vulkan) |
| `mnemerynne-embedder` | 127.0.0.1:8085 | Qwen3-Embedding-0.6B (llama-server, CUDA on the 1050 Ti) |
| `mnemerynne-backup.timer` | — | nightly snapshot to `/var/backups/mnemerynne` |

**VRAM:** the generator uses about 2.5 GB of the RX 580's 8 GB. The 1050 Ti
(4 GB) holds the embedder (~0.7 GB) next to MicroJames's generator and jina
(~2.7 GB). Check both cards with `nvtop` after step 3. The generator unit
joins the `render` and `video` groups to reach the RX 580.

## 1. Code and prerequisites

```sh
sudo dnf install git
sudo git clone https://github.com/AELovelace/erinne.git /opt/mnemerynne
sudo bash /opt/mnemerynne/deploy/linux/preinstall.sh
cd /opt/mnemerynne && sudo uv sync --frozen --no-dev
sudo restorecon -R /opt/mnemerynne
```

`preinstall.sh` installs the packages (uv, ocrmypdf, the Vulkan build tools),
creates the `mnemerynne` user, builds llama.cpp for Vulkan into
`/opt/llama.cpp-vulkan`, downloads and checks Qwen3-Embedding, and finds
which llama.cpp device is the RX 580 (`Vulkan1` when the 1050 Ti is also
present). It writes that device to a `gpu.conf` drop-in for
`mnemerynne-generator`. It's safe to run again, for example
after adding or removing a card. `MNEMERYNNE_GPU=Vulkan0 sudo -E bash …` skips
the detection.

If the repo is private, `git clone` asks for a GitHub username and a personal
access token (read-only, for this repo) instead of a password.

## 2. Settings

```sh
sudo mkdir -p /etc/mnemerynne
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne.env.example /etc/mnemerynne/mnemerynne.env
sudo chown root:mnemerynne /etc/mnemerynne/mnemerynne.env && sudo chmod 640 /etc/mnemerynne/mnemerynne.env
sudoedit /etc/mnemerynne/mnemerynne.env      # set MNEMERYNNE_SECRET_KEY (command is in the file)
sudo install -m 755 /opt/mnemerynne/deploy/linux/mnemerynne-admin /usr/local/bin/
```

`sudo mnemerynne-admin <command>` runs any `mnemerynne` command as the service user
with these settings (e.g. `sudo mnemerynne-admin list-users`).

## 3. Services

```sh
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne-*.service /opt/mnemerynne/deploy/linux/mnemerynne-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mnemerynne-generator mnemerynne-embedder mnemerynne-web mnemerynne-worker mnemerynne-backup.timer
journalctl -u mnemerynne-web -n 20 --no-pager     # "Database ready ... (schema v6)"
journalctl -u mnemerynne-generator -n 40 --no-pager | grep -i 'RX 580'   # the model loads on the RX 580
nvtop                                          # RX 580: the generator; 1050 Ti: the embedder too
```

## 4. Import MicroJames users and documents

The mnemerynne user needs to read MicroJames's data for this step:

```sh
sudo setfacl -R -m u:mnemerynne:rX /opt/microjames/data /opt/microjames/docs
sudo mnemerynne-admin migrate-microjames --source /opt/microjames --dry-run
sudo mnemerynne-admin migrate-microjames --source /opt/microjames --import-files
sudo setfacl -R -x u:mnemerynne /opt/microjames/data /opt/microjames/docs
journalctl -u mnemerynne-worker -f        # "File N: … chunks indexed"; the Oracle manual takes a while
```

Users keep their MicroJames passwords. With `ocrmypdf` installed the two
scanned Oregon letters should come through this time.

## 5. HTTPS (Caddy)

Caddy serves Mnemerynne on 443; MicroJames isn't behind Caddy and keeps its own
port. Pick a hostname that resolves to this box on staff PCs (internal DNS
or the router), e.g. `erynne.example.internal`.

```sh
sudo dnf install caddy
sudoedit /etc/caddy/Caddyfile                       # delete Fedora's default ":80 { … }" block
sed 's/erynne.example.internal/YOUR-HOSTNAME/' /opt/mnemerynne/deploy/linux/Caddyfile-mnemerynne \
  | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo setsebool -P httpd_can_network_connect 1       # lets Caddy connect to Mnemerynne on 38200
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl enable caddy && sudo systemctl reload-or-restart caddy
sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload
curl -vk --resolve YOUR-HOSTNAME:443:127.0.0.1 https://YOUR-HOSTNAME/healthz
```

If a browser shows Caddy's "Fedora webserver" test page, the request missed
the Mnemerynne site: the default `:80` block is still there, or the address was
`http://`, the IP, or a hostname that doesn't match the Caddyfile.

`tls internal` makes Caddy its own certificate authority. Its root
certificate is `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`:
install it on staff PCs as a trusted root (Local Machine → Trusted Root
Certification Authorities; Group Policy for many PCs), and copy it to
`clients/claude-desktop/ca.pem` on the dev PC before packing the extension.
Then set `MNEMERYNNE_PUBLIC_URL=https://YOUR-HOSTNAME` (next section).

`flush_interval -1` keeps answers streaming. `nginx-mnemerynne.conf` is an
equivalent nginx setup, for a box where nginx owns port 443 instead.

## 5b. Claude Desktop

Staff connect Claude Desktop through the Mnemerynne extension, which relays to
`https://YOUR-HOSTNAME/mcp` with a personal token (made under **Claude** in
the web app). Admins choose what Claude may search on the **Access** page;
everything is on by default.

1. Set `MNEMERYNNE_PUBLIC_URL` in `/etc/mnemerynne/mnemerynne.env` to the address people
   use, then `sudo systemctl restart mnemerynne-web`.
2. On the dev PC, pack the extension (see `clients/claude-desktop/README.md`)
   with that address as the default and Caddy's root certificate as `ca.pem`.
3. Copy `mnemerynne.mcpb` to `/var/lib/mnemerynne/mnemerynne.mcpb` (owner mnemerynne). The
   **Claude** page then offers it for download.

To cut someone off at once: **Access → Revoke Claude tokens**, or
`sudo mnemerynne-admin revoke-tokens USERNAME`. **Access → Claude activity**
shows every search and which files' text went back to Claude.

## 5c. Domain accounts

People sign in with their Mnemerynne username and password. An admin links
each user to their domain account on **Access → Accounts**; admin rights then
come from an AD group picked on **Access → Settings** (default `Domain Admins`).
Users with the `admin` role stay admins regardless, so keep one as a fallback
for when AD is unreachable.

The server must be joined to the domain (winbind) first: `wbinfo -u` should
list domain accounts and `id 'DOMAIN\someone'` should resolve. Then set the
domain's NetBIOS name and restart:

```sh
echo 'MNEMERYNNE_AD_DOMAIN=ETERNALHOTELS' | sudo tee -a /etc/mnemerynne/mnemerynne.env
sudo systemctl restart mnemerynne-web
```

Linking checks that the domain account exists; unlinking keeps the user and
their files. `sudo mnemerynne-admin delete-user NAME` removes a user, with
their inbox and memories.

## 5d. Libraries on the SMB share

With `MNEMERYNNE_SHARE_ROOT` set, every top-level folder of the share is a
library and `Users\<name>` is that person's inbox. The share is the source of
truth: what people add, rename, move or delete through `\\server\share` shows
up in the Files tab within a minute, and what they do in the Files tab happens
on the share straight away. The Files tab's recycle bin is
`/srv/storage/.mnemerynne-trash`, beside the share and hidden from SMB.

**Access follows the share.** In a share library, people see exactly the
folders their linked domain account (section 5c) can open on the share, and
can change files where that account may write; Mnemerynne's own grants don't
apply there. Set permissions in Windows (folder → Properties → Security).
Mnemerynne reads the Linux permissions Samba keeps in step with them, so
leave `acl_xattr:ignore system acls` at its default (`no`). Permissions are
checked per folder: a file with tighter permissions than its folder is still
visible to everyone who can open the folder, so keep restricted files in their
own folder. Unlinked users see only their inbox. Inboxes stay private: only the owner's domain account gets
into their `Users\<name>` folder.

1. Let the service user read and write the share and its trash (new files and
   folders inherit this):
   ```sh
   sudo setfacl -R -m u:mnemerynne:rwX -m d:u:mnemerynne:rwX /srv/storage/share
   sudo install -d -o mnemerynne -g mnemerynne -m 700 /srv/storage/.mnemerynne-trash
   # Same SELinux label as the share, so files restored from the bin stay readable by Samba:
   sudo semanage fcontext -a -t samba_share_t '/srv/storage/\.mnemerynne-trash(/.*)?'
   sudo restorecon -Rv /srv/storage/.mnemerynne-trash
   ```
   In Windows' Security tab this entry shows as *Unix User\mnemerynne*; keep it
   (Modify) on every folder, or Mnemerynne can't see or change that folder.
2. Turn it on and prepare the share:
   ```sh
   echo 'MNEMERYNNE_SHARE_ROOT=/srv/storage/share' | sudo tee -a /etc/mnemerynne/mnemerynne.env
   sudo systemctl restart mnemerynne-web mnemerynne-worker
   sudo mnemerynne-admin share-init     # marker, Users\, trash; moves empty inboxes onto the share
   sudo mnemerynne-admin share-scan     # first scan now (the worker repeats it every minute)
   ```
   `share-init` lists inboxes and libraries that still hold files inside
   Mnemerynne. Move them onto the share (files keep their search index, so
   nothing is re-embedded; names Windows can't use are renamed and listed):
   ```sh
   sudo mnemerynne-admin share-export --all --dry-run   # what would happen
   sudo mnemerynne-admin share-export --user edith      # or --library Oracle, or --all
   ```
   Each one is copied while people keep working, then switched over in a
   moment; if someone changed it during the copy, the copy is undone and you
   run it again. An exported library's access then follows the share's
   permissions, so set its folder's Security in Windows (the export creates it
   with the share root's permissions).

The worker only scans while `/srv/storage/share/.mnemerynne-share` exists, so
an unmounted share is never mirrored as "everything deleted". Renames and
moves are recognised (a file keeps its search index). Names must also suit
Windows: no `\ / : * ? " < > |`, no trailing dot or space. Office lock files
(`~$…`), `Thumbs.db`, `desktop.ini` and hidden files are ignored. Deleting a
library on the Access page moves its folder to the trash directory.

## 5e. Office documents and code

Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls), CSV, OpenDocument
(.odt/.odp/.ods), RTF, HTML and source/config files are read directly and
become searchable; citations name the heading, slide, sheet rows or code
lines, and the file's text view links to them. Old binary Word and PowerPoint
files (.doc, .ppt) need LibreOffice, which converts them first; without it
they are stored but not searchable:

```sh
sudo dnf install libreoffice-core libreoffice-writer libreoffice-impress   # optional, for .doc/.ppt
```

After updating to a version with this, files uploaded earlier are still
"stored only"; make them searchable with:

```sh
sudo mnemerynne-admin reclassify
```

## 6. Check

```sh
curl -s http://127.0.0.1:38200/healthz            # {"status":"ok","schema":6}
sudo mnemerynne-admin list-users
```

Then in a browser: sign in, open **Files** (statuses should reach Ready),
ask "How do I run the night audit?" on **Ask**, and click a citation: it
should open the OPERA manual at the right page.

## Moving an Erinne install to Mnemerynne

A box set up before the rename (user `erinne`, `/opt/erinne`, `erinne-*`
units) is moved once with:

```sh
cd /opt/erinne && sudo git pull
sudo bash /opt/erinne/deploy/linux/migrate-from-erinne.sh
```

It stops the old units, renames the service user, moves `/opt`, `/var/lib`,
`/etc` and the backups, renames `ERINNE_*` settings to `MNEMERYNNE_*`, moves
the GPU and model drop-ins, rebuilds `.venv` and starts the new units. Data
and accounts are untouched; everyone signs in again, and Claude tokens made
before the rename keep working.

## Updating

```sh
sudo mnemerynne-admin check-updates     # what's new on GitHub and what an update would change
sudo mnemerynne-admin update            # asks first; --yes to skip the question
```

`update` (deploy/linux/update.sh, run as root) does what used to be done by hand:

1. Backs up the database to `/var/lib/mnemerynne/update/before-*.db` (the last five are kept).
2. Fast-forwards `/opt/mnemerynne` to GitHub's `main`. It refuses, changing nothing, if
   the checkout has local edits or commits GitHub doesn't have.
3. `uv sync --frozen --no-dev` and `restorecon`.
4. Installs service files that changed (and the `mnemerynne-admin` command), then
   restarts what changed: the generator or embedder if their files did, then
   `mnemerynne-web` (which applies any database update), and once it reports
   healthy, `mnemerynne-worker`.
5. If any step fails or the web app doesn't come back healthy, it undoes all of
   it: previous code and packages, previous service files, and the database backup
   if the schema had changed. Then it shows the end of the web app's log.

Keep your own changes to service files in drop-ins
(`sudo systemctl edit mnemerynne-generator`): an update replaces the unit files
in `/etc/systemd/system` but never touches drop-ins. The **Settings** tab on the
Access page shows whether updates are available and how the last one went; it
can't start one.

**First time:** this needs the new `mnemerynne-admin` installed once by hand:

```sh
cd /opt/mnemerynne && sudo git pull && sudo uv sync --frozen --no-dev && sudo restorecon -R /opt/mnemerynne
sudo install -m 755 deploy/linux/mnemerynne-admin /usr/local/bin/
sudo systemctl restart mnemerynne-web mnemerynne-worker
```

## Restore from backup

```sh
sudo systemctl stop mnemerynne-web mnemerynne-worker
sudo -u mnemerynne cp /var/backups/mnemerynne/db/mnemerynne-YYYY-MM-DD.db /var/lib/mnemerynne/mnemerynne.db
sudo -u mnemerynne rsync -a /var/backups/mnemerynne/blobs/ /var/lib/mnemerynne/blobs/
sudo systemctl start mnemerynne-web mnemerynne-worker
```
