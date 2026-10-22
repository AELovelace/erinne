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
| `mnemerynne-adsync.timer` | — | every 5 minutes, as root: AD group memberships into `/var/lib/mnemerynne-ad` (5c) |

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
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne-*.service /opt/mnemerynne/deploy/linux/mnemerynne-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mnemerynne-generator mnemerynne-embedder mnemerynne-web mnemerynne-worker mnemerynne-backup.timer
# Only once the box is joined to the domain (5c):
#   sudo systemctl enable --now mnemerynne-adsync.timer
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

**The Claude column in Files.** People with Claude connected (an active token)
get a **Claude** column after Status in the file list.
- **What it shows:** ticked means Claude can see that file or folder.
  Unticking takes it out of Claude's search, browsing, passages and pages.
  For a folder, that includes everything inside it. Ticking puts it back.
- **Who can change it:** only people who can change that folder. It's a
  property of the file or folder, so it applies to everyone's Claude, not
  just their own.
- **What it can't do:** share anything an admin hasn't shared with Claude.
  There the box is unticked and greyed out, as it is under a hidden folder.
- **Erynne is unaffected:** she still sees everything the person can read.
- **How search applies it:** the flag is stored on the file's search chunks
  and vectors, so hidden files never take up search results. The first start
  after updating rebuilds the vector index's layout once, keeping every
  embedding.

**AI edit.** People with Claude connected, or with Erynne's tools, also get
an **AI edit** column. Ticked means Claude, and Erynne's proposals, may
change that file, or the files in that folder.
- **In their own files:** people can switch it on for any folder or file, as
  if it were the Agents folder.
- **In libraries:** only an admin allows AI edits (Access → the library's
  "Claude and Erynne can edit files here"). There the column can only make
  things read-only.
- **Folders:** a folder's setting covers everything inside it.

**Properties and Re-embed.** Right-click → **Properties…** (or the toolbar
button) shows:
- the file's or folder's details
- its search state: status, number of passages, how many are embedded, and
  any error
- the "Claude can see" and "AI can edit" switches

**Re-embed** (toolbar, right-click, or Properties) reads and embeds files
again in the background:
- what it covers: the selection, or everything in the current folder or
  library when nothing is selected
- what's redone: chunks and embeddings only; extracted text and OCR are kept
- who can use it: anyone in their own files; admins in any library they can
  open. For all files at once: `sudo mnemerynne-admin reindex`.

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

**Group memberships come from AD itself, refreshed every 5 minutes.** Winbind
only refreshes someone's groups when they sign in to this box, and it also
remembers for a while that a newly created group "doesn't exist", so it can't
be trusted for memberships. The `mnemerynne-adsync` timer runs
`deploy/linux/ad-snapshot.sh` as root every 5 minutes. The script reads every
account's and group's memberships from a domain controller with
`net ads search -P` (the machine account the join created; no extra
credentials) into `/var/lib/mnemerynne-ad/snapshot.txt`. Mnemerynne works out
nested groups from it and uses it for admin rights, API access and share
permissions. Adding someone to a group or removing them takes effect within
about 5 minutes, without restarts. If the file is missing or over 30 minutes
old, Mnemerynne uses winbind as before and logs a warning.

```sh
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne-adsync.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mnemerynne-adsync.timer
sudo systemctl start mnemerynne-adsync && journalctl -u mnemerynne-adsync -n 5 --no-pager   # "AD snapshot: N accounts and groups"
sudo grep -c '^sAMAccountName:' /var/lib/mnemerynne-ad/snapshot.txt
```

Never "fix" stale groups with `net cache flush` on a schedule: right after a
flush winbind reports only each person's primary group, which briefly locks
people out of everything their other groups give them.

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

## 5f. Internal APIs

Erynne can answer live questions from internal systems ("did every hotel send
its night audit?", "any critical Wazuh alerts?", "what's the status of ticket
4812?"). Admins set them up on **Access → APIs**: start from a preset (Wazuh,
Atera) or a blank API.

- **Read-only.** Erynne only calls the operations defined there: a fixed path
  with typed parameters, GET, or a POST whose body is fixed on that page.
  Redirects aren't followed, and responses are capped at 256 KB.
- **Who.** Each API is open to one AD group, reached through people's linked
  domain accounts (section 5c). People outside it never see the API, in
  chat or through Claude. **Claude** only reaches an API when its *Claude
  can query this* switch is on (off by default): whatever Claude reads goes
  to Anthropic.
- **Credentials** are pasted on the page, stored encrypted with a key derived
  from `MNEMERYNNE_SECRET_KEY`, and never shown again. Keep that key stable:
  if it changes, the credentials must be entered again. Use dedicated
  read-only credentials, for example a Wazuh user with a read-only role, and an Atera token
  limited to reading tickets.
- **Self-signed servers** (Wazuh, for example): press *Fetch from server*, compare
  the SHA-256 fingerprint with the real one, and save it. Only that
  certificate is then trusted for the API. For servers reached by IP, untick
  *Check the name in the certificate*.
