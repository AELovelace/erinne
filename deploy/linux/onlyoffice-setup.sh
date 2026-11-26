#!/usr/bin/env bash
# ONLYOFFICE Docs Community on the box, for viewing and editing Office files in the browser (README "ONLYOFFICE"):
#   sudo bash /opt/mnemerynne/deploy/linux/onlyoffice-setup.sh install        podman, secret, container, nginx, tell the app
#   sudo bash /opt/mnemerynne/deploy/linux/onlyoffice-setup.sh update 9.4.0.1 pull a new image tag and restart on it
#   sudo bash /opt/mnemerynne/deploy/linux/onlyoffice-setup.sh off            stop it, unhook nginx and the app (keeps /srv/onlyoffice)
#   sudo bash /opt/mnemerynne/deploy/linux/onlyoffice-setup.sh status         is it up, healthy, and wired in
#
# The container runs in rootful podman as a quadlet (onlyoffice/mnemerynne-onlyoffice.container), published on
# 127.0.0.1:8090 only. Browsers reach it at https://<host>/onlyoffice/ through the edge nginx (nginx-onlyoffice.conf),
# and it reaches the app back at https://<host>/api/office/ds/ through the same edge. It needs the edge nginx
# (README "nginx holds HTTPS on this box") and about 4 GB of free RAM (the box has 20 GB since its upgrade).
# The secret is generated here and never printed: it lands in two root-only files and nowhere else.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)       # deploy/linux, wherever the repo is checked out
ENV_FILE=/etc/mnemerynne/mnemerynne.env                  # the app's settings
SAVED=$ENV_FILE.before-front-door                        # front-door.sh's copy: "off" there puts it back, so it gets the settings too
OO_ENV=/etc/mnemerynne/onlyoffice.env                    # the container's settings (the JWT secret), mode 0600
UNIT=mnemerynne-onlyoffice                               # the quadlet's unit name (from the file name)
QUADLET_SRC=$HERE/onlyoffice/$UNIT.container
QUADLET_DST=/etc/containers/systemd/$UNIT.container
SNIPPET_SRC=$HERE/nginx-onlyoffice.conf
SNIPPET_DIR=/etc/nginx/mnemerynne-edge.d                 # the edge includes *.conf from here inside its 443 block
SNIPPET_DST=$SNIPPET_DIR/onlyoffice.conf
EDGE_CONF=/etc/nginx/conf.d/mnemerynne-edge.conf         # without the edge there's nothing to hang /onlyoffice/ on
DATA=/srv/onlyoffice                                     # everything the container keeps (never removed by this script)
PORT=8090                                                # PublishPort=127.0.0.1:8090:80
INTERNAL_URL=http://127.0.0.1:$PORT/
IMAGE_REPO=docker.io/onlyoffice/documentserver
MIN_RAM_GB=12                                            # safety check: below this there's no room for 4 GB more
HEALTH_WAIT=900                                          # seconds: the first start unpacks fonts and caches for minutes

