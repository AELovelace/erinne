#!/usr/bin/env bash
# Valkey on the box, for live events between the app's processes (mnemerynne/events.py; README "Valkey"):
#   sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh on       install it, a private socket, tell the app
#   sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh off      the app stops using it (Valkey stays installed)
#   sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh status   is it up, and can the app's user reach it
#
# Valkey listens only on a unix socket (no TCP port), readable by the valkey group, and the app's user joins that
# group. Without MNEMERYNNE_VALKEY_URL each process keeps its events to itself, which is fine until a phase of the
# services rewrite moves a live feature (Rooms at the latest) into a process of its own.
# Fedora's package layout is written from memory: every path is checked, and the script says which one is missing.
set -euo pipefail

APP=/opt/mnemerynne
PY=$APP/.venv/bin/python
SERVICE_USER=mnemerynne
ENV=/etc/mnemerynne/mnemerynne.env
SAVED=$ENV.before-front-door                 # front-door.sh's copy: "off" there puts it back, so it gets the URL too
SOCK_DIR=/run/valkey
SOCK=$SOCK_DIR/valkey.sock
URL=unix://$SOCK
CONF_CANDIDATES=(/etc/valkey/valkey.conf /etc/valkey.conf)
TMPFILES=/etc/tmpfiles.d/valkey-mnemerynne.conf

env_set() {                                  # set (or add) one setting in .env file $3 (default the live one)
    local file=${3:-$ENV}
    if grep -qE "^$1=" "$file"; then sed -i "s#^$1=.*#$1=$2#" "$file"; else echo "$1=$2" >> "$file"; fi
}
env_unset() { sed -i "/^$1=/d" "${2:-$ENV}"; }                          # remove one setting from an .env file
env_get() { grep -E "^$1=" "${2:-$ENV}" | tail -1 | cut -d= -f2- || true; }

conf_file() {                                # Valkey's config file, wherever the package put it
    local c
    for c in "${CONF_CANDIDATES[@]}"; do [ -f "$c" ] && { echo "$c"; return 0; }; done
    return 1
}
conf_set() {                                 # set (or add) one directive in Valkey's config ($1 file, $2 key, $3 value)
    if grep -qE "^[[:space:]]*$2[[:space:]]" "$1"; then
        sed -i -E "s#^[[:space:]]*$2[[:space:]].*#$2 $3#" "$1"
    else
        echo "$2 $3" >> "$1"
    fi
}

has_socket() { [ -S "$1" ]; }                 # is there a unix socket at $1 (tests stand in for it)

ping_as_app() {                              # can the app's user reach Valkey with the URL the app will use?
    (cd "$APP" && runuser -u "$SERVICE_USER" -- "$PY" -c \
        'import sys, redis; print(redis.Redis.from_url(sys.argv[1], socket_timeout=3).ping())' "$1") 2>&1
}

app_units() {                                # the app's long-running units that run now (they read the .env)
    systemctl list-units --type=service --state=active --no-legend --plain \
        mnemerynne-web.service mnemerynne-worker.service mnemerynne-sync.service mnemerynne-games.service \
        'mnemerynne@*.service' | awk '{print $1}'
}
restart_app() {
    local units
    units=$(app_units)
    if [ -n "$units" ]; then
        echo "Restarting: $(echo "$units" | tr '\n' ' ')"
        systemctl restart $units
    fi
}

valkey_on() {
    command -v valkey-server >/dev/null || dnf install -y valkey
    command -v valkey-server >/dev/null || { echo "dnf didn't give us valkey-server; nothing changed." >&2; exit 1; }
    getent group valkey >/dev/null || { echo "The valkey package made no 'valkey' group; nothing changed." >&2; exit 1; }
    local conf
    conf=$(conf_file) || { echo "No Valkey config in ${CONF_CANDIDATES[*]}: where did the package put it?" >&2; exit 1; }
    systemctl cat valkey.service >/dev/null 2>&1 || { echo "No valkey.service unit: is the service named differently?" >&2; exit 1; }
    echo "Valkey config: $conf"

    [ -f "$conf.before-mnemerynne" ] || cp -a "$conf" "$conf.before-mnemerynne"     # once: the package's own
    conf_set "$conf" unixsocket "$SOCK"
    conf_set "$conf" unixsocketperm 770
    conf_set "$conf" port 0                                                          # no TCP at all

    # The socket's folder: the unit may make it (RuntimeDirectory); if not, systemd-tmpfiles does at each boot.
    if ! systemctl show -p RuntimeDirectory --value valkey.service | grep -qw valkey; then
        echo "d $SOCK_DIR 0750 valkey valkey -" > "$TMPFILES"
        systemd-tmpfiles --create "$TMPFILES"
        echo "valkey.service makes no $SOCK_DIR; $TMPFILES does."
    fi

    usermod -aG valkey "$SERVICE_USER"                                               # the app may use the socket
    systemctl enable valkey
    systemctl restart valkey
    for _ in $(seq 1 20); do has_socket "$SOCK" && break; sleep 1; done
    if ! has_socket "$SOCK"; then
        echo "Valkey is up but no socket at $SOCK. Its log:" >&2
        journalctl -u valkey -n 20 --no-pager >&2 || true
        command -v ausearch >/dev/null && echo "SELinux denials: sudo ausearch -m avc -ts recent | grep -i valkey" >&2
        exit 1
    fi

    local answer
    answer=$(ping_as_app "$URL") || true
    if [ "$answer" != True ]; then
        echo "The $SERVICE_USER user can't reach $SOCK (the app is unchanged): $answer" >&2
        exit 1
    fi
    echo "$SERVICE_USER reaches Valkey at $URL."

    env_set MNEMERYNNE_VALKEY_URL "$URL"
    [ -f "$SAVED" ] && env_set MNEMERYNNE_VALKEY_URL "$URL" "$SAVED"                 # survives front-door.sh off
    restart_app
    echo "Done: the app's processes share their events through Valkey. Each one logs which bus it uses at start."
}

valkey_off() {
    env_unset MNEMERYNNE_VALKEY_URL
    [ -f "$SAVED" ] && env_unset MNEMERYNNE_VALKEY_URL "$SAVED"
    restart_app
    echo "The app keeps events in each process again. Valkey itself is still installed and running."
}

valkey_status() {
    local conf
    echo "valkey: $(systemctl is-active valkey 2>/dev/null || true) ($(systemctl is-enabled valkey 2>/dev/null || echo 'not installed'))"
    if conf=$(conf_file); then
        echo "config: $conf"
        grep -E "^(unixsocket|unixsocketperm|port)[[:space:]]" "$conf" | sed 's/^/  /' || true
    else
        echo "config: none found in ${CONF_CANDIDATES[*]}"
    fi
    if has_socket "$SOCK"; then ls -l "$SOCK"; else echo "socket: no $SOCK"; fi
    echo "$SERVICE_USER in group valkey: $(id -nG "$SERVICE_USER" 2>/dev/null | grep -qw valkey && echo yes || echo no)"
    local url
    url=$(env_get MNEMERYNNE_VALKEY_URL)
    echo "MNEMERYNNE_VALKEY_URL: ${url:-(not set: events stay in each process)}"
    [ -n "$url" ] && echo "ping as $SERVICE_USER: $(ping_as_app "$url" || true)"
    return 0
}

main() {
    [ "$(id -u)" = 0 ] || { echo "Run it with sudo." >&2; exit 1; }
    case "${1:-}" in
        on) valkey_on ;;
        off) valkey_off ;;
        status) valkey_status ;;
        *) echo "Usage: sudo bash $0 on|off|status" >&2; exit 2 ;;
    esac
}

main "$@"
