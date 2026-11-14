#!/usr/bin/env bash
# nginx as the box's front door (README "nginx in front of the app processes"):
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh on        take the app's address, split the paths
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh off       give the address back to the web app
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services  run every service as mnemerynne@<name>
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh accel on|off   nginx sends /static/ and file bytes itself
#   sudo bash /opt/mnemerynne/deploy/linux/front-door.sh status    what listens where, and who answers
#
# "on" keeps the address and port the web app has now (MNEMERYNNE_HOST/PORT: where the reverse proxy sends to),
# moves the web app to 127.0.0.1:38210 (the services follow MNEMERYNNE_HOST to 127.0.0.1) and has nginx listen
# there instead. The nginx config is written from the service registry (mnemerynne/services/nginx.py), so a
# service added there gets its paths the next time this runs. If anything doesn't come up, it says which step
# and puts everything back.
# "services" moves each registry service from its first named unit (mnemerynne-sync, ...) to the template unit
# mnemerynne@.service, one at a time, and puts the old unit back if the new one doesn't answer.
# "accel on" (Phase 1) has nginx serve /static/ from disk and send the files the app hands it (X-Accel-Redirect,
# mnemerynne/web/sendfile.py): nginx's workers run as the service user (the share's ACL names only it), SELinux is
# told nginx may read those folders, each folder is probed through nginx, and only then is MNEMERYNNE_ACCEL set.
# "accel off" is the B-to-A switch: the app sends every file itself again.
# A script rather than pasted lines: long pasted lines got cut short in the terminal.
set -euo pipefail

APP=/opt/mnemerynne
PY=$APP/.venv/bin/python
SERVICE_USER=mnemerynne
ENV=/etc/mnemerynne/mnemerynne.env
SAVED=$ENV.before-front-door                     # the .env as it was before "on": "off" puts it back
CONF=/etc/nginx/conf.d/mnemerynne-front.conf
UNIT_DIR=/etc/systemd/system
TEMPLATE=$APP/deploy/linux/mnemerynne@.service
WEB_PORT=38210
WAIT=90                                          # seconds to wait for a health answer
NGINX_CONF=/etc/nginx/nginx.conf
SEMODULE=mnemerynne_accel                        # SELinux: nginx may read the folders the app hands it
PROBE=.mnemerynne-accel-probe                    # written in each folder, fetched through nginx, removed
STATIC_PROBE=/static/icons/icon-192.png

env_get() { grep -E "^$1=" "${2:-$ENV}" | tail -1 | cut -d= -f2- || true; }   # a setting from the .env (or $2)
env_set() {                                                             # set (or add) one setting in the .env
    if grep -qE "^$1=" "$ENV"; then sed -i "s#^$1=.*#$1=$2#" "$ENV"; else echo "$1=$2" >> "$ENV"; fi
}

app_py() { (cd "$APP" && runuser -u "$SERVICE_USER" -- "$PY" "$@"); }  # the app's Python, as the service user
registry_services() {                                                   # "name port" per registry service
    app_py -c 'from mnemerynne.services import registry
for s in registry.SERVICES.values(): print(s.name, s.port)'
}
services_or_default() {                     # the registry's services; the first two if the app can't be asked
    registry_services 2>/dev/null || printf 'sync 38201\ngames 38202\n'
}

running() {                                 # which of the app's services run: named units and mnemerynne@ ones
    systemctl list-units --type=service --state=active --no-legend --plain \
        mnemerynne-web.service mnemerynne-sync.service mnemerynne-games.service 'mnemerynne@*.service' \
        | awk '{print $1}' | sed 's/\.service$//'
}
unit_of() {                                 # the unit running service $1 now (template first), or nothing
    local unit
    for unit in "mnemerynne@$1" "mnemerynne-$1"; do
        if systemctl is-active --quiet "$unit"; then echo "$unit"; return 0; fi
    done
}
local_host() {                              # where the services listen, as seen from this box
    case "${1:-}" in ""|0.0.0.0|"::"|"[::]") echo 127.0.0.1 ;; *) echo "$1" ;; esac
}
up() {                                      # wait for a health answer from $1 (and, given $2, from that role)
    local body
    for _ in $(seq 1 "$WAIT"); do
        body=$(curl -s --max-time 3 "$1" || true)
        if [[ $body == *'"ok"'* ]] && { [ -z "${2:-}" ] || [[ $body == *"\"role\":\"$2\""* ]]; }; then
            return 0                        # {"status":"ok","role":"sync"}: the right process, not a leftover
        fi
        sleep 1
    done
    return 1
}