# ------------------------------------------------------------------ helpers (same shape as setup.sh)
step() { printf '\n== %s\n' "$*"; }                      # a heading for each stage
note() { printf '   %s\n' "$*"; }                        # an indented line under it
warn() { printf '   WARNING: %s\n' "$*" >&2; }
die() { printf 'onlyoffice-setup: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

env_value() {  # env_value KEY DEFAULT [FILE]: a value from an .env file (the last active line wins)
    local file=${3:-$ENV_FILE} v=""
    [[ -r $file ]] && v=$(sed -n "s/^$1=//p" "$file" | tail -n 1 | tr -d '"'"'")
    printf '%s' "${v:-$2}"
}
set_env() {  # set_env KEY VALUE [FILE]: replace the active or commented line, else append
    # The value goes to awk through the environment, not its command line, so a secret never shows in `ps`.
    local key=$1 file=${3:-$ENV_FILE}
    if grep -qE "^#?$key=" "$file"; then
        SET_ENV_VALUE=$2 awk -v k="$key" 'BEGIN { done = 0 }
             !done && $0 ~ ("^#?" k "=") { print k "=" ENVIRON["SET_ENV_VALUE"]; done = 1; next } { print }' \
             "$file" > "$file.tmp"
        cat "$file.tmp" > "$file"                        # keeps the file's owner and mode
        rm -f "$file.tmp"
    else
        printf '%s=%s\n' "$key" "$2" >> "$file"
    fi
}
set_app_env() {  # set_app_env KEY VALUE: in the live .env and in front-door.sh's saved copy, if there is one
    set_env "$1" "$2" "$ENV_FILE"
    [[ -f $SAVED ]] && set_env "$1" "$2" "$SAVED"
    return 0
}

ram_gb() { awk '/^MemTotal:/ { printf "%d", $2 / 1024 / 1024 + 0.5 }' /proc/meminfo; }   # total RAM, rounded GB
ram_check() {  # warn (and ask, at a terminal) when the box is too small to spare 4 GB
    local gb
    gb=$(ram_gb)
    if (( gb < MIN_RAM_GB )); then
        warn "this box has only ${gb} GB of RAM. ONLYOFFICE needs about 4 GB free on top of the app and the models,"
        warn "or expect the OOM killer. (The Mnemerynne box has 20 GB: this is a safety check.)"
        if [[ -t 0 ]]; then                              # someone's at the keyboard: let them stop here
            local answer=""
            read -r -p "   Carry on anyway? [y/N] " answer || true
            [[ $answer =~ ^[yY] ]] || die "stopped before changing anything."
        fi
    else
        note "RAM: ${gb} GB (fine)"
    fi
}

public_host() {  # the bare hostname from MNEMERYNNE_PUBLIC_URL (no scheme, port or path)
    local url host
    url=$(env_value MNEMERYNNE_PUBLIC_URL "")
    [[ -n $url ]] || die "MNEMERYNNE_PUBLIC_URL isn't set in $ENV_FILE: set it to https://YOUR-HOSTNAME first."
    host=${url#*://}                                     # drop https://
    host=${host%%/*}                                     # drop any path
    host=${host%%:*}                                     # drop any port
    [[ $host =~ ^[A-Za-z0-9.-]+$ ]] || die "can't read a hostname out of MNEMERYNNE_PUBLIC_URL=$url"
    printf '%s' "$host"
}

healthy() {  # does the document server say it's ready? (it answers the literal word true)
    [[ $(curl -s --max-time 5 "http://127.0.0.1:$PORT/healthcheck" 2>/dev/null || true) == true ]]
}
wait_healthy() {  # poll the healthcheck for up to HEALTH_WAIT seconds, with a dot every 10 s
    local waited=0
    printf '   waiting for http://127.0.0.1:%s/healthcheck (the first start takes a few minutes) ' "$PORT"
    until healthy; do
        if (( waited >= HEALTH_WAIT )); then
            printf '\n'
            warn "no healthy answer after ${HEALTH_WAIT}s. Its log:"
            journalctl -u "$UNIT" -n 30 --no-pager >&2 || true
            die "the container isn't healthy; nginx wasn't touched and the app wasn't restarted. Look, then run install again (or off)."
        fi
        printf '.'
        sleep 10
        waited=$((waited + 10))
    done
    printf ' ok\n'
}

app_restart() {  # restart the units that read the ONLYOFFICE settings, only if they're running
    local u
    for u in mnemerynne-web mnemerynne@files; do
        if systemctl is-active --quiet "$u"; then
            note "restarting $u"
            systemctl restart "$u"
        fi
    done
}

nginx_apply() {  # check nginx's whole config and reload it; on a bad config remove $1 (the new snippet) and stop
    local out
    if out=$(nginx -t 2>&1); then
        systemctl reload nginx
        note "nginx -t passed; nginx reloaded"
    else
        printf '%s
' "$out" | sed 's/^/   /' >&2              # nginx's own words
        [[ -n ${1:-} ]] && rm -f "$1"                    # undo the snippet so nginx keeps its last good config
        die "nginx -t failed (above); nothing reloaded."
    fi
}

selinux_port() {  # let nginx connect to 8090 under SELinux (the same as the README does for 38200)
    have getenforce && [[ $(getenforce) != Disabled ]] || return 0
    have semanage || dnf install -y policycoreutils-python-utils
    semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null \
        || semanage port -m -t http_port_t -p tcp "$PORT" 2>/dev/null \
        || warn "couldn't label port $PORT for nginx; if /onlyoffice/ answers 502, check: sudo ausearch -m avc -ts recent"
}

installed_image() {  # the Image= line of the installed quadlet
    sed -n 's/^Image=//p' "$QUADLET_DST" 2>/dev/null | tail -n 1
}

# ------------------------------------------------------------------ install
oo_install() {
    step "Checks"
    [[ -f $QUADLET_SRC && -f $SNIPPET_SRC ]] || die "missing $QUADLET_SRC or $SNIPPET_SRC: run it from the repo's deploy/linux."
    [[ -f $ENV_FILE ]] || die "no $ENV_FILE: set the app up first (setup.sh)."
    [[ -f $EDGE_CONF ]] || die "no $EDGE_CONF: ONLYOFFICE hangs off the edge nginx (README \"nginx holds HTTPS on this box\")."
    local host
    host=$(public_host)
    note "public host: $host"
    ram_check

    step "podman"
    have podman || dnf install -y podman                 # rootful podman; quadlet comes with it
    have podman || die "dnf didn't give us podman; nothing changed."
    [[ -x /usr/libexec/podman/quadlet ]] || die "podman has no quadlet generator (/usr/libexec/podman/quadlet): it's too old."
    note "$(podman --version)"

    step "Secret"
    install -d -m 755 /etc/mnemerynne
    local secret
    secret=$(env_value JWT_SECRET "" "$OO_ENV")          # a second install keeps the secret it made the first time
    if [[ $secret =~ ^[0-9a-f]{64}$ ]]; then
        note "keeping the secret already in $OO_ENV"
    else
        secret=$(openssl rand -hex 32)                   # 64 hex characters; never echoed
        note "made a new secret"
    fi
    (   umask 077                                        # the file is born private, never world-readable for a moment
        printf 'JWT_ENABLED=true\nJWT_SECRET=%s\nJWT_HEADER=Authorization\nALLOW_PRIVATE_IP_ADDRESS=true\n' "$secret" \
            > "$OO_ENV.tmp"
    )
    chmod 600 "$OO_ENV.tmp" && chown root:root "$OO_ENV.tmp"
    mv -f "$OO_ENV.tmp" "$OO_ENV"                        # swapped in whole
    note "$OO_ENV written (mode 0600)"

    step "App settings"
    set_app_env MNEMERYNNE_ONLYOFFICE_URL "https://$host/onlyoffice/"      # where browsers load the editor from
    set_app_env MNEMERYNNE_ONLYOFFICE_JWT_SECRET "$secret"                  # the same secret, so both sides sign alike
    set_app_env MNEMERYNNE_ONLYOFFICE_INTERNAL_URL "$INTERNAL_URL"          # how the app itself reaches the container
    if [[ -z $(env_value MNEMERYNNE_ONLYOFFICE_APP_URL "") ]]; then
        set_app_env MNEMERYNNE_ONLYOFFICE_APP_URL ""     # empty: the container calls back on MNEMERYNNE_PUBLIC_URL
    else
        note "keeping MNEMERYNNE_ONLYOFFICE_APP_URL=$(env_value MNEMERYNNE_ONLYOFFICE_APP_URL "") (set by hand)"
    fi
    unset secret                                         # done with it in this shell
    note "MNEMERYNNE_ONLYOFFICE_URL, _JWT_SECRET and _INTERNAL_URL set in $ENV_FILE"

    step "Container"
    install -d -m 755 "$DATA" "$DATA"/{data,logs,lib,fonts,db}   # the volumes; :Z in the quadlet labels them
    local image
    image=$(sed -n 's/^Image=//p' "$QUADLET_SRC" | tail -n 1)
    if [[ -f $QUADLET_DST ]]; then                       # already installed (maybe updated since): keep its tag
        image=$(installed_image)
        note "keeping the installed image $image"
    fi
    note "pulling $image (about 1 GB the first time)"
    podman pull "$image"                                 # now, so the unit's start isn't a long silent download
    install -d -m 755 "$(dirname "$QUADLET_DST")"
    sed -e "s/@HOST@/$host/g" -e "s#^Image=.*#Image=$image#" "$QUADLET_SRC" > "$QUADLET_DST"   # host and tag filled in
    chmod 644 "$QUADLET_DST"
    systemctl daemon-reload                              # the quadlet generator writes $UNIT.service from it
    systemctl cat "$UNIT.service" >/dev/null 2>&1 \
        || die "systemd made no $UNIT.service from $QUADLET_DST: try /usr/libexec/podman/quadlet -dryrun"
    systemctl restart "$UNIT.service"                    # start (or restart onto new settings); WantedBy= starts it at boot
    wait_healthy

    step "nginx"
    selinux_port
    install -d -m 755 "$SNIPPET_DIR"
    install -m 644 "$SNIPPET_SRC" "$SNIPPET_DST"
    have restorecon && restorecon -R "$SNIPPET_DIR" || true   # nginx may read it under SELinux
    nginx_apply "$SNIPPET_DST"

    step "App"
    app_restart

    step "Done"
    note "Editor: https://$host/onlyoffice/ (signed-in people only). Turn it on in Admin, then open a .docx."
    note "The free edition allows 20 people editing at once."
}

# ------------------------------------------------------------------ update
oo_update() {
    local tag=${1:-}
    [[ $tag =~ ^[0-9]+(\.[0-9]+){1,3}$ ]] || die "usage: update <tag>, a version like 9.4.0.1 (see hub.docker.com/r/onlyoffice/documentserver/tags)"
    [[ -f $QUADLET_DST ]] || die "not installed ($QUADLET_DST missing): run install first."
    local old new=$IMAGE_REPO:$tag
    old=$(installed_image)
    step "Update $old -> $new"
    podman pull "$new"                                   # fails here (nothing changed) if the tag doesn't exist
    sed -i "s#^Image=.*#Image=$new#" "$QUADLET_DST"      # re-pin the installed copy only; the repo's stays as it is
    systemctl daemon-reload
    systemctl restart "$UNIT.service"                    # open documents reconnect; unsaved changes live in $DATA/db
    wait_healthy
    note "now on $new (was $old). To go back: sudo bash $0 update ${old##*:}"
    note "Also bump Image= in deploy/linux/onlyoffice/$UNIT.container so a fresh install gets the same tag."
}

# ------------------------------------------------------------------ off
oo_off() {
    step "Container"
    systemctl stop "$UNIT.service" 2>/dev/null || true   # gone already is fine
    rm -f "$QUADLET_DST"
    systemctl daemon-reload                              # the generated unit goes with the quadlet
    note "stopped; $QUADLET_DST removed"

    step "nginx"
    if [[ -f $SNIPPET_DST ]]; then
        rm -f "$SNIPPET_DST"
        nginx_apply ""
    else
        note "no $SNIPPET_DST"
    fi

    step "App settings"
    set_app_env MNEMERYNNE_ONLYOFFICE_URL ""             # blank: the app shows no Office editor
    set_app_env MNEMERYNNE_ONLYOFFICE_JWT_SECRET ""
    set_app_env MNEMERYNNE_ONLYOFFICE_INTERNAL_URL ""
    note "the three ONLYOFFICE settings blanked in $ENV_FILE"

    step "App"
    app_restart

    step "Done"
    note "Kept: $DATA (fonts, logs, data) and $OO_ENV (install reuses its secret). The image stays in podman:"
    note "  sudo podman rmi $IMAGE_REPO   frees about 3 GB once you're sure."
}

# ------------------------------------------------------------------ status
oo_status() {
    echo "RAM: $(ram_gb) GB$( (( $(ram_gb) < MIN_RAM_GB )) && echo "  (under ${MIN_RAM_GB} GB: too little to spare the 4 GB it needs)")"
    if [[ -f $QUADLET_DST ]]; then
        echo "quadlet: $QUADLET_DST"
        echo "image: $(installed_image)"
    else
        echo "quadlet: not installed"
    fi
    echo "$UNIT: $(systemctl is-active "$UNIT.service" 2>/dev/null || true)"
    echo "healthcheck (127.0.0.1:$PORT): $(healthy && echo true || echo 'no answer')"
    have podman && podman ps --filter "name=$UNIT" --format '  {{.Names}}  {{.Status}}  {{.Image}}' 2>/dev/null || true
    have podman && podman stats --no-stream --format '  memory: {{.MemUsage}}' "$UNIT" 2>/dev/null || true
    echo "nginx snippet: $([[ -f $SNIPPET_DST ]] && echo "$SNIPPET_DST" || echo 'not installed')"
    echo "$OO_ENV: $([[ -f $OO_ENV ]] && stat -c 'mode %a, owner %U' "$OO_ENV" || echo 'missing')"
    echo "MNEMERYNNE_ONLYOFFICE_URL: $(env_value MNEMERYNNE_ONLYOFFICE_URL '(blank: off)')"
    echo "MNEMERYNNE_ONLYOFFICE_INTERNAL_URL: $(env_value MNEMERYNNE_ONLYOFFICE_INTERNAL_URL '(blank)')"
    echo "MNEMERYNNE_ONLYOFFICE_JWT_SECRET: $([[ -n $(env_value MNEMERYNNE_ONLYOFFICE_JWT_SECRET '') ]] && echo set || echo blank)"   # never the value
    echo "MNEMERYNNE_ONLYOFFICE_APP_URL: $(env_value MNEMERYNNE_ONLYOFFICE_APP_URL '(blank: the public URL)')"
    return 0
}

main() {
    [[ $(id -u) = 0 ]] || die "run it with sudo."
    case "${1:-}" in
        install) oo_install ;;
        update) oo_update "${2:-}" ;;
        off) oo_off ;;
        status) oo_status ;;
        *) echo "Usage: sudo bash $0 install | update <tag> | off | status" >&2; exit 2 ;;
    esac
}

main "$@"