- **Routing.** A chat question goes to an operation when it closely matches
  one of the operation's *example questions*, so add a few phrased the way
  staff ask. `MNEMERYNNE_API_ROUTE_THRESHOLD` (default 0.80) sets how close a
  match has to be.
- **Test** on each operation calls it now and lists the fields in the response.
  *Recent calls* shows who asked what. A citation in chat opens the exact
  response the answer was based on (for the person who asked, and for admins).
  That response is stored with the call, so keep in mind that answers from
  Atera keep ticket text in Mnemerynne's database.

## 5g. Erynne agent (tools in chat)

People in one AD group get Erynne with the same tools Claude has: she
searches, reads and browses their documents, opens PDFs at a page, asks the
internal APIs they may use, and drafts changes to `.txt`, `.md`, `.docx` and
`.xlsx` files. Everyone else keeps the normal answers.

1. **Create the group** on the domain controller (for example
   `Erynne Agents`) and add people to it. They need their Mnemerynne user
   linked to their domain account (section 5c). Admins aren't included
   automatically; add them to the group like anyone else.
2. **The model.** The agent uses the same model as the answers: the balancer
   nodes (5h), the least busy first, then `MNEMERYNNE_LLM_URL` (the RX 580)
   when none answers. There's no separate address to set. Those servers need
   tool support: llama-server with `--jinja` (the balancer starts its
   catalog models with it) or a `--chat-template-file`.
3. **Settings → Erynne agent:** pick the group and save. The status line says
   whether a model answers, and by which route. The limits (timeout, tool
   steps, tokens, thinking) are below it. Settings from the old setup (the
   agent's own address, model name and key) are ignored.

How it behaves:

- **Edits wait for a click.** Erynne never changes a file herself. Each edit
  or new file shows as a card under her answer, with the change list (and a
  diff for text files). Nothing happens until the person presses **Apply**.
  Apply checks everything again: still in the group, can still edit there,
  and the file hasn't changed since (otherwise it's refused as out of date).
  The previous version is kept, as with Claude's edits (Files → Versions).
  She edits only in the person's Agents folder, and in libraries where
  *Claude and Erynne can edit files here* is on and the person can edit.
- **What she reads.** She runs on this server, so she reads everything the
  person can read, not only what's shared with Claude. APIs follow each API's
  own group. A *Use tools* switch under the question box lets people ask
  without tools.
- **Fallback.** If no model answers (balancer nodes or the RX 580, checked
  every 30 seconds), or it fails before doing anything, the question gets a
  normal answer instead.
- **Records.** Every tool call is logged in `agent_calls`, and answers
  record the agent model's name. Proposals nobody decides on expire after
  `MNEMERYNNE_VERSION_DAYS` (30). Their content sits in
  `<data_dir>/proposals` until then. It isn't backed up, since it's
  short-lived.

## 5h. Balancer nodes (the answer model on other boxes)

The answer model can run on one or more
[mnemerynne-balancer](https://github.com/AELovelace/mnemerynne-balancer)
boxes (for example the npc-rag node's two RX 6800s) instead of, or as well
as, the RX 580 here.

1. **On each balancer**, download and start Erynne's model on its control
   page (`http://<node>:9180`): LFM2.5 2.6B Q8 for the stock model. A
   fine-tuned GGUF has to be on Hugging Face for the balancer to download it.
   Note its API key (`BALANCER_API_KEY` in its `.env`, or set it on its
   Settings tab). Every node needs the same key.
2. **Settings → Balancer nodes:** enter the addresses separated by commas,
   for example `http://10.0.5.20:9180, http://10.0.5.21:9180`, and the key,
   then Save. Each node is checked with the key first; if one doesn't answer
   or rejects the key, nothing is saved and the message says which one. The
   key is stored encrypted (it needs `MNEMERYNNE_SECRET_KEY`) and never shown
   again.

How it behaves:

- **Least busy.** Every node is asked every 2 seconds how busy it is
  (`/v1/balancer/load`: requests in flight and parallel slots). Each answer
  goes to the ready node with the fewest requests per slot. Requests are
  counted the moment they're sent, so a burst spreads across nodes instead
  of piling onto whichever looked idle at the last check. Ties go to the
  node listed first.
- **Failover.** A node that refuses the connection, times out, rejects the
  key or answers 5xx sits out for 30 seconds, and the request goes to the
  next node. Once a node has started answering, the answer comes from it.
  If no node answers, or the list is empty, answers come from
  `MNEMERYNNE_LLM_URL` as before, so keep the RX 580 generator running as the
  fallback.
- **Status.** The section shows each node: whether it answers, the model it
  runs, requests in flight per slot, answers sent there in the last hour, and
  when it was last checked. It refreshes while the tab is open.
- **What it covers.** Chat answers, everything else the answer model does
  (routing questions to APIs, memories, apologies), and the Erynne agent
  (5g). The embedder stays on this box, next to the files it indexes.
- **The balancers don't know about each other.** Each one spreads requests
  over its own cards. Spreading them between boxes happens here.

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
sudo -u mnemerynne rsync -a /var/backups/mnemerynne/versions/ /var/lib/mnemerynne/versions/
sudo systemctl start mnemerynne-web mnemerynne-worker
```
