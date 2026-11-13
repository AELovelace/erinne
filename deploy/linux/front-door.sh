#!/usr/bin/env bash
# nginx as the box's front door (README "nginx in front of the app processes"):
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh on       take the app's address, split the paths
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh off      give the address back to the web app
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh status   what listens where, and who answers
#
# "on" keeps the address and port the web app has now (MNEMERYNNE_HOST/PORT: where the reverse proxy sends to),
# moves the web app to 127.0.0.1:38210 (the backup and games processes follow MNEMERYNNE_HOST to 127.0.0.1) and
# has nginx listen there instead. If anything doesn't come up, it says which step and puts everything back.
# A script rather than pasted lines: long pasted lines got cut short in the terminal.
set -euo pipefail

ENV=/etc/mnemerynne/mnemerynne.env
SAVED=$ENV.before-front-door                     # the .env as it was before "on": "off" puts it back
SRC=/opt/mnemerynne/deploy/linux/nginx-mnemerynne-front.conf
CONF=/etc/nginx/conf.d/mnemerynne-front.conf
WEB_PORT=38210
ROLES=(mnemerynne-web mnemerynne-sync mnemerynne-games)

[ "$(id -u)" = 0 ] || { echo "Run it with sudo." >&2; exit 1; }

env_get() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2- || true; }   # a setting from the .env, or empty
env_set() {                                                             # set (or add) one setting in the .env
    if grep -qE "^$1=" "$ENV"; then sed -i "s#^$1=.*#$1=$2#" "$ENV"; else echo "$1=$2" >> "$ENV"; fi
}
running() { systemctl list-units --type=service --state=active --no-legend "${ROLES[@]/%/.service}" \
            | awk '{print $1}' | sed 's/\.service$//'; }                # which of the app's services run
up() {                                                                  # wait up to 90 s for a health answer
    for _ in $(seq 1 90); do
        curl -s --max-time 3 "$1" | grep -q '"ok"' && return 0
        sleep 1
    done
    return 1
}

off() {
    systemctl disable --now nginx 2>/dev/null || true
    if [ -f "$SAVED" ]; then
        cp -a "$SAVED" "$ENV"
        rm -f "$SAVED"
    fi
    RUN=$(running); systemctl restart ${RUN:-mnemerynne-web}
    echo "The web app has its address back: $(env_get MNEMERYNNE_HOST):$(env_get MNEMERYNNE_PORT); nginx is off."
}

fail() {                                                                # say what went wrong, then undo it all
    echo "Step failed: $1" >&2
    [ -n "${2:-}" ] && journalctl -u "$2" -n 15 --no-pager >&2 || true
    echo "Putting everything back." >&2
    off
    exit 1
}

case "${1:-}" in
on)
    if [ -f "$SAVED" ]; then echo "Already on (run 'off' first to start again)."; exit 0; fi
    HOST=$(env_get MNEMERYNNE_HOST); PORT=$(env_get MNEMERYNNE_PORT)
    HOST=${HOST:-127.0.0.1}; PORT=${PORT:-38200}
    if [ "$HOST" = 127.0.0.1 ]; then
        echo "MNEMERYNNE_HOST is 127.0.0.1: nothing outside this box sends here; use Caddy or nginx-mnemerynne.conf." >&2
        exit 1
    fi
    echo "Front door: nginx on $HOST:$PORT; the web app moves to 127.0.0.1:$WEB_PORT."

    command -v nginx >/dev/null || dnf install -y nginx >/dev/null
    command -v semanage >/dev/null || dnf install -y policycoreutils-python-utils >/dev/null
    sed "s#192\.168\.1\.5:38200#$HOST:$PORT#" "$SRC" > "$CONF"                  # nginx takes the app's address
    if command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ]; then   # SELinux: nginx on this port,
        semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null \
            || semanage port -m -t http_port_t -p tcp "$PORT"
        setsebool -P httpd_can_network_connect 1                                     # and reaching the app
    fi
    nginx -t || { rm -f "$CONF"; echo "nginx didn't accept the config; nothing changed." >&2; exit 1; }

    cp -a "$ENV" "$SAVED"
    TRUST=$(env_get FORWARDED_ALLOW_IPS)                    # the reverse proxy trusted now, plus this box's nginx
    case "$TRUST" in
        "*") ;;                                             # trusts every address already
        *127.0.0.1*) ;;
        "") TRUST=127.0.0.1 ;;
        *) TRUST="127.0.0.1,$TRUST" ;;
    esac
    env_set MNEMERYNNE_HOST 127.0.0.1
    env_set MNEMERYNNE_PORT "$WEB_PORT"
    env_set FORWARDED_ALLOW_IPS "$TRUST"
    echo "Trusting forwarded addresses from: $TRUST"

    RUN=$(running); systemctl restart ${RUN:-mnemerynne-web} || fail "restarting the app" mnemerynne-web
    up "http://127.0.0.1:$WEB_PORT/healthz" || fail "the web app on 127.0.0.1:$WEB_PORT" mnemerynne-web
    if systemctl is-active --quiet mnemerynne-sync; then
        up http://127.0.0.1:38201/healthz || fail "the backup processes on 127.0.0.1:38201" mnemerynne-sync
    fi
    if systemctl is-active --quiet mnemerynne-games; then
        up http://127.0.0.1:38202/healthz || fail "the games process on 127.0.0.1:38202" mnemerynne-games
    fi
    systemctl enable --now nginx || fail "starting nginx" nginx
    up "http://$HOST:$PORT/healthz" || fail "nginx answering on $HOST:$PORT" nginx
    echo "Done: nginx on $HOST:$PORT -> the web app (127.0.0.1:$WEB_PORT), /api/sync/ -> 38201, games -> 38202."
    ;;
off)
    off
    ;;
status)
    echo "nginx: $(systemctl is-active nginx)   saved .env: $([ -f "$SAVED" ] && echo yes || echo no)"
    ss -ltnp | grep -E ":(38200|38201|38202|$WEB_PORT) " || true
    for url in "http://127.0.0.1:$WEB_PORT/healthz" http://127.0.0.1:38201/healthz http://127.0.0.1:38202/healthz \
               "http://$(env_get MNEMERYNNE_HOST):$(env_get MNEMERYNNE_PORT)/healthz"; do
        printf "%-45s %s\n" "$url" "$(curl -s --max-time 3 "$url" || echo '(no answer)')"
    done
    ;;
*)
    echo "Usage: sudo bash $0 on|off|status" >&2
    exit 2
    ;;
esac
