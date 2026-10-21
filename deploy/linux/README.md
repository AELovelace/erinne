# Erinne on Fedora (pilot next to MicroJames)

The box already has what MicroJames needed: the NVIDIA 580xx driver,
llama.cpp in `/opt/llama.cpp`, and the `llama-generator` (:8081) and
`llama-embedder` (:8082, jina) services. Erinne shares the generator but uses
a better embedder (Qwen3-Embedding-0.6B, chosen by evaluation), so it gets its
own embedder service on :8085. Nothing of MicroJames changes.

| Service | Listens on | Does |
|---|---|---|
| `erinne-web` | 127.0.0.1:38200 | web app, answers questions |
| `erinne-worker` | — | turns uploads into searchable chunks (OCR, embeddings) |
| `erinne-embedder` | 127.0.0.1:8085 | Qwen3-Embedding-0.6B (llama-server) |
| `erinne-backup.timer` | — | nightly snapshot to `/var/backups/erinne` |

**VRAM check (4 GB card):** the generator (~2.5 GB with its cache), jina for
MicroJames (~0.2 GB) and Qwen3-Embedding (~0.7 GB) should just fit. Watch
`nvidia-smi` after step 4; if the embedder fails to start or the generator
runs out of memory, switch `erinne-embedder` to the CPU (see the comment in
its unit file). At cutover the jina embedder goes away and frees 0.2 GB.

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
sudo systemctl enable --now erinne-embedder erinne-web erinne-worker erinne-backup.timer
journalctl -u erinne-web -n 20 --no-pager     # "Database ready ... (schema v2)"
nvidia-smi                                     # three llama-server processes, under 4 GB total
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

## 6. nginx

```sh
sudo cp /opt/erinne/deploy/linux/nginx-erinne.conf /etc/nginx/conf.d/erinne.conf
sudoedit /etc/nginx/conf.d/erinne.conf          # server_name + certificate paths
sudo setsebool -P httpd_can_network_connect 1    # already set if MicroJames is proxied
sudo nginx -t && sudo systemctl reload nginx
```

The `/api/ask` block turns off proxy buffering; without it answers arrive
all at once instead of streaming.

## 7. Check

```sh
curl -s http://127.0.0.1:38200/healthz            # {"status":"ok","schema":2}
sudo erinne-admin list-users
```

Then in a browser: sign in, open **Files** (statuses should reach Ready),
ask "How do I run the night audit?" on **Ask**, and click a citation: it
should open the OPERA manual at the right page.

## Updating

Ship a new tarball, extract over `/opt/erinne`, `uv sync --frozen --no-dev`,
`sudo systemctl restart erinne-web erinne-worker`. Database migrations run
automatically at start-up.

## Restore from backup

```sh
sudo systemctl stop erinne-web erinne-worker
sudo -u erinne cp /var/backups/erinne/db/erinne-YYYY-MM-DD.db /var/lib/erinne/erinne.db
sudo -u erinne rsync -a /var/backups/erinne/blobs/ /var/lib/erinne/blobs/
sudo systemctl start erinne-web erinne-worker
```
