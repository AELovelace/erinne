#!/bin/bash
# One-time move of an Erinne install to the Mnemerynne names: service user,
# units, /opt, /var/lib, /etc, backups, settings (ERINNE_* -> MNEMERYNNE_*)
# and the GPU drop-ins. Data, files and accounts are kept as they are.
#
#   cd /opt/erinne && sudo git pull
#   sudo bash /opt/erinne/deploy/linux/migrate-from-erinne.sh
#
# Safe to run again: steps already done are skipped. Caddy needs no change
# (Mnemerynne still listens on 127.0.0.1:38200).
set -euo pipefail

step() { printf '\n== %s\n' "$*"; }
die() { printf 'migrate-from-erinne: %s\n' "$*" >&2; exit 1; }
move() {  # move <old> <new>, unless already moved
    if [ -e "$1" ] && [ ! -e "$2" ]; then mv "$1" "$2"; echo "moved $1 -> $2"
    elif [ -e "$1" ]; then die "both $1 and $2 exist; sort that out by hand first"; fi
}

[ "$(id -u)" = 0 ] || die "run with sudo"
[ -d /opt/erinne ] || [ -d /opt/mnemerynne ] || die "no /opt/erinne or /opt/mnemerynne here"

step "Stop the Erinne services"
for unit in erinne-web erinne-worker erinne-generator erinne-embedder erinne-backup.timer erinne-backup; do
    systemctl disable --now "$unit" 2>/dev/null || true
done

step "Rename the service user"
if id erinne >/dev/null 2>&1; then
    usermod -l mnemerynne -d /var/lib/mnemerynne erinne
    groupmod -n mnemerynne erinne
fi
id mnemerynne >/dev/null 2>&1 || die "no mnemerynne user after renaming"

step "Move code, data, settings and backups"
move /opt/erinne /opt/mnemerynne
move /var/lib/erinne /var/lib/mnemerynne
move /var/backups/erinne /var/backups/mnemerynne
move /var/cache/erinne-llama /var/cache/mnemerynne-llama
# SQLite finds its -wal/-shm files by name, so they move with the database.
for suffix in "" -wal -shm; do
    move "/var/lib/mnemerynne/erinne.db$suffix" "/var/lib/mnemerynne/mnemerynne.db$suffix"
done
move /var/lib/mnemerynne/erinne.mcpb /var/lib/mnemerynne/erynne.mcpb
if [ -d /etc/erinne ]; then
    move /etc/erinne /etc/mnemerynne
    move /etc/mnemerynne/erinne.env /etc/mnemerynne/mnemerynne.env
fi
if [ -f /etc/mnemerynne/mnemerynne.env ]; then
    sed -i 's/ERINNE_/MNEMERYNNE_/g' /etc/mnemerynne/mnemerynne.env
    chown root:mnemerynne /etc/mnemerynne/mnemerynne.env && chmod 640 /etc/mnemerynne/mnemerynne.env
fi

step "Move the unit drop-ins (GPU, model)"
for unit in generator embedder; do
    move "/etc/systemd/system/erinne-$unit.service.d" "/etc/systemd/system/mnemerynne-$unit.service.d"
    dir="/etc/systemd/system/mnemerynne-$unit.service.d"
    [ -d "$dir" ] && sed -i 's/ERINNE_/MNEMERYNNE_/g; s#/opt/erinne#/opt/mnemerynne#g' "$dir"/*.conf 2>/dev/null || true
done

step "Remove the old units and admin command"
rm -f /etc/systemd/system/erinne-{web,worker,generator,embedder,backup}.service /etc/systemd/system/erinne-backup.timer
rm -f /usr/local/bin/erinne-admin

step "Rebuild the Python environment (the package is now mnemerynne)"
cd /opt/mnemerynne
rm -rf .venv
uv sync --frozen --no-dev

step "Install the Mnemerynne units"
cp deploy/linux/mnemerynne-*.service deploy/linux/mnemerynne-backup.timer /etc/systemd/system/
install -m 755 deploy/linux/mnemerynne-admin /usr/local/bin/
chown -R mnemerynne:mnemerynne /var/lib/mnemerynne /var/backups/mnemerynne
restorecon -R /opt/mnemerynne /var/lib/mnemerynne /var/backups/mnemerynne /etc/mnemerynne /etc/systemd/system
systemctl daemon-reload
systemctl enable --now mnemerynne-generator mnemerynne-embedder mnemerynne-web mnemerynne-worker mnemerynne-backup.timer

step "Done"
cat <<'EOF'
Check:
  curl -s http://127.0.0.1:38200/healthz
  sudo mnemerynne-admin list-users
Everyone has to sign in again (the session cookie was renamed). Existing Claude
tokens keep working; download the extension again from the Claude page once the
new erynne.mcpb is packed and copied to /var/lib/mnemerynne/.
EOF
