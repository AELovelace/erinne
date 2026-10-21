# Erinne on Fedora (pilot next to MicroJames)

The box runs an AMD RX 580 (8 GB) with llama.cpp built for Vulkan in
`/opt/llama.cpp` (see the RX 580 install guide for the driver and build),
next to MicroJames and its `llama-generator` (:8081) and `llama-embedder`
(:8082, jina) services. Erinne has its own generator and embedder, so it
runs independently and MicroJames can be switched off without touching it.

| Service | Listens on | Does |
|---|---|---|
| `erinne-web` | 127.0.0.1:38200 | web app, answers questions, MCP for Claude |
| `erinne-worker` | — | turns uploads into searchable chunks (OCR, embeddings) |
| `erinne-generator` | 127.0.0.1:8086 | LFM2.5-2.6B (llama-server, Vulkan) |
| `erinne-embedder` | 127.0.0.1:8085 | Qwen3-Embedding-0.6B (llama-server, Vulkan) |
| `erinne-backup.timer` | — | nightly snapshot to `/var/backups/erinne` |

**VRAM (8 GB card):** Erinne's generator (~2.5 GB with its cache) and
embedder (~0.7 GB) plus MicroJames's generator and jina embedder (~2.9 GB)
come to about 6 GB. Check with `radeontop` after step 4. Both llama-server
units join the `render` and `video` groups to reach the GPU.

## 1. Packages

```sh
sudo dnf install uv ocrmypdf tesseract-langpack-eng
ocrmypdf --version          # scanned PDFs are OCR'd automatically when this exists

# Embedding model (check the SHA256 against the Hugging Face file page)
cd /opt/models && sudo curl -LO https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf
sha256sum Qwen3-Embedding-0.6B-Q8_0.gguf   # 06507c7b4268… (first 12 hex digits)
```

## 2. Code (from the dev PC)

The repo isn't on GitHub, so ship a tarball of a commit:

```powershell
# On the dev PC (PowerShell), in C:\Scripts\erinne
git archive --format=tar.gz -o erinne.tar.gz HEAD
scp erinne.tar.gz edith@<box-ip>:/home/edith/
```

```sh
# On the box
sudo useradd --system --home-dir /var/lib/erinne --shell /sbin/nologin erinne
sudo mkdir -p /opt/erinne && sudo tar -xzf /home/edith/erinne.tar.gz -C /opt/erinne
cd /opt/erinne && sudo uv sync --frozen --no-dev
sudo chown -R root:root /opt/erinne && sudo restorecon -R /opt/erinne
```

## 3. Settings

```sh
sudo mkdir -p /etc/erinne
sudo cp /opt/erinne/deploy/linux/erinne.env.example /etc/erinne/erinne.env
sudo chown root:erinne /etc/erinne/erinne.env && sudo chmod 640 /etc/erinne/erinne.env
sudoedit /etc/erinne/erinne.env      # set ERINNE_SECRET_KEY (command is in the file)
sudo install -m 755 /opt/erinne/deploy/linux/erinne-admin /usr/local/bin/
```

`sudo erinne-admin <command>` runs any `erinne` command as the service user
with these settings (e.g. `sudo erinne-admin list-users`).

## 4. Services

```sh
sudo cp /opt/erinne/deploy/linux/erinne-*.service /opt/erinne/deploy/linux/erinne-backup.timer /etc/systemd/system/
sudo mkdir -p /var/backups/erinne && sudo chown erinne:erinne /var/backups/erinne
sudo systemctl daemon-reload
sudo chown erinne:erinne /opt/models/Qwen3-Embedding-0.6B-Q8_0.gguf
sudo chmod o+r /opt/models/LFM2.5-2.6B-Q4_K_M.gguf     # erinne-generator reads it too
sudo systemctl enable --now erinne-generator erinne-embedder erinne-web erinne-worker erinne-backup.timer
journalctl -u erinne-web -n 20 --no-pager     # "Database ready ... (schema v6)"
journalctl -u erinne-generator -n 20 --no-pager | grep -i vulkan   # "Vulkan0: AMD Radeon RX 580 ..."
sudo radeontop -d - -l 1 | grep -o 'vram [^,]*'  # about 6 GB used while MicroJames also runs
```

## 5. Import MicroJames users and documents

The erinne user needs to read MicroJames's data for this step:

