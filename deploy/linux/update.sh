#!/usr/bin/env bash
# Update Mnemerynne from GitHub, as root, through the admin wrapper:
#
#   sudo mnemerynne-admin check-updates      what's new, and what an update would change
#   sudo mnemerynne-admin update [--yes]     back up, update, restart, check; undo on failure
#
# Only fast-forwards: refuses when /opt/mnemerynne has local edits or its
# history differs from GitHub. The web app can show that updates exist but
# can never start one.
#
# Everything is inside main(), called on the script's last line: git rewrites
# this file during an update, and bash has to have read all of it by then.

main() {
    # -E: the undo trap below also fires for failures inside helper functions.
    set -Eeuo pipefail

    local APP=${MNEMERYNNE_APP:-/opt/mnemerynne}
    local ENV_FILE=${MNEMERYNNE_ENV_FILE:-/etc/mnemerynne/mnemerynne.env}
    local UNIT_DIR=${MNEMERYNNE_UNIT_DIR:-/etc/systemd/system}
    local WRAPPER=${MNEMERYNNE_WRAPPER:-/usr/local/bin/mnemerynne-admin}
    local SERVICE_USER=mnemerynne
    local REMOTE=origin

    local mode=${1:-}
    shift || true
    local assume_yes=0
    for arg in "$@"; do
        case $arg in
            --yes|-y) assume_yes=1 ;;
            *) die "unknown option: $arg" ;;
        esac
    done
    case $mode in
        update|check-updates) ;;
        *) die "usage: mnemerynne-admin update [--yes] | mnemerynne-admin check-updates" ;;
    esac
    [ "$(id -u)" = 0 ] || [ -n "${MNEMERYNNE_UPDATE_TEST:-}" ] || die "run it with sudo: sudo mnemerynne-admin $mode"

    # Service settings: where the data is and which address and port the app listens on.
    local DATA_DIR=/var/lib/mnemerynne HOST=127.0.0.1 PORT=38200 BRANCH=main
    local HEALTH_TIMEOUT=${MNEMERYNNE_UPDATE_HEALTH_TIMEOUT:-300}
    if [ -r "$ENV_FILE" ]; then
        DATA_DIR=$(env_value MNEMERYNNE_DATA_DIR "$DATA_DIR")
        HOST=$(env_value MNEMERYNNE_HOST "$HOST")
        PORT=$(env_value MNEMERYNNE_PORT "$PORT")
        BRANCH=$(env_value MNEMERYNNE_UPDATE_BRANCH "$BRANCH")
        HEALTH_TIMEOUT=${MNEMERYNNE_UPDATE_HEALTH_TIMEOUT:-$(env_value MNEMERYNNE_UPDATE_HEALTH_TIMEOUT "$HEALTH_TIMEOUT")}
    fi
    # Reject invalid deadlines before backing up, changing code or restarting anything.
    [[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]{0,3}$ ]] && [ "$HEALTH_TIMEOUT" -le 3600 ] \
        || die "MNEMERYNNE_UPDATE_HEALTH_TIMEOUT must be 1..3600 seconds"
    local DB="$DATA_DIR/mnemerynne.db"
    local STATE_DIR="$DATA_DIR/update"

    cd "$APP"
    git_() { git -c safe.directory="$APP" "$@"; }

    step "Checking GitHub ($(git_ remote get-url "$REMOTE"), branch $BRANCH)"
    git_ fetch --quiet "$REMOTE" "$BRANCH"
    local current target
    current=$(git_ rev-parse HEAD)
    target=$(git_ rev-parse "$REMOTE/$BRANCH")

    if [ "$current" = "$target" ]; then
        echo "Mnemerynne is up to date ($(git_ log -1 --format='%h, %cd' --date=short HEAD))."
        return 0
    fi
    if ! git_ merge-base --is-ancestor "$current" "$target"; then
        die "this copy's history differs from GitHub's $BRANCH (it has commits GitHub doesn't), so it can't fast-forward. Sort that out by hand in $APP."
    fi
    local dirty
    dirty=$(git_ status --porcelain --untracked-files=no)
    if [ -n "$dirty" ]; then
        printf '%s\n' "$dirty" >&2
        die "$APP has local edits (above). Commit, move or undo them first; an update never overwrites them."
    fi

    # ---------------------------------------------------------- what's new
    local changed migrations units deps admin_changed
    changed=$(git_ diff --name-only "$current" "$target")
    migrations=$(grep -E '^mnemerynne/migrations/[0-9]+_.*\.sql$' <<<"$changed" || true)
    units=$(grep -E '^deploy/linux/[^/]+\.(service|timer)$' <<<"$changed" || true)
    deps=$(grep -E '^(uv\.lock|pyproject\.toml)$' <<<"$changed" || true)
    admin_changed=$(grep -E '^deploy/linux/mnemerynne-admin$' <<<"$changed" || true)

    echo
    echo "$(git_ rev-list --count "$current..$target") update(s) available: $(git_ log -1 --format=%h HEAD) -> $(git_ log -1 --format=%h "$target")"
    git_ log --format='  %h %cd  %s' --date=short "$current..$target"
    echo
    [ -n "$migrations" ] && echo "  * Database update: $(tr '\n' ' ' <<<"$migrations")(a backup is taken first)"
    [ -n "$units" ] && echo "  * Service files change: $(tr '\n' ' ' <<<"$units")(installed; those services restart)"
    [ -n "$deps" ] && echo "  * Python packages change (uv sync)"
    [ -n "$admin_changed" ] && echo "  * The mnemerynne-admin command itself changes"
    echo "  * mnemerynne-web and mnemerynne-worker restart"

    if [ "$mode" = check-updates ]; then
        echo
        echo "To install: sudo mnemerynne-admin update"
        return 0
    fi

    if [ "$assume_yes" != 1 ]; then
        local answer=""
        read -r -p "Update now? [y/N] " answer || true
        case $answer in y|Y|yes|YES) ;; *) echo "Nothing changed."; return 0 ;; esac
    fi

    # ------------------------------------------------------------ backup
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$STATE_DIR"
    local stamp snapshot schema_before
    stamp=$(date +%Y%m%d-%H%M%S)
    snapshot="$STATE_DIR/before-$stamp-$(git_ log -1 --format=%h HEAD).db"
    step "Backing up the database to $snapshot"
    run_py "import sqlite3, sys; d = sqlite3.connect(sys.argv[2]); sqlite3.connect(sys.argv[1]).backup(d); d.close()" \
        "$DB" "$snapshot"
    schema_before=$(schema_version)
    # Keep the last five snapshots.
    ls -1t "$STATE_DIR"/before-*.db 2>/dev/null | tail -n +6 | xargs -r rm -f

    # Unit files about to be replaced, kept for an undo.
    local saved_units="$STATE_DIR/units-$stamp"
    install -d -m 700 "$saved_units"

    local phase=start restart_extra=()
    trap 'on_error' ERR

    # ------------------------------------------------------------ update
    phase=code
    step "Updating the code to $(git_ log -1 --format=%h "$target")"
    git_ merge --ff-only --quiet "$target"
    phase=packages
    step "Installing Python packages and compiling startup caches"
    install_packages
    if command -v restorecon >/dev/null; then restorecon -R "$APP"; fi

    phase=units
    local f name changed_units=()
    for f in deploy/linux/*.service deploy/linux/*.timer; do
        name=$(basename "$f")
        if [ ! -e "$UNIT_DIR/$name" ]; then
            echo "  New service file $name is not installed automatically; see deploy/linux/README.md."
            continue
        fi
        if ! cmp -s "$f" "$UNIT_DIR/$name"; then
            cp -p "$UNIT_DIR/$name" "$saved_units/$name"
            install -m 644 "$f" "$UNIT_DIR/$name"
            changed_units+=("$name")
        fi
    done
    if [ ${#changed_units[@]} -gt 0 ]; then
        step "Installed changed service files: ${changed_units[*]}"
        echo "  (Your own settings in drop-ins, $UNIT_DIR/<unit>.d/*.conf, are kept.)"
        systemctl daemon-reload
        for name in "${changed_units[@]}"; do
            case $name in
                mnemerynne-generator.service|mnemerynne-embedder.service) restart_extra+=("${name%.service}") ;;
            esac
        done
    fi
    if ! cmp -s deploy/linux/mnemerynne-admin "$WRAPPER"; then
        [ -e "$WRAPPER" ] && cp -p "$WRAPPER" "$saved_units/mnemerynne-admin.wrapper"
        install -m 755 deploy/linux/mnemerynne-admin "$WRAPPER"
        echo "  Updated $WRAPPER"
    fi

    # ----------------------------------------------------------- restart
    phase=restart
    if [ ${#restart_extra[@]} -gt 0 ]; then
        step "Restarting ${restart_extra[*]}"
        systemctl restart "${restart_extra[@]}"
    fi
    # The web app runs any database update at start-up; the worker waits for it.
    step "Restarting mnemerynne-web (it applies any database update)"
    systemctl restart mnemerynne-web
    wait_healthy || fail "mnemerynne-web didn't come back healthy"
    systemctl restart mnemerynne-worker
    trap - ERR

    write_status ok "$current" "$target" ""
    step "Updated to $(git_ log -1 --format='%h (%cd) %s' --date=short HEAD)"
    echo "  Database schema: v$schema_before -> v$(schema_version). Backup: $snapshot"
}

# ---------------------------------------------------------------- helpers
step() { printf '\n== %s\n' "$*"; }
die() { printf 'mnemerynne-admin: %s\n' "$*" >&2; exit 1; }

env_value() {  # NAME DEFAULT: a value from the service env file
    local v
    v=$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '"'"'")
    printf '%s' "${v:-$2}"
}

run_py() {  # CODE ARGS...: run Python from the app's venv as the service user
    local code=$1; shift
    if [ -n "${MNEMERYNNE_UPDATE_TEST:-}" ]; then
        "$APP/.venv/bin/python" -c "$code" "$@"
    else
        runuser -u "$SERVICE_USER" -- "$APP/.venv/bin/python" -c "$code" "$@"
    fi
}

schema_version() {
    run_py "import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA user_version').fetchone()[0])" "$DB"
}

install_packages() {  # Build caches as root before the read-only services start importing Python.
    uv sync --frozen --no-dev --compile-bytecode --quiet || return $?
    # uv compiles site-packages; the editable application lives outside that directory.
    "$APP/.venv/bin/python" -m compileall -q "$APP/mnemerynne"
}

web_environment() {  # Read the running service's environment, including systemd drop-in overrides.
    local pid
    pid=$(systemctl show mnemerynne-web --property=MainPID --value 2>/dev/null) || return 0
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
        cat "/proc/$pid/environ" 2>/dev/null || true  # A restarting process may disappear; use configured defaults.
    fi
}

health_url() {  # Only host and port leave this helper; never print the service's other settings or secrets.
    local host=$HOST port=$PORT entry
    while IFS= read -r -d '' entry; do
        case $entry in
            MNEMERYNNE_HOST=*) host=${entry#*=} ;;
            MNEMERYNNE_PORT=*) port=${entry#*=} ;;
        esac
    done < <(web_environment)
    printf 'http://%s:%s/healthz' "$(health_host "$host")" "$port"
}

wait_healthy() {  # Slow NAS startup gets a real deadline, shared by updates and rollback recovery.
    local timeout=${HEALTH_TIMEOUT:-300} started=$SECONDS url reply remaining request_timeout delay
    local deadline=$((SECONDS + timeout)) next_report=$((SECONDS + 30))
    local last_error="No successful health response."
    url=$(health_url)
    step "Waiting up to ${timeout}s for $url"
    while (( SECONDS < deadline )); do
        remaining=$((deadline - SECONDS))
        if (( remaining <= 0 )); then break; fi  # curl treats a zero timeout as unlimited.
        request_timeout=$((remaining < 5 ? remaining : 5))
        # The service is local: bypass inherited HTTP proxies and bound each request, too.
        if reply=$(curl --noproxy '*' --connect-timeout 2 --max-time "$request_timeout" -fsS "$url" 2>&1); then
            if grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' <<<"$reply"; then
                echo "  Healthy after $((SECONDS - started))s."
                return 0
            fi
            last_error="The endpoint responded but did not report status ok."
        else
            last_error=${reply:0:500}
        fi
        if (( SECONDS >= next_report )); then
            echo "  Still waiting ($((SECONDS - started))s elapsed)."
            next_report=$((SECONDS + 30))
        fi
        remaining=$((deadline - SECONDS))
        if (( remaining <= 0 )); then break; fi
        delay=$((remaining < 2 ? remaining : 2))
        sleep "$delay"
    done
    printf '  Health check timed out at %s after %ss: %s\n' "$url" "$timeout" "$last_error" >&2
    return 1
}

health_host() {  # HOST: where to reach the app that listens on MNEMERYNNE_HOST
    case $1 in
        ''|0.0.0.0) echo 127.0.0.1 ;;    # every address: loopback is one of them
        ::|'[::]') echo '[::1]' ;;
        \[*) echo "$1" ;;
        *:*) echo "[$1]" ;;              # an IPv6 address needs brackets in a URL
        *) echo "$1" ;;                  # a specific address, e.g. the LAN IP: only it answers
    esac
}

write_status() {  # RESULT FROM TO MESSAGE: last.json, which the Settings page shows
    run_py "import json, sys, time
r, a, b, msg, out = sys.argv[1:]
json.dump({'result': r, 'from': a, 'to': b, 'message': msg, 'at': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())},
          open(out, 'w'))" "$1" "$2" "$3" "$4" "$STATE_DIR/last.json" || true
}

fail() {
    trap - ERR
    printf '\nmnemerynne-admin: update failed while %s: %s\n' "$phase" "$*" >&2
    undo
    exit 1
}

on_error() {
    fail "a step exited with an error (see above)"
}

undo() {
    step "Undoing the update"
    set +e
    cd "$APP"
    git_ reset --hard --quiet "$current"
    install_packages
    if command -v restorecon >/dev/null; then restorecon -R "$APP"; fi
    local f restored=0
    if [ -e "$saved_units/mnemerynne-admin.wrapper" ]; then
        install -m 755 "$saved_units/mnemerynne-admin.wrapper" "$WRAPPER"
    fi
    for f in "$saved_units"/*.service "$saved_units"/*.timer; do
        [ -e "$f" ] || continue
        install -m 644 "$f" "$UNIT_DIR/$(basename "$f")"
        restored=1
    done
    [ "$restored" = 1 ] && systemctl daemon-reload
    if [ "$(schema_version)" != "$schema_before" ]; then
        echo "  The database was updated; putting back $snapshot"
        systemctl stop mnemerynne-worker mnemerynne-web
        rm -f "$DB-wal" "$DB-shm"
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 640 "$snapshot" "$DB"
        if command -v restorecon >/dev/null; then restorecon "$DB"; fi
    fi
    [ ${#restart_extra[@]} -gt 0 ] && systemctl restart "${restart_extra[@]}"
    systemctl restart mnemerynne-web
    if wait_healthy; then
        systemctl restart mnemerynne-worker
        echo "  Back on $(git_ log -1 --format=%h HEAD); Mnemerynne is running."
        write_status failed "$current" "$target" "failed while $phase; undone"
    else
        echo "  Mnemerynne still isn't healthy after undoing. Look at: journalctl -u mnemerynne-web -n 50" >&2
        write_status failed "$current" "$target" "failed while $phase; undo didn't bring it back"
    fi
    echo
    journalctl -u mnemerynne-web -n 15 --no-pager 2>/dev/null || true
}

main "$@"; exit $?
