#!/bin/bash
# Set up a Fedora box for Mnemerynne in one go: the storage (blank disks made into an XFS volume,
# mirrored when there are two, or an array already mounted), the folders on it (the SMB share, its
# recycle bin, backups, the data folder), the settings file, the Python environment, the services,
# the Samba share definition, Caddy and the first admin. It asks each question with its default,
# shows the whole plan, confirms once and then runs; every step skips what is already done, so it
# is safe to run again. The README's sections 1 to 5d are what it automates; they remain the
# manual path.
#
#   sudo bash /opt/mnemerynne/deploy/linux/setup.sh                 # ask, show the plan, run
#   sudo bash /opt/mnemerynne/deploy/linux/setup.sh --dry-run       # the plan only, nothing written
#   sudo bash /opt/mnemerynne/deploy/linux/setup.sh --yes --disks /dev/sdb,/dev/sdc --hostname erynne.corp.internal
#
# Every answer is also a flag (below) or a SETUP_* variable; with --yes the defaults are taken and
# nothing destructive happens without its explicit flag.
#
#   --mount DIR            use the storage mounted at DIR (default /srv/storage), or mount the new volume there
#   --disks /dev/a,/dev/b  wipe these blank disks and make the storage volume on them (XFS on LVM)
#   --raid1 | --no-raid    mirror the disks with mdadm (default: yes with two or more)
#   --force-wipe           allow --disks that carry a filesystem or partitions
#   --no-storage           touch no disks and no storage folders (settings and services only)
#   --grow-root GB | --no-grow-root   grow the root volume first (offered when / is short of space)
#   --data-on-storage | --data-local  the data folder (database, uploads) on the storage, or /var/lib/mnemerynne
#   --share-name NAME      the Samba share's name (default share); --no-samba skips the Samba step
#   --hostname H           the address people will use (default: this box's name); --public uses the
#                          Let's Encrypt Caddyfile; --no-caddy skips Caddy
#   --admin NAME           create this local admin (password prompted; SETUP_ADMIN_PASSWORD for unattended runs)
#   --no-admin             create nobody
#   --skip-preinstall      don't run preinstall.sh (packages, llama.cpp, models)
#   --yes, -y              no questions; --dry-run: show the plan and stop
set -Eeuo pipefail

APP=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
HERE=$APP/deploy/linux
ROOT=${SETUP_ROOT:-}            # tests: a prefix in front of /etc, /srv, /var, /usr/local
ENV_FILE=$ROOT/etc/mnemerynne/mnemerynne.env
UNIT_DIR=$ROOT/etc/systemd/system
SMB_CONF=$ROOT/etc/samba/smb.conf
SHARE_CONF=$ROOT/etc/samba/mnemerynne-share.conf
CADDYFILE=$ROOT/etc/caddy/Caddyfile
FSTAB=$ROOT/etc/fstab
MDADM_CONF=$ROOT/etc/mdadm.conf
LOCAL_DATA=/var/lib/mnemerynne
SERVICE_USER=mnemerynne
VG=storage
LV=data
MD=/dev/md/storage

# The answers (flags, SETUP_* variables, prompts).
MOUNT=${SETUP_MOUNT:-}
DISKS=()
RAID=${SETUP_RAID:-}
FORCE_WIPE=0
STORAGE=1
GROW_ROOT=${SETUP_GROW_ROOT:-}        # GB, 0 = no, "" = decide
DATA_ON_STORAGE=${SETUP_DATA_ON_STORAGE:-}
SHARE_NAME=${SETUP_SHARE_NAME:-share}
SAMBA=${SETUP_SAMBA:-}
HOST_NAME=${SETUP_HOSTNAME:-}
CADDY=${SETUP_CADDY:-}
PUBLIC=0
ADMIN=${SETUP_ADMIN:-}
NO_ADMIN=0
PREINSTALL=1
YES=0
DRY=0

# What the answers work out to.
DATA_DIR= SHARE_ROOT= TRASH_DIR= BACKUP_DIR= AD_DOMAIN= SECRET_NEW=0
MOUNTED=0 BLANK=() ROOT_LV= ROOT_FREE_GB= VG_FREE_GB=
PLAN=()

# ------------------------------------------------------------------ helpers
step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf 'setup: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
run() { printf '   + %s\n' "$*"; "$@"; }
plan() { PLAN+=("$*"); }