```sh
sudo setfacl -R -m u:erinne:rX /opt/microjames/data /opt/microjames/docs
sudo erinne-admin migrate-microjames --source /opt/microjames --dry-run
sudo erinne-admin migrate-microjames --source /opt/microjames --import-files
sudo setfacl -R -x u:erinne /opt/microjames/data /opt/microjames/docs
journalctl -u erinne-worker -f        # "File N: … chunks indexed"; the Oracle manual takes a while
```

Users keep their MicroJames passwords. With `ocrmypdf` installed the two
scanned Oregon letters should come through this time.

## 6. HTTPS (Caddy)

Caddy already serves MicroJames on 443, so Erinne is a second site in the
same Caddyfile. Pick a hostname that resolves to this box on staff PCs
(internal DNS or the router), e.g. `erinne.example.internal`.

```sh
sudo dnf install caddy                              # already there if MicroJames uses it
sudoedit /etc/caddy/Caddyfile                       # delete Fedora's default ":80 { … }" block
sed 's/erinne.example.internal/YOUR-HOSTNAME/' /opt/erinne/deploy/linux/Caddyfile-erinne \
  | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo setsebool -P httpd_can_network_connect 1       # already set if MicroJames is proxied
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl enable caddy && sudo systemctl reload-or-restart caddy
sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload
curl -vk --resolve YOUR-HOSTNAME:443:127.0.0.1 https://YOUR-HOSTNAME/healthz
```

If a browser shows Caddy's "Fedora webserver" test page, the request missed
the Erinne site: the default `:80` block is still there, or the address was
`http://`, the IP, or a hostname that doesn't match the Caddyfile.

`tls internal` makes Caddy its own certificate authority. Its root
certificate is `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`:
install it on staff PCs as a trusted root (Local Machine → Trusted Root
Certification Authorities; Group Policy for many PCs), and copy it to
`clients/claude-desktop/ca.pem` on the dev PC before packing the extension.
Then set `ERINNE_PUBLIC_URL=https://YOUR-HOSTNAME` (next section).

`flush_interval -1` keeps answers streaming. `nginx-erinne.conf` is an
equivalent nginx setup, for a box where nginx owns port 443 instead.

## 6b. Claude Desktop

Staff connect Claude Desktop through the Erinne extension, which relays to
`https://YOUR-HOSTNAME/mcp` with a personal token (made under **Claude** in
the web app). Admins choose what Claude may search on the **Access** page;
everything is on by default.

1. Set `ERINNE_PUBLIC_URL` in `/etc/erinne/erinne.env` to the address people
   use, then `sudo systemctl restart erinne-web`.
2. On the dev PC, pack the extension (see `clients/claude-desktop/README.md`)
   with that address as the default and Caddy's root certificate as `ca.pem`.
3. Copy `erinne.mcpb` to `/var/lib/erinne/erinne.mcpb` (owner erinne). The
   **Claude** page then offers it for download.

To cut someone off at once: **Access → Revoke Claude tokens**, or
`sudo erinne-admin revoke-tokens USERNAME`. **Access → Claude activity**
shows every search and which files' text went back to Claude.

## 7. Check

```sh
curl -s http://127.0.0.1:38200/healthz            # {"status":"ok","schema":6}
sudo erinne-admin list-users
```

Then in a browser: sign in, open **Files** (statuses should reach Ready),
ask "How do I run the night audit?" on **Ask**, and click a citation: it
should open the OPERA manual at the right page.

## Updating

Ship a new tarball, extract over `/opt/erinne`, `uv sync --frozen --no-dev`,
`sudo systemctl restart erinne-web erinne-worker`. Database migrations run
automatically at start-up.

Take a backup first (`sudo systemctl start erinne-backup`) when an update
changes the schema. The file-manager update (schema v4) rebuilds the files
table and the vector index in place: about 20 seconds for 6,000 chunks, with
nothing re-embedded. Start `erinne-web` first and let it finish migrating
before starting `erinne-worker`.

## Restore from backup

```sh
sudo systemctl stop erinne-web erinne-worker
sudo -u erinne cp /var/backups/erinne/db/erinne-YYYY-MM-DD.db /var/lib/erinne/erinne.db
sudo -u erinne rsync -a /var/backups/erinne/blobs/ /var/lib/erinne/blobs/
sudo systemctl start erinne-web erinne-worker
```
