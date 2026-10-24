#!/usr/bin/env bash
# Install the local Cockpit directory UI; never provision a domain or modify shares.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { printf 'Run with sudo bash.\n' >&2; exit 1; }
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ -x /usr/bin/samba-tool && -x /usr/bin/testparm && -x /usr/bin/python3 ]] || { printf 'Samba and Python 3 must already be installed.\n' >&2; exit 1; }
[[ -f /usr/share/cockpit/base1/cockpit.js || -f /usr/share/cockpit/base1/cockpit.js.gz ]] || { printf 'Cockpit base1 assets are missing; install cockpit-system first.\n' >&2; exit 1; }
[[ $(testparm -s --parameter-name='server role' 2>/dev/null) == 'active directory domain controller' ]] || { printf 'An existing Samba AD domain controller is required.\n' >&2; exit 1; }
ui=/usr/share/cockpit/sadgirlsclub
helper=/usr/local/libexec/sadgirlsclub-admin
[[ ! -L $ui && ! -L $helper ]] || { printf 'Unexpected installation symlink; inspect it before proceeding.\n' >&2; exit 1; }
backup=$(mktemp -d /root/sadgirlsclub-ui-backup-XXXXXXXX)
for path in "$ui" "$helper"; do
    if [[ -e $path ]]; then cp -a --parents "$path" "$backup/"; fi # Preserve an existing UI before upgrading.
done
install -d -o root -g root -m 755 "$ui" /usr/local/libexec
for file in index.html app.js style.css manifest.json; do
    install -o root -g root -m 644 "$source_root/deploy/cockpit-sadgirlsclub/$file" "$ui/$file"
done
install -o root -g root -m 755 "$source_root/python/sadgirlsclub_admin.py" "$helper"
restorecon -R "$ui"
restorecon "$helper"
printf '{"action":"overview"}' | /usr/bin/python3 -I "$helper" |
    /usr/bin/python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["ok"],r.get("error"); d=r["data"]; print("Directory verified:", d["realm"], "—", len(d["users"]), "users,", len(d["groups"]), "groups")'
systemctl enable --now cockpit.socket
printf '\nInstalled. Open https://192.168.1.5:9090 and enable Administrative access.\n'
printf 'Sign out and back in if Sadgirlsclub Directory is not yet in the sidebar.\n'
printf 'Previous UI files, if any: %s\n' "$backup"
