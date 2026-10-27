# Installing and running

The reference install is Fedora with systemd; `deploy/linux/README.md` in the repository is the step-by-step version with the exact commands. This page is the map.

## The wizard

`deploy/linux/setup.sh` does sections 1 to 5 below, and the SMB share, in one go:

```sh
sudo dnf install git
sudo git clone https://github.com/AELovelace/erinne.git /opt/mnemerynne
sudo bash /opt/mnemerynne/deploy/linux/setup.sh             # ask, show the plan, run
sudo bash /opt/mnemerynne/deploy/linux/setup.sh --dry-run   # the plan only
```

It asks each question with its default and confirms the whole plan once. It grows a cramped root volume, runs `preinstall.sh`, prepares the storage (blank disks mirrored with mdadm, LVM and XFS mounted at `/srv/storage`; or an array already mounted there), lays out the share, its recycle bin, the backups and the data folder on it with the right owners, ACLs and SELinux labels, writes the settings file with a generated secret key, installs the Python environment and the services, defines the Samba share where Samba runs on the box, sets up Caddy and creates the first admin. Every step skips what is already done, so run it again after adding a disk or to pick up new unit files; every answer is also a flag (`--help`), so it runs unattended with `--yes`. The README's section 0 lists the flags and what stays by hand (joining the domain, Caddy's root certificate on PCs, Claude Desktop).

## 1. Code and prerequisites

```sh
sudo dnf install git
sudo git clone https://github.com/AELovelace/erinne.git /opt/mnemerynne
sudo bash /opt/mnemerynne/deploy/linux/preinstall.sh
cd /opt/mnemerynne && sudo uv sync --frozen --no-dev
sudo restorecon -R /opt/mnemerynne
```

`preinstall.sh` installs packages (uv, ocrmypdf, Vulkan build tools), creates the `mnemerynne` service user, builds llama.cpp, downloads and checks the embedding model, and checks the GPU. It is safe to run again. Python 3.14 and uv are required; Windows development works the same way with `py -3.14 -m uv`.

## 2. The settings file

```sh
sudo mkdir -p /etc/mnemerynne
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne.env.example /etc/mnemerynne/mnemerynne.env
sudo chown root:mnemerynne /etc/mnemerynne/mnemerynne.env && sudo chmod 640 /etc/mnemerynne/mnemerynne.env
sudoedit /etc/mnemerynne/mnemerynne.env      # set MNEMERYNNE_SECRET_KEY at least
sudo install -m 755 /opt/mnemerynne/deploy/linux/mnemerynne-admin /usr/local/bin/
```

Every setting is a `MNEMERYNNE_*` variable; see the [configuration reference](configuration.md). `sudo mnemerynne-admin <command>` runs any `mnemerynne` command as the service user with those settings.

## 3. Services

```sh
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne-*.service /opt/mnemerynne/deploy/linux/mnemerynne-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mnemerynne-generator mnemerynne-embedder mnemerynne-web mnemerynne-worker mnemerynne-backup.timer
journalctl -u mnemerynne-web -n 20 --no-pager      # "Database ready ... (schema vNN)"
```

Enable `mnemerynne-adsync.timer` only once the box is joined to the domain ([Accounts and domain accounts](accounts.md)). Keep your own changes to unit files in drop-ins (`systemctl edit`), which updates never touch.

## 4. The first admin

```sh
sudo mnemerynne-admin create-user yourname --role admin
```

Local admins (role `admin`) are admins whatever the domain says; keep one as a fallback for when AD is unreachable. Later admins can come from a domain group instead.

## 5. HTTPS

Caddy serves Mnemerynne on 443 with its own internal CA by default (`Caddyfile-mnemerynne`); install Caddy's root certificate on staff PCs, or use the public Caddyfile with Let's Encrypt when the box is reachable from the internet ([Security and exposure](security.md)). Set `MNEMERYNNE_PUBLIC_URL` to the address people use and restart the web app. `nginx-mnemerynne.conf` is the equivalent for nginx. Keep `FORWARDED_ALLOW_IPS=127.0.0.1` so rate limits see real client addresses.

## 6. Check

```sh
curl -s http://127.0.0.1:38200/healthz           # {"status":"ok","schema":NN}
sudo mnemerynne-admin list-users
```

Then sign in, upload a document on Files, watch it reach Ready, ask a question on Ask and click the citation.

## Development on a PC

```sh
uv sync
cp .env.example .env               # set MNEMERYNNE_SECRET_KEY
scripts/start-llama.ps1            # Windows: generator :8081, embedder :8082
uv run mnemerynne create-user yourname --role admin
uv run mnemerynne serve            # http://127.0.0.1:38200
uv run mnemerynne worker           # a second terminal
uv run pytest
```

## Moving an old "Erinne" install

A box set up before the rename is moved once with `deploy/linux/migrate-from-erinne.sh`: it renames the user, paths, settings and units, rebuilds the environment and starts the new services. Data and accounts stay; everyone signs in again.

## What else to set up

In rough order of value: [the SMB share](share.md), [domain accounts](accounts.md), [backups](backups.md), [Claude](claude.md), [internal APIs](internal-apis.md), [the Erynne agent](erynne-agent.md), [balancer nodes](balancer.md), [Microsoft 365](microsoft-365.md), [the organisation wiki](wiki-setup.md).