accel_set() { case "$(env_get MNEMERYNNE_ACCEL)" in 1|true|True|TRUE|yes|on) return 0 ;; esac; return 1; }
gen_conf() {                                # print the config for listen address $1; $2 = accel | plain
    if [ "$2" = accel ]; then
        app_py -m mnemerynne.services.nginx --listen "$1" --web "127.0.0.1:$WEB_PORT" --accel --env-file - < "$ENV"
    else
        app_py -m mnemerynne.services.nginx --listen "$1" --web "127.0.0.1:$WEB_PORT"
    fi
}
write_conf() {                              # the nginx config for listen address $1, checked; $2: accel | plain |
    local new old mode=${2:-auto}           # auto (the default: accel when MNEMERYNNE_ACCEL is set)
    if [ "$mode" = auto ]; then if accel_set; then mode=accel; else mode=plain; fi; fi
    new=$(mktemp); old=$(mktemp)
    if ! gen_conf "$1" "$mode" > "$new" || [ ! -s "$new" ]; then
        rm -f "$new" "$old"; echo "Couldn't write the nginx config from the registry; nothing changed." >&2; return 1
    fi
    if [ -f "$CONF" ]; then cp -a "$CONF" "$old"; else rm -f "$old"; fi                # the one that worked
    install -m 644 "$new" "$CONF"; rm -f "$new"
    command -v restorecon >/dev/null && restorecon "$CONF" 2>/dev/null || true        # SELinux label for nginx
    if ! nginx -t; then                                                                 # put the old one back
        if [ -f "$old" ]; then cp -a "$old" "$CONF"; else rm -f "$CONF"; fi
        rm -f "$old"; echo "nginx didn't accept the new config; the old one is back." >&2; return 1
    fi
    rm -f "$old"
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

front_on() {
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
    if command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ]; then   # SELinux: nginx on this port,
        semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null \
            || semanage port -m -t http_port_t -p tcp "$PORT"
        setsebool -P httpd_can_network_connect 1                                     # and reaching the app
    fi
    write_conf "$HOST:$PORT" || exit 1                                              # nginx takes the app's address

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
    local name port unit
    while read -r name port; do                                 # every service that runs answers on its port
        unit=$(unit_of "$name")
        [ -n "$unit" ] || continue
        up "http://127.0.0.1:$port/healthz" "$name" || fail "$name on 127.0.0.1:$port" "$unit"
    done < <(services_or_default)
    systemctl enable --now nginx || fail "starting nginx" nginx
    up "http://$HOST:$PORT/healthz" || fail "nginx answering on $HOST:$PORT" nginx
    echo "Done: nginx on $HOST:$PORT -> each service's paths to its port, everything else to 127.0.0.1:$WEB_PORT."
}

move_service() {          # service $1 (port $2) from mnemerynne-$1 to mnemerynne@$1; the old unit back on failure
    local name=$1 port=$2 old="mnemerynne-$1" new="mnemerynne@$1" host had_old=no
    host=$(local_host "$(env_get MNEMERYNNE_HOST)")
    if systemctl is-enabled --quiet "$new" 2>/dev/null && systemctl is-active --quiet "$new"; then
        echo "$new: already running."; return 0
    fi
    if systemctl is-enabled --quiet "$old" 2>/dev/null; then
        had_old=yes
        systemctl disable --now "$old"
    fi
    echo "$new: starting (waits for the web app's migrations, then answers on $host:$port)."
    if systemctl enable --now "$new" && up "http://$host:$port/healthz" "$name"; then
        echo "$new: answering."; return 0
    fi
    echo "$new didn't answer on $host:$port:" >&2
    journalctl -u "$new" -n 15 --no-pager >&2 || true
    systemctl disable --now "$new" || true
    if [ "$had_old" = yes ]; then
        systemctl enable --now "$old" || true
        echo "$old is back on." >&2
    fi
    return 1
}