ask() {  # ask VAR "question" default: a prompt with its default, or the default with --yes
    local var=$1 question=$2 default=$3 answer=""
    if [[ $YES = 1 ]]; then printf -v "$var" '%s' "$default"; return; fi
    read -r -p "$question [${default:-none}] " answer </dev/tty || true
    printf -v "$var" '%s' "${answer:-$default}"
}
confirm() {  # confirm "question" default(y|n): 0 = yes
    local question=$1 default=${2:-n} answer=""
    if [[ $YES = 1 ]]; then [[ $default = y ]]; return; fi
    if [[ $default = y ]]; then read -r -p "$question [Y/n] " answer </dev/tty || true; [[ -z $answer || $answer =~ ^[yY] ]]
    else read -r -p "$question [y/N] " answer </dev/tty || true; [[ $answer =~ ^[yY] ]]; fi
}

env_value() {  # env_value KEY DEFAULT [FILE]: a value from the settings file
    local file=${3:-$ENV_FILE} v=""
    [[ -r $file ]] && v=$(sed -n "s/^$1=//p" "$file" | tail -n 1 | tr -d '"'"'")
    printf '%s' "${v:-$2}"
}
set_env() {  # set_env KEY VALUE [FILE]: replace the active or commented line, else append (no inline comments)
    local key=$1 value=$2 file=${3:-$ENV_FILE}
    if grep -qE "^#?$key=" "$file"; then
        awk -v k="$key" -v v="$value" 'BEGIN { done = 0 }
             !done && $0 ~ ("^#?" k "=") { print k "=" v; done = 1; next } { print }' "$file" > "$file.tmp"
        cat "$file.tmp" > "$file"       # keeps the file's owner and mode
        rm -f "$file.tmp"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

samba_conf() {  # samba_conf NAME PATH: the share definition Samba includes
    cat <<EOF
# Mnemerynne's share (deploy/linux/setup.sh): every top-level folder is a library, Users\\<name> a File box.
# Windows permissions decide who sees what; Samba keeps POSIX ACLs in step (acl_xattr), which Mnemerynne reads.
[$1]
    path = $2
    read only = no
    browseable = yes
    vfs objects = acl_xattr
    inherit acls = yes
    map acl inherit = yes
    store dos attributes = yes
    hide dot files = yes
EOF
}

gb() { df --output=avail -BG "$1" 2>/dev/null | tail -1 | tr -dc 0-9; }
is_mounted() { have findmnt && findmnt -n "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------- arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --mount) MOUNT=$2; shift ;;
            --disks) IFS=, read -r -a DISKS <<<"$2"; shift ;;
            --raid1) RAID=1 ;;
            --no-raid) RAID=0 ;;
            --force-wipe) FORCE_WIPE=1 ;;
            --no-storage) STORAGE=0 ;;
            --grow-root) GROW_ROOT=$2; shift ;;
            --no-grow-root) GROW_ROOT=0 ;;
            --data-on-storage) DATA_ON_STORAGE=1 ;;
            --data-local) DATA_ON_STORAGE=0 ;;
            --share-name) SHARE_NAME=$2; shift ;;
            --no-samba) SAMBA=0 ;;
            --hostname) HOST_NAME=$2; shift ;;
            --public) PUBLIC=1 ;;
            --no-caddy) CADDY=0 ;;
            --admin) ADMIN=$2; shift ;;
            --no-admin) NO_ADMIN=1 ;;
            --skip-preinstall) PREINSTALL=0 ;;
            --yes|-y) YES=1 ;;
            --dry-run) DRY=1 ;;
            -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
            *) die "unknown option: $1 (see --help)" ;;
        esac
        shift
    done
    [[ -n ${SETUP_DISKS:-} && ${#DISKS[@]} = 0 ]] && IFS=, read -r -a DISKS <<<"$SETUP_DISKS"
    [[ $SHARE_NAME =~ ^[A-Za-z0-9_-]{1,32}$ ]] || die "share name: letters, digits, - and _ only"
    [[ $DRY = 1 ]] && YES=1
    return 0
}

# ------------------------------------------------------------- the storage
blank_disks() {  # whole disks with no partitions, no filesystem signature, not mounted, not removable
    have lsblk || return 0
    local name type rm_ rest
    while read -r name type rm_ rest; do
        [[ $type = disk && $rm_ = 0 ]] || continue
        [[ $(lsblk -nro TYPE "$name" 2>/dev/null | grep -vc '^disk$') = 0 ]] || continue   # has children
        lsblk -nro MOUNTPOINTS "$name" 2>/dev/null | grep -q . && continue
        [[ -z $(blkid -p -o value -s TYPE "$name" 2>/dev/null) ]] || continue
        printf '%s\n' "$name"
    done < <(lsblk -dnpo NAME,TYPE,RM,SIZE,MODEL 2>/dev/null)
}
disk_label() { lsblk -dnpo SIZE,MODEL "$1" 2>/dev/null | tr -s ' ' || true; }

gather_storage() {
    [[ $STORAGE = 1 ]] || return 0
    local default=/srv/storage
    is_mounted "${MOUNT:-$default}" && MOUNTED=1
    if [[ ${#DISKS[@]} -gt 0 ]]; then
        MOUNT=${MOUNT:-$default}
        [[ $MOUNTED = 1 ]] && die "$MOUNT is already mounted; leave out --disks to use it"
        local d
        for d in "${DISKS[@]}"; do
            [[ -b $d ]] || die "$d is not a block device"
            if ! blank_disks | grep -qxF "$d"; then
                [[ $FORCE_WIPE = 1 ]] || die "$d isn't blank (partitions, a filesystem, or mounted); --force-wipe to wipe it anyway"
            fi
        done
    elif [[ -n $MOUNT ]]; then
        [[ -d $ROOT$MOUNT ]] || die "$MOUNT doesn't exist"
        [[ $MOUNTED = 1 ]] || note "$MOUNT isn't a mount point; using the folder as it is"
    elif [[ $YES = 1 ]]; then
        if [[ $MOUNTED = 1 ]]; then MOUNT=$default
        else STORAGE=0; note "No storage mounted at $default and no --disks given: skipping the storage"; fi
    else
        mapfile -t BLANK < <(blank_disks)
        printf '\nStorage for the share, backups and data:\n'
        [[ $MOUNTED = 1 ]] && printf '  m) use what is mounted at %s (%s GB free)\n' "$default" "$(gb "$ROOT$default")"
        local i
        for i in "${!BLANK[@]}"; do printf '  %d) prepare the blank disk %s (%s)\n' "$((i + 1))" "${BLANK[$i]}" "$(disk_label "${BLANK[$i]}")"; done
        printf '  d) another folder or mount point\n  n) no storage setup\n'
        local choice="" preset=n
        [[ $MOUNTED = 1 ]] && preset=m
        [[ $MOUNTED = 0 && ${#BLANK[@]} -gt 0 ]] && preset=$(seq -s, 1 "${#BLANK[@]}")
        ask choice "Which? (disk numbers may be comma-separated)" "$preset"
        case $choice in
            m) MOUNT=$default ;;
            d) ask MOUNT "Folder" "$default"; [[ -d $ROOT$MOUNT ]] || die "$MOUNT doesn't exist" ;;
            n) STORAGE=0 ;;
            *)  local n
                for n in ${choice//,/ }; do
                    [[ $n =~ ^[0-9]+$ && $n -ge 1 && $n -le ${#BLANK[@]} ]] || die "no disk $n"
                    DISKS+=("${BLANK[$((n - 1))]}")
                done
                ask MOUNT "Mount the new volume at" "$default" ;;
        esac
    fi
    [[ $STORAGE = 1 ]] || return 0
    if [[ ${#DISKS[@]} -ge 2 ]]; then
        [[ -n $RAID ]] || { if confirm "Mirror the ${#DISKS[@]} disks (RAID1, half the space, survives one disk)?" y; then RAID=1; else RAID=0; fi; }
    else
        RAID=0
    fi
    if [[ -z $DATA_ON_STORAGE ]]; then
        if confirm "Keep the database and uploads on the storage too ($MOUNT/mnemerynne, instead of $LOCAL_DATA)?" y; then DATA_ON_STORAGE=1; else DATA_ON_STORAGE=0; fi
    fi
}

gather_root() {  # a Fedora Server root volume is 15 GB; offer to grow it from the volume group's free space
    [[ $GROW_ROOT = 0 ]] && return 0
    have lvs && have findmnt || { GROW_ROOT=0; return 0; }
    ROOT_LV=$(findmnt -nro SOURCE / 2>/dev/null || true)
    local vg_free
    vg_free=$(lvs --noheadings -o vg_free --units g --nosuffix "$ROOT_LV" 2>/dev/null | tr -d ' ' || true)
    ROOT_FREE_GB=$(gb /)
    VG_FREE_GB=${vg_free%%.*}
    if [[ -z $VG_FREE_GB || $VG_FREE_GB -lt 1 ]]; then GROW_ROOT=0; return 0; fi
    if [[ -n $GROW_ROOT ]]; then
        [[ $GROW_ROOT -le $VG_FREE_GB ]] || die "only $VG_FREE_GB GB free in the root volume group, can't add $GROW_ROOT"
        return 0
    fi
    if [[ ${ROOT_FREE_GB:-99} -lt 10 ]]; then
        local want=20
        [[ $want -gt $VG_FREE_GB ]] && want=$VG_FREE_GB
        ask GROW_ROOT "/ has ${ROOT_FREE_GB} GB free; grow it by how many GB (the volume group has $VG_FREE_GB free; 0 = don't)" "$want"
        [[ $GROW_ROOT =~ ^[0-9]+$ ]] || die "a number of GB, please"
    else
        GROW_ROOT=0
    fi
}

gather_rest() {
    if [[ $STORAGE = 1 ]]; then
        SHARE_ROOT=$MOUNT/share
        TRASH_DIR=$MOUNT/.mnemerynne-trash
        BACKUP_DIR=$MOUNT/backups/mnemerynne
        DATA_DIR=$LOCAL_DATA
        [[ $DATA_ON_STORAGE = 1 ]] && DATA_DIR=$MOUNT/mnemerynne
    else
        DATA_DIR=$(env_value MNEMERYNNE_DATA_DIR "$LOCAL_DATA")
        SHARE_ROOT=$(env_value MNEMERYNNE_SHARE_ROOT "")
        BACKUP_DIR=$(env_value MNEMERYNNE_BACKUP_DIR "")
    fi
    if [[ -z $SAMBA ]]; then
        if have testparm && [[ -n $SHARE_ROOT ]]; then SAMBA=1; else SAMBA=0; fi
    fi
    [[ $SAMBA = 1 && -z $SHARE_ROOT ]] && SAMBA=0
    if [[ $SAMBA = 1 && $YES = 0 ]]; then ask SHARE_NAME "Samba share name (\\\\$(hostname -s 2>/dev/null || hostname)\\NAME)" "$SHARE_NAME"; fi
    [[ -n $HOST_NAME ]] || HOST_NAME=$(hostname -f 2>/dev/null || hostname)
    [[ -z $CADDY ]] && CADDY=1
    if [[ $YES = 0 ]]; then ask HOST_NAME "The address people will open Erynne at (it must resolve to this box)" "$HOST_NAME"; fi
    [[ $HOST_NAME =~ ^[A-Za-z0-9.-]+$ ]] || die "hostname: letters, digits, dots and dashes only"
    if have wbinfo && wbinfo -u >/dev/null 2>&1; then
        AD_DOMAIN=$(testparm -s --parameter-name=workgroup 2>/dev/null || true)
    fi
    local secret
    secret=$(env_value MNEMERYNNE_SECRET_KEY change-me)
    [[ $secret = change-me || ${#secret} -lt 32 ]] && SECRET_NEW=1
    if [[ $NO_ADMIN = 0 && -z $ADMIN && $YES = 0 ]]; then
        ask ADMIN "First local admin's username (empty: none now)" "${SUDO_USER:-}"
    fi
    [[ -z $ADMIN || $ADMIN =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "admin username: letters, digits, . _ - only"
}

# ------------------------------------------------------------------- plan
show_plan() {
    local d
    [[ $GROW_ROOT != 0 && -n $GROW_ROOT ]] && plan "Grow / ($ROOT_LV) by $GROW_ROOT GB (lvextend -r)"
    [[ $PREINSTALL = 1 ]] && plan "preinstall.sh: packages, the $SERVICE_USER user, llama.cpp (Vulkan), models, the GPU check"
    if [[ $STORAGE = 1 && ${#DISKS[@]} -gt 0 ]]; then
        for d in "${DISKS[@]}"; do plan "WIPE $d ($(disk_label "$d")): everything on it is lost"; done
        [[ $RAID = 1 ]] && plan "mdadm RAID1 $MD over ${DISKS[*]} (assembled at boot: /etc/mdadm.conf, dracut)"
        plan "LVM: volume group $VG, logical volume $LV (all the space), XFS, mounted at $MOUNT by UUID in /etc/fstab (nofail)"
    elif [[ $STORAGE = 1 ]]; then
        plan "Use the storage at $MOUNT (no disk is touched)"
    else
        plan "No storage setup (settings and services only)"
    fi
    if [[ $STORAGE = 1 ]]; then
        plan "Folders: $SHARE_ROOT (ACL rwX for $SERVICE_USER, SELinux samba_share_t), $TRASH_DIR, $BACKUP_DIR, $DATA_DIR"
        [[ $DATA_ON_STORAGE = 1 && -f $ROOT$LOCAL_DATA/mnemerynne.db ]] && plan "Move the existing data from $LOCAL_DATA to $DATA_DIR (services stopped meanwhile; the old folder is kept, renamed)"
        [[ $MOUNT != /srv/storage ]] && plan "Service drop-ins so the units may write under $MOUNT"
    fi
    plan "Settings: $ENV_FILE (MNEMERYNNE_DATA_DIR=$DATA_DIR, PUBLIC_URL=https://$HOST_NAME$([[ -n $SHARE_ROOT ]] && printf ', SHARE_ROOT=%s' "$SHARE_ROOT")$([[ -n $BACKUP_DIR ]] && printf ', BACKUP_DIR=%s' "$BACKUP_DIR")$([[ -n $AD_DOMAIN ]] && printf ', AD_DOMAIN=%s' "$AD_DOMAIN")$([[ $SECRET_NEW = 1 ]] && printf ', a new SECRET_KEY'))"
    plan "Python environment (uv sync) and the mnemerynne-admin command"
    plan "Services: generator, embedder, web, worker, backup timer$([[ -n $SHARE_ROOT ]] && printf ', share-acl watcher')$([[ -n $AD_DOMAIN ]] && printf ', AD sync timer')"
    [[ $SAMBA = 1 ]] && plan "Samba: share [$SHARE_NAME] = $SHARE_ROOT in $SHARE_CONF, included from smb.conf (testparm checks it first)"
    [[ $CADDY = 1 ]] && plan "Caddy on 443 for https://$HOST_NAME ($([[ $PUBLIC = 1 ]] && printf "Let's Encrypt" || printf "its own CA")), SELinux boolean, firewall"
    [[ -n $ADMIN ]] && plan "First admin: $ADMIN"
    [[ -n $SHARE_ROOT ]] && plan "share-init and share-scan (marker, Users folder, first scan)"

    printf '\nThe plan:\n'
    local line
    for line in "${PLAN[@]}"; do printf '  - %s\n' "$line"; done
    printf '\n'
    if [[ $DRY = 1 ]]; then echo "Dry run: nothing was changed."; exit 0; fi
    confirm "Go ahead?" n || { echo "Nothing changed."; exit 0; }
    if [[ ${#DISKS[@]} -gt 0 && $YES = 0 ]]; then
        local typed=""
        read -r -p "Type the disks to wipe, exactly (${DISKS[*]}): " typed </dev/tty || true
        [[ $typed = "${DISKS[*]}" ]] || die "that doesn't match; nothing changed"
    fi
}

# ---------------------------------------------------------------- execute
do_grow_root() {
    [[ $GROW_ROOT != 0 && -n $GROW_ROOT ]] || return 0
    step "Growing / by $GROW_ROOT GB"
    run lvextend -r -L "+${GROW_ROOT}G" "$ROOT_LV"
}

do_preinstall() {
    [[ $PREINSTALL = 1 ]] || return 0
    step "preinstall.sh"
    bash "$HERE/preinstall.sh"
}

do_disks() {
    [[ $STORAGE = 1 && ${#DISKS[@]} -gt 0 ]] || return 0
    step "The storage volume on ${DISKS[*]}"
    run dnf install -y lvm2 xfsprogs acl inotify-tools $([[ $RAID = 1 ]] && printf mdadm)
    local pv="${DISKS[0]}" d
    if lvs "$VG/$LV" >/dev/null 2>&1; then
        note "volume $VG/$LV exists already"
    else
        for d in "${DISKS[@]}"; do run wipefs -a "$d"; done
        if [[ $RAID = 1 ]]; then
            run mdadm --create "$MD" --level=1 --raid-devices="${#DISKS[@]}" --metadata=1.2 --run "${DISKS[@]}"
            pv=$MD
            grep -q "$VG" "$MDADM_CONF" 2>/dev/null || mdadm --detail --scan >> "$MDADM_CONF"
            run dracut -f
            run pvcreate "$pv"
            run vgcreate "$VG" "$pv"
        else
            for d in "${DISKS[@]}"; do run pvcreate "$d"; done
            run vgcreate "$VG" "${DISKS[@]}"
        fi
        run lvcreate -l 100%FREE -n "$LV" "$VG"
        run mkfs.xfs -L "$VG" "/dev/$VG/$LV"
    fi
    local uuid
    uuid=$(blkid -s UUID -o value "/dev/$VG/$LV")
    [[ -n $uuid ]] || die "no UUID for /dev/$VG/$LV"
    install -d "$ROOT$MOUNT"
    if grep -qE "[[:space:]]$MOUNT[[:space:]]" "$FSTAB" 2>/dev/null; then
        note "$MOUNT is in /etc/fstab already"
    else
        printf 'UUID=%s %s xfs defaults,nofail,x-systemd.device-timeout=30 0 0\n' "$uuid" "$MOUNT" >> "$FSTAB"
    fi
    run systemctl daemon-reload
    is_mounted "$MOUNT" || run mount "$MOUNT"
    is_mounted "$MOUNT" || die "$MOUNT didn't mount; check /etc/fstab"
}

do_layout() {
    [[ $STORAGE = 1 ]] || return 0
    step "Folders on $MOUNT"
    id "$SERVICE_USER" >/dev/null 2>&1 || run useradd --system --home-dir "$LOCAL_DATA" --shell /sbin/nologin "$SERVICE_USER"
    run install -d -m 775 "$ROOT$SHARE_ROOT"
    run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$ROOT$TRASH_DIR"
    run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$ROOT$BACKUP_DIR"
    if have setfacl; then
        run setfacl -R -m "u:$SERVICE_USER:rwX" -m "d:u:$SERVICE_USER:rwX" "$ROOT$SHARE_ROOT"
    else
        note "no setfacl here; the share's ACL for $SERVICE_USER wasn't set"
    fi
    if have semanage; then
        semanage fcontext -a -t samba_share_t "$SHARE_ROOT(/.*)?" 2>/dev/null || true
        semanage fcontext -a -t samba_share_t "${TRASH_DIR//./\\.}(/.*)?" 2>/dev/null || true
    fi
    if [[ $DATA_ON_STORAGE = 1 ]]; then
        if [[ -f $ROOT$LOCAL_DATA/mnemerynne.db && ! -e $ROOT$DATA_DIR/mnemerynne.db ]]; then
            step "Moving the data from $LOCAL_DATA to $DATA_DIR"
            run systemctl stop mnemerynne-web mnemerynne-worker mnemerynne-backup.timer mnemerynne-backup 2>/dev/null || true
            run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$ROOT$DATA_DIR"
            run rsync -a "$ROOT$LOCAL_DATA/" "$ROOT$DATA_DIR/"
            run mv "$ROOT$LOCAL_DATA" "$ROOT$LOCAL_DATA.moved-$(date +%Y%m%d)"
        else
            run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$ROOT$DATA_DIR"
        fi
        have semanage && { semanage fcontext -a -e "$LOCAL_DATA" "$DATA_DIR" 2>/dev/null || true; }
    fi
    have restorecon && run restorecon -R "$ROOT$MOUNT"
    install -d "$ROOT/etc/sysctl.d"
    [[ -f $ROOT/etc/sysctl.d/90-inotify.conf ]] || echo 'fs.inotify.max_user_watches=1048576' > "$ROOT/etc/sysctl.d/90-inotify.conf"
    have sysctl && run sysctl --system >/dev/null
    if [[ $MOUNT != /srv/storage ]]; then
        local u
        for u in web worker backup share-acl; do
            install -d "$UNIT_DIR/mnemerynne-$u.service.d"
            printf '[Service]\nReadWritePaths=-%s\n' "$MOUNT" > "$UNIT_DIR/mnemerynne-$u.service.d/storage.conf"
        done
    fi
}

do_settings() {
    step "Settings in $ENV_FILE"
    install -d -m 755 "$(dirname "$ENV_FILE")"
    if [[ ! -f $ENV_FILE ]]; then
        cp "$HERE/mnemerynne.env.example" "$ENV_FILE"
        id "$SERVICE_USER" >/dev/null 2>&1 && chown "root:$SERVICE_USER" "$ENV_FILE"
        chmod 640 "$ENV_FILE"
    fi
    if [[ $SECRET_NEW = 1 ]]; then
        local secret
        secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
                 || openssl rand -base64 48 | tr '+/' '-_' | tr -d '\n=')
        set_env MNEMERYNNE_SECRET_KEY "$secret"
        note "New MNEMERYNNE_SECRET_KEY: $secret"
        note "It encrypts stored credentials: keep a copy off this box (a restored backup needs it)."
    fi
    set_env MNEMERYNNE_DATA_DIR "$DATA_DIR"
    set_env MNEMERYNNE_PUBLIC_URL "https://$HOST_NAME"
    [[ -n $SHARE_ROOT ]] && set_env MNEMERYNNE_SHARE_ROOT "$SHARE_ROOT"
    [[ -n $BACKUP_DIR ]] && set_env MNEMERYNNE_BACKUP_DIR "$BACKUP_DIR"
    [[ -n $AD_DOMAIN ]] && set_env MNEMERYNNE_AD_DOMAIN "$AD_DOMAIN"
    install -d "$ROOT/usr/local/bin"
    run install -m 755 "$HERE/mnemerynne-admin" "$ROOT/usr/local/bin/"
}

do_code() {
    step "Python environment in $APP"
    have uv || die "uv isn't installed (preinstall.sh installs it)"
    (cd "$APP" && run uv sync --frozen --no-dev)
    have restorecon && run restorecon -R "$APP"
}

do_units() {
    step "Services"
    run cp "$HERE"/mnemerynne-*.service "$HERE"/mnemerynne-*.timer "$UNIT_DIR/"
    run systemctl daemon-reload
    local units=(mnemerynne-generator mnemerynne-embedder mnemerynne-web mnemerynne-worker mnemerynne-backup.timer)
    [[ -n $SHARE_ROOT ]] && units+=(mnemerynne-share-acl)
    [[ -n $AD_DOMAIN ]] && units+=(mnemerynne-adsync.timer)
    run systemctl enable --now "${units[@]}"
    local host port i
    host=$(env_value MNEMERYNNE_HOST 127.0.0.1)
    port=$(env_value MNEMERYNNE_PORT 38200)
    for i in $(seq 1 30); do
        if curl -fs "http://$host:$port/healthz" >/dev/null 2>&1; then note "web app healthy: $(curl -s "http://$host:$port/healthz")"; return 0; fi
        sleep 2
    done
    note "the web app didn't answer on $host:$port within a minute; journalctl -u mnemerynne-web -n 40"
}

do_samba() {
    if [[ $SAMBA != 1 ]]; then
        if [[ -n $SHARE_ROOT ]] && ! have testparm; then
            note "Samba isn't installed here: share $SHARE_ROOT from the file server as [$SHARE_NAME], with vfs objects = acl_xattr"
        fi
        return 0
    fi
    step "Samba share [$SHARE_NAME]"
    [[ -f $SMB_CONF ]] || die "no $SMB_CONF; is Samba set up?"
    samba_conf "$SHARE_NAME" "$SHARE_ROOT" > "$SHARE_CONF"
    chmod 644 "$SHARE_CONF"
    local added=0
    if ! grep -qF "include = ${SHARE_CONF#"$ROOT"}" "$SMB_CONF"; then
        cp -a "$SMB_CONF" "$SMB_CONF.bak-$(date +%Y%m%d-%H%M%S)"
        printf '\n# Mnemerynne'"'"'s share (deploy/linux/setup.sh)\ninclude = %s\n' "${SHARE_CONF#"$ROOT"}" >> "$SMB_CONF"
        added=1
    fi
    if [[ -z $ROOT ]] && ! testparm -s "$SMB_CONF" >/dev/null 2>&1; then
        [[ $added = 1 ]] && sed -i "\#include = ${SHARE_CONF#"$ROOT"}#d; /# Mnemerynne's share (deploy\/linux\/setup.sh)/d" "$SMB_CONF"
        die "testparm rejects the share definition; smb.conf was put back. Check: testparm -s $SMB_CONF"
    fi
    [[ -z $ROOT ]] && have smbcontrol && run smbcontrol all reload-config
    if have firewall-cmd && ! firewall-cmd --query-service=samba >/dev/null 2>&1; then
        run firewall-cmd --permanent --add-service=samba && run firewall-cmd --reload
    fi
}

do_caddy() {
    [[ $CADDY = 1 ]] || return 0
    step "Caddy for https://$HOST_NAME"
    [[ -n $ROOT ]] || have caddy || run dnf install -y caddy
    local site=$HERE/Caddyfile-mnemerynne placeholder=erynne.example.internal
    [[ $PUBLIC = 1 ]] && { site=$HERE/Caddyfile-mnemerynne-public; placeholder=erynne.example.org; }
    install -d "$(dirname "$CADDYFILE")"
    if [[ -f $CADDYFILE ]] && grep -qE '^[[:space:]]*root \* /usr/share/caddy' "$CADDYFILE"; then
        cp -a "$CADDYFILE" "$CADDYFILE.bak-$(date +%Y%m%d-%H%M%S)"      # Fedora's test page
        sed "s/$placeholder/$HOST_NAME/" "$site" > "$CADDYFILE"
    elif [[ ! -f $CADDYFILE ]]; then
        sed "s/$placeholder/$HOST_NAME/" "$site" > "$CADDYFILE"
    elif ! grep -qE "^$HOST_NAME\b" "$CADDYFILE"; then
        { printf '\n'; sed "s/$placeholder/$HOST_NAME/" "$site"; } >> "$CADDYFILE"
    else
        note "$HOST_NAME is in the Caddyfile already"
    fi
    [[ -n $ROOT ]] && return 0
    have setsebool && run setsebool -P httpd_can_network_connect 1
    run caddy validate --config "$CADDYFILE"
    run systemctl enable caddy
    run systemctl reload-or-restart caddy
    if have firewall-cmd; then
        run firewall-cmd --permanent --add-service=https $([[ $PUBLIC = 1 ]] && printf -- '--add-service=http')
        run firewall-cmd --reload
    fi
}

do_admin() {
    [[ -n $ADMIN ]] || return 0
    step "First admin: $ADMIN"
    local wrapper=$ROOT/usr/local/bin/mnemerynne-admin
    if "$wrapper" list-users 2>/dev/null | grep -qE "^$ADMIN\b"; then note "$ADMIN exists already"; return 0; fi
    if [[ -n ${SETUP_ADMIN_PASSWORD:-} ]]; then
        printf '%s\n' "$SETUP_ADMIN_PASSWORD" | "$wrapper" create-user "$ADMIN" --role admin --password-stdin
    else
        "$wrapper" create-user "$ADMIN" --role admin </dev/tty
    fi
}

do_share_init() {
    [[ -n $SHARE_ROOT && -z $ROOT ]] || return 0
    step "The share: marker, Users folder, first scan"
    run /usr/local/bin/mnemerynne-admin share-init
    run /usr/local/bin/mnemerynne-admin share-scan
}

done_summary() {
    step "Done"
    cat <<EOF
Erynne: https://$HOST_NAME  (settings: ${ENV_FILE#"$ROOT"})
$([[ -n $SHARE_ROOT ]] && printf 'Share: %s%s\n' "$SHARE_ROOT" "$([[ $SAMBA = 1 ]] && printf ' as \\\\%s\\%s' "$(hostname -s 2>/dev/null || hostname)" "$SHARE_NAME")")
Still by hand, from deploy/linux/README.md:
  - 5c  join the domain (winbind), then MNEMERYNNE_AD_DOMAIN and the adsync timer, if not done
  - 5   put Caddy's root certificate on staff PCs (tls internal); 5b the Claude Desktop extension
  - 5d  share-export for libraries that live inside Mnemerynne; Windows permissions on the share's folders
  - "Home NAS share alias" for the \\\\mnemerynne name; 5i Microsoft 365; 5k Media
Run this script again any time: finished steps are skipped.
EOF
}

main() {
    parse_args "$@"
    [[ $(id -u) = 0 || -n $ROOT ]] || die "run with sudo"
    grep -q '^ID=fedora' "$ROOT/etc/os-release" 2>/dev/null || die "this script is for Fedora"
    [[ -f $HERE/preinstall.sh && -f $HERE/mnemerynne.env.example ]] || die "run it from the Mnemerynne checkout (deploy/linux/setup.sh)"
    gather_storage
    gather_root
    gather_rest
    show_plan
    do_grow_root
    do_preinstall
    do_disks
    do_layout
    do_settings
    do_code
    do_units
    do_samba
    do_caddy
    do_admin
    do_share_init
    done_summary
}

[[ -n ${SETUP_SOURCE_ONLY:-} ]] || main "$@"