services() {
    [ -f "$TEMPLATE" ] || { echo "No $TEMPLATE: update the app first (mnemerynne-admin update)." >&2; exit 1; }
    local list name port f
    list=$(registry_services) || { echo "Couldn't read the service registry with $PY." >&2; exit 1; }
    install -m 644 "$TEMPLATE" "$UNIT_DIR/mnemerynne@.service"
    mkdir -p "$UNIT_DIR/mnemerynne@.service.d"
    for f in "$UNIT_DIR"/mnemerynne-web.service.d/*.conf; do   # the web app's drop-ins (paths, limits) for all
        [ -e "$f" ] || continue
        if [ -e "$UNIT_DIR/mnemerynne@.service.d/$(basename "$f")" ]; then
            echo "Kept the existing mnemerynne@.service.d/$(basename "$f")."
        else
            cp -a "$f" "$UNIT_DIR/mnemerynne@.service.d/"
            echo "Copied the web app's drop-in $(basename "$f") to mnemerynne@.service.d/."
        fi
    done
    systemctl daemon-reload
    while read -r name port; do
        [ -n "$name" ] || continue
        move_service "$name" "$port" || { echo "Stopped at $name; the services before it run as mnemerynne@." >&2; exit 1; }
    done <<< "$list"
    if [ -f "$SAVED" ]; then                                    # the front door is on: its routes from the registry
        local listen                                            # where the reverse proxy sends: the saved .env's
        listen="$(env_get MNEMERYNNE_HOST "$SAVED"):$(env_get MNEMERYNNE_PORT "$SAVED")"
        write_conf "$listen" || exit 1
        systemctl reload nginx
        echo "nginx on $listen: routes rewritten from the registry and reloaded."
    fi
    echo "Done: $(echo "$list" | awk '{printf "mnemerynne@%s ", $1}')run from the template unit."
}

status() {
    local list ports host name port
    list=$(services_or_default)
    ports=$(echo "$list" | awk '{printf "|%s", $2}')
    host=$(local_host "$(env_get MNEMERYNNE_HOST)")
    echo "nginx: $(systemctl is-active nginx || true)   saved .env: $([ -f "$SAVED" ] && echo yes || echo no)" \
         "  accel: $(accel_set && echo on || echo off)$(grep -q '/_accel/' "$CONF" 2>/dev/null && echo ' (nginx has its locations)')"
    echo "running: $(running | tr '\n' ' ')"
    ss -ltnp | grep -E ":(38200|$WEB_PORT$ports) " || true
    while read -r name port; do
        printf "%-45s %s\n" "http://$host:$port/healthz" "$(curl -s --max-time 3 "http://$host:$port/healthz" || echo '(no answer)')"
    done <<< "$list"
    for url in "http://127.0.0.1:$WEB_PORT/healthz" "http://$(env_get MNEMERYNNE_HOST):$(env_get MNEMERYNNE_PORT)/healthz"; do
        printf "%-45s %s\n" "$url" "$(curl -s --max-time 3 "$url" || echo '(no answer)')"
    done
}

front_listen() {                            # where nginx listens: the address in the .env saved by "on"
    echo "$(env_get MNEMERYNNE_HOST "$SAVED"):$(env_get MNEMERYNNE_PORT "$SAVED")"
}
accel_roots() { app_py -m mnemerynne.services.nginx --roots --env-file - < "$ENV"; }   # "key path" per line

nginx_user() {                              # nginx's workers as $1, keeping nginx.conf as it was, once
    [ -f "$NGINX_CONF.before-accel" ] || cp -a "$NGINX_CONF" "$NGINX_CONF.before-accel"
    if grep -qE '^[[:space:]]*user[[:space:]]' "$NGINX_CONF"; then
        sed -i -E "s/^[[:space:]]*user[[:space:]][^;]*;/user $1;/" "$NGINX_CONF"
    else
        sed -i "1i user $1;" "$NGINX_CONF"
    fi
}
nginx_user_back() {                         # nginx.conf as it was before "accel on"
    if [ -f "$NGINX_CONF.before-accel" ]; then cp -a "$NGINX_CONF.before-accel" "$NGINX_CONF"; fi
}

selinux_allow() {                           # SELinux: let nginx (httpd_t) read the file types in folders $@
    command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ] || return 0
    local types t dir
    types=$(for p in "$@"; do find "$p" -maxdepth 4 \( -type d -o -type f -o -type l \) -printf '%Z\n' 2>/dev/null; done \
            | awk -F: 'NF >= 3 {print $3}' | sort -u)
    [ -n "$types" ] || { echo "SELinux: no file types found under $*." >&2; return 1; }
    command -v checkmodule >/dev/null || dnf install -y checkpolicy >/dev/null
    dir=$(mktemp -d)
    {
        echo "module $SEMODULE 1.0;"
        echo "require {"
        echo "    type httpd_t;"
        for t in $types; do echo "    type $t;"; done
        echo "    class file { getattr open read map ioctl };"
        echo "    class dir { getattr open read search };"
        echo "    class lnk_file { getattr read };"
        echo "}"
        for t in $types; do
            echo "allow httpd_t $t:file { getattr open read map ioctl };"
            echo "allow httpd_t $t:dir { getattr open read search };"
            echo "allow httpd_t $t:lnk_file { getattr read };"
        done
    } > "$dir/$SEMODULE.te"
    if ! { checkmodule -M -m -o "$dir/$SEMODULE.mod" "$dir/$SEMODULE.te" >/dev/null \
           && semodule_package -o "$dir/$SEMODULE.pp" -m "$dir/$SEMODULE.mod" \
           && semodule -i "$dir/$SEMODULE.pp"; }; then
        rm -rf "$dir"; echo "SELinux: couldn't install $SEMODULE." >&2; return 1
    fi
    rm -rf "$dir"
    echo "SELinux: nginx may read $(echo $types | tr ' ' ',') ($SEMODULE)."
}
selinux_back() { if command -v semodule >/dev/null; then semodule -r "$SEMODULE" >/dev/null 2>&1 || true; fi; }

probe() {                                   # can nginx on $1 send from folder $3 (key $2)?
    local listen=$1 key=$2 root=$3 got
    if [ "$key" = static ]; then
        got=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$listen$STATIC_PROBE" || true)
        [ "$got" = 200 ] && return 0
        echo "nginx couldn't send $STATIC_PROBE from $root (HTTP $got)." >&2; return 1
    fi
    runuser -u "$SERVICE_USER" -- sh -c 'echo mnemerynne accel probe > "$1"' _ "$root/$PROBE" \
        || { echo "The $SERVICE_USER user can't write in $root." >&2; return 1; }
    got=$(curl -s --max-time 5 "http://$listen/_accel-probe/$key" || true)
    rm -f "$root/$PROBE"
    [ "$got" = "mnemerynne accel probe" ] && return 0
    echo "nginx couldn't read $root (key $key): ${got:-no answer}" >&2
    return 1
}

restart_app() {                             # the running app units, then the web app's answer
    local run
    run=$(running)
    systemctl restart ${run:-mnemerynne-web}
    up "http://127.0.0.1:$WEB_PORT/healthz" || { echo "The web app didn't come back on 127.0.0.1:$WEB_PORT." >&2; return 1; }
}

accel_undo() {                              # nginx back to proxying only (the app sends the files)
    write_conf "$1" plain || true
    nginx_user_back
    systemctl restart nginx || true
    selinux_back
}

accel_on() {
    [ -f "$SAVED" ] || { echo "The front door is off: run 'on' first." >&2; exit 1; }
    local listen roots key path failed=no
    listen=$(front_listen)
    roots=$(accel_roots) || { echo "Couldn't read the app's folders from $ENV." >&2; exit 1; }
    echo "nginx will send files from:"; echo "$roots" | sed 's/^/  /'
    usermod -aG nginx "$SERVICE_USER" 2>/dev/null || true             # nginx's temp folders, for its workers
    nginx_user "$SERVICE_USER"
    selinux_allow $(echo "$roots" | awk '{print $2}') || { accel_undo "$listen"; exit 1; }
    write_conf "$listen" accel || { accel_undo "$listen"; exit 1; }
    systemctl restart nginx || { echo "nginx didn't restart." >&2; accel_undo "$listen"; exit 1; }
    while read -r key path; do
        [ -n "$key" ] || continue
        probe "$listen" "$key" "$path" || failed=yes
    done <<< "$roots"
    if [ "$failed" = yes ]; then
        if command -v ausearch >/dev/null; then echo "SELinux denials, if any: sudo ausearch -m avc -c nginx -ts recent" >&2; fi
        echo "Putting nginx back; the app still sends every file." >&2
        accel_undo "$listen"
        exit 1
    fi
    env_set MNEMERYNNE_ACCEL 1
    restart_app || { echo "Turning accel off again." >&2; accel_off; exit 1; }
    echo "Done: nginx sends /static/ and the app's files on $listen. 'accel off' hands them back to the app."
}

accel_off() {
    sed -i '/^MNEMERYNNE_ACCEL=/d' "$ENV"
    restart_app || true
    if [ -f "$SAVED" ]; then accel_undo "$(front_listen)"; else nginx_user_back; selinux_back; fi
    echo "Accel is off: the app sends every file itself."
}

main() {
    [ "$(id -u)" = 0 ] || { echo "Run it with sudo." >&2; exit 1; }
    case "${1:-}" in
        on) front_on ;;
        off) off ;;
        services) services ;;
        accel)
            case "${2:-}" in
                on) accel_on ;;
                off) accel_off ;;
                *) echo "Usage: sudo bash $0 accel on|off" >&2; exit 2 ;;
            esac ;;
        status) status ;;
        *) echo "Usage: sudo bash $0 on|off|services|accel on|off|status" >&2; exit 2 ;;
    esac
}

main "$@"
