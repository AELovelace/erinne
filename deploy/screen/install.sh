#!/bin/bash
# Make a Fedora box (Server or Everything, minimal install) into a Mnemerynne screen: at boot it opens
# Media's screen page full-screen on the TV (Firefox in kiosk mode under cage, on tty1), with sound over
# HDMI, and nothing else. Unpaired, the page shows a code to pair it with from a phone (Media →
# Screens → Pair a TV); no keyboard or sign-in is needed on the box. Self-contained: copy this one file
# to the box and run it. Safe to run again (to change the address, or after an upgrade).
#
#   sudo bash install.sh https://erynne.example.com             # set up, start
#   sudo bash install.sh https://erynne.example.com --dry-run   # what it would do, nothing written
#   sudo bash install.sh --check                                # codecs, video decoding, the server
#   sudo bash install.sh --uninstall
#
#   --audio hdmi|auto      sound over HDMI, also when the TV is turned on later (default), or leave
#                          the choice to PipeWire
#   --youtube-any-codec    let YouTube send VP9/AV1 (decoded on the CPU) instead of H.264 (the GPU)
#   --no-auto-updates      don't install security updates nightly (dnf automatic, rebooting if needed)
#   --no-packages          write the files only; install nothing with dnf
#   --dry-run              print the plan and stop
#
# What it needs from the hardware: an Intel GPU (VA-API decoding; 3rd generation Core and newer use the
# i965 driver, 5th and newer also intel-media-driver), HDMI to the TV, and the network. RPM Fusion
# (free) is enabled for the codecs: Fedora's own FFmpeg can't decode H.264, which is what Mnemerynne's
# transcoder sends.
set -Eeuo pipefail

ROOT=${SCREEN_ROOT:-}             # tests: a prefix in front of /etc and /usr/local
CONF=$ROOT/etc/mnemerynne-screen.conf
SHARE=$ROOT/usr/local/share/mnemerynne-screen
BIN=$ROOT/usr/local/bin
UNIT=$ROOT/etc/systemd/system/mnemerynne-screen.service
PAM_FILE=$ROOT/etc/pam.d/mnemerynne-screen
POLICIES=$ROOT/etc/firefox/policies/policies.json
DNF_AUTOMATIC=$ROOT/etc/dnf/dnf5-plugins/automatic.conf
SCREEN_USER=mnemerynne-screen

URL=""
AUDIO=hdmi
YOUTUBE_H264=1
AUTO_UPDATES=1
PACKAGES=1
DRY_RUN=0
MODE=install

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# A command that changes the system: printed instead on a dry run, or under a test root.
sys() {
    if [[ $DRY_RUN == 1 || -n $ROOT ]]; then
        printf '   + %s\n' "$*"
    else
        "$@"
    fi
}

# Write stdin to a file (mode, path), unless it is a dry run.
put() {
    local mode=$1 path=$2 tmp
    if [[ $DRY_RUN == 1 ]]; then
        printf '   write %s\n' "${path#"$ROOT"}"
        cat >/dev/null
        return
    fi
    mkdir -p "$(dirname -- "$path")"
    tmp=$(mktemp "$path.XXXXXX")
    cat >"$tmp"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$path"
}

# The server's address as an origin: https://host[:port], nothing after it.
clean_url() {
    local url=${1%/}
    url=${url%/media/screen}
    [[ $url =~ ^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]] ||
        { printf 'Give the address people open Mnemerynne at, like https://erynne.example.com (got "%s").\n' "$1" >&2; return 1; }
    printf '%s' "$url"
}

conf_value() {   # a value from the settings file; empty when unset
    [[ -f $CONF ]] || return 0
    sed -n "s/^$1=//p" "$CONF" | tail -n 1
}

# ------------------------------------------------------------------ files
render_conf() {
    cat <<EOF
# Mnemerynne screen (deploy/screen/install.sh). Run the installer again to change these; it rewrites
# this file and restarts the screen.
MNEMERYNNE_URL=$URL
AUDIO_OUTPUT=$AUDIO
EOF
}

# Runs inside cage as $SCREEN_USER: Firefox in kiosk mode, starting on the local page that waits for
# the server. When Firefox ends, cage does, and systemd starts both again.
render_session() {
    cat <<'EOF'
#!/bin/bash
# The Mnemerynne screen's session (deploy/screen/install.sh): run by cage on tty1.
set -u
. /etc/mnemerynne-screen.conf
PROFILE=$HOME/firefox
mkdir -p "$PROFILE"
rm -f "$PROFILE/lock" "$PROFILE/.parentlock"     # left by a power cut: Firefox would refuse to start
cp /usr/local/share/mnemerynne-screen/user.js "$PROFILE/user.js"
if [ "${AUDIO_OUTPUT:-hdmi}" = hdmi ]; then
    /usr/local/bin/mnemerynne-screen-audio >/dev/null 2>&1 &
fi
export MOZ_ENABLE_WAYLAND=1
exec firefox --kiosk --no-remote --profile "$PROFILE" file:///usr/local/share/mnemerynne-screen/start.html
EOF
}

# Sound over HDMI: the sound card's HDMI profile and sink, checked every 30 s because the TV may be off
# (HDMI audio unavailable) when the box starts. `--pick` reads `pactl list cards` and prints the card and
# profile it would choose (tests).
render_audio() {
    cat <<'EOF'
#!/bin/bash
# HDMI sound for the Mnemerynne screen (deploy/screen/install.sh).
set -u
pick() {
    awk '
        /^Card #/ { card = "" }
        /^\tName: / { card = $2 }
        /^\t\toutput:hdmi-stereo[^ ]*: / && !/available: no\)/ {
            p = $1; sub(/:$/, "", p)
            if (card != "" && (best == "" || p == "output:hdmi-stereo")) { best = p; bestcard = card }
        }
        END { if (best != "") print bestcard, best }'
}
if [ "${1:-}" = --pick ]; then pick; exit 0; fi

for _ in $(seq 60); do pactl info >/dev/null 2>&1 && break; sleep 1; done
while :; do
    choice=$(pactl list cards 2>/dev/null | pick)
    if [ -n "$choice" ]; then
        card=${choice% *} profile=${choice#* }
        active=$(pactl list cards | awk -v c="$card" '$1 == "Name:" { n = $2 } n == c && /Active Profile:/ { print $3 }')
        [ "$active" = "$profile" ] || pactl set-card-profile "$card" "$profile"
        sink=$(pactl list short sinks | awk '/hdmi/ { print $2; exit }')
        if [ -n "$sink" ] && [ "$(pactl get-default-sink)" != "$sink" ]; then
            pactl set-default-sink "$sink"
            pactl set-sink-mute "$sink" 0
            pactl set-sink-volume "$sink" 100%      # Media sets its own volume on top
        fi
    fi
    sleep 30
done
EOF
}

# What Firefox shows first: it waits until the server answers, then opens the screen page. Opening the
# server straight away would leave Firefox's error page up for good when the box starts before the
# network or the server does.
render_start() {
    cat <<EOF
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Mnemerynne screen</title>
<style>
html, body { margin: 0; height: 100%; background: #12031f; color: #f6d6ea; cursor: none; }
body { display: grid; place-content: center; gap: 1rem; text-align: center; font: 2rem system-ui, sans-serif; }
small { font-size: 1.2rem; color: #a98bb5; }
</style></head>
<body><div>📺 Connecting to ${URL#*://}…</div><small id="state"></small>
<script>
const SERVER = "$URL";
let tries = 0;
async function connect() {
  try {
    await fetch(SERVER + "/healthz", { mode: "no-cors", cache: "no-store" });
    location.replace(SERVER + "/media/screen");
  } catch (err) {
    tries += 1;
    document.getElementById("state").textContent =
      "Not reachable yet (" + tries + "). Check the network cable or Wi-Fi; trying again…";
    setTimeout(connect, 5000);
  }
}
connect();
</script></body></html>
EOF
}

# Firefox preferences, copied into the screen's profile at every start (so changes here always apply).
render_user_js() {
    cat <<'EOF'
// The Mnemerynne screen (deploy/screen/install.sh); rewritten at every start.
user_pref("media.autoplay.default", 0);                         // sound without pressing Start
user_pref("media.autoplay.blocking_policy", 0);
user_pref("media.ffmpeg.vaapi.enabled", true);                  // the GPU decodes video
user_pref("media.hardware-video-decoding.force-enabled", true);
user_pref("browser.sessionstore.resume_from_crash", false);     // no "restore session?" after a power cut
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.translations.automaticallyPopup", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("dom.disable_beforeunload", true);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("full-screen-api.transition-duration.enter", "0 0");
user_pref("full-screen-api.transition-duration.leave", "0 0");
user_pref("media.videocontrols.picture-in-picture.video-toggle.enabled", false);
EOF
    if [[ $YOUTUBE_H264 == 1 ]]; then
        cat <<'EOF'
// YouTube in H.264, which the GPU decodes; it sends VP9 or AV1 otherwise, decoded on the CPU.
user_pref("media.mediasource.vp9.enabled", false);
user_pref("media.av1.enabled", false);
EOF
    fi
}

render_policies() {
    cat <<EOF
{
  "policies": {
    "DisableAppUpdate": true,
    "DisableTelemetry": true,
    "DisableFirefoxStudies": true,
    "DisableFirefoxAccounts": true,
    "DisablePocket": true,
    "DisableProfileImport": true,
    "DontCheckDefaultBrowser": true,
    "NoDefaultBookmarks": true,
    "OfferToSaveLogins": false,
    "PasswordManagerEnabled": false,
    "OverrideFirstRunPage": "",
    "OverridePostUpdatePage": "",
    "UserMessaging": {"ExtensionRecommendations": false, "FeatureRecommendations": false,
                      "UrlbarInterventions": false, "SkipOnboarding": true, "MoreFromMozilla": false,
                      "Locked": true},
    "Permissions": {"Autoplay": {"Allow": ["$URL"], "Default": "allow-audio-video", "Locked": true}},
    "ExtensionSettings": {"*": {"installation_mode": "blocked"}}
  }
}
EOF
}

# cage on tty1 as the screen's user, with a real login session (logind gives it the seat, the GPU and
# input; pam_systemd starts the user's PipeWire). The layout is cage's own example unit.
render_unit() {
    cat <<EOF
# The Mnemerynne screen (deploy/screen/install.sh): Firefox in kiosk mode under cage, on tty1.
[Unit]
Description=Mnemerynne screen
After=systemd-user-sessions.service plymouth-quit-wait.service dbus.socket systemd-logind.service
After=network-online.target getty@tty1.service
Wants=network-online.target dbus.socket systemd-logind.service
Conflicts=getty@tty1.service
ConditionPathExists=/dev/tty1
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SCREEN_USER
PAMName=mnemerynne-screen
WorkingDirectory=~
ExecStart=/usr/bin/cage -d -- /usr/local/bin/mnemerynne-screen-session
Restart=always
RestartSec=3
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland

[Install]
WantedBy=multi-user.target
EOF
}

render_pam() {
    cat <<'EOF'
# The Mnemerynne screen's session (deploy/screen/install.sh): no password, a logind session.
auth     required pam_unix.so nullok
account  required pam_unix.so
session  required pam_loginuid.so
session  required pam_unix.so
session  required pam_systemd.so
EOF
}

render_dnf_automatic() {
    cat <<'EOF'
# The Mnemerynne screen (deploy/screen/install.sh): install updates nightly, rebooting when one needs it.
[commands]
apply_updates = yes
reboot = when-needed
EOF
}

# --------------------------------------------------------------- packages
install_packages() {
    step "Packages"
    local fedora
    fedora=$(rpm -E %fedora 2>/dev/null || echo "?")
    if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
        sys dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$fedora.noarch.rpm"
    fi
    # RPM Fusion's FFmpeg in place of ffmpeg-free (no H.264), and the VA-API drivers: i965 for 3rd and
    # 4th generation Intel, intel-media-driver from 5th on.
    if rpm -q ffmpeg-free >/dev/null 2>&1; then
        sys dnf swap -y ffmpeg-free ffmpeg --allowerasing
    fi
    sys dnf install -y --allowerasing --setopt=install_weak_deps=False \
        cage firefox ffmpeg ffmpeg-libs libva-intel-driver libva-utils mesa-dri-drivers \
        pipewire pipewire-alsa pipewire-pulseaudio wireplumber pulseaudio-utils curl \
        google-noto-sans-fonts google-noto-color-emoji-fonts
    sys dnf install -y --skip-unavailable intel-media-driver
    if [[ $AUTO_UPDATES == 1 ]]; then
        sys dnf install -y dnf5-plugin-automatic
    fi
}

# ------------------------------------------------------------------ steps
write_files() {
    step "Files"
    render_conf | put 0644 "$CONF"
    render_session | put 0755 "$BIN/mnemerynne-screen-session"
    render_audio | put 0755 "$BIN/mnemerynne-screen-audio"
    render_start | put 0644 "$SHARE/start.html"
    render_user_js | put 0644 "$SHARE/user.js"
    render_policies | put 0644 "$POLICIES"
    render_unit | put 0644 "$UNIT"
    render_pam | put 0644 "$PAM_FILE"
    if [[ $AUTO_UPDATES == 1 ]]; then
        render_dnf_automatic | put 0644 "$DNF_AUTOMATIC"
    fi
}

make_user() {
    step "The screen's user ($SCREEN_USER)"
    if id "$SCREEN_USER" >/dev/null 2>&1; then
        note "already there"
    else
        sys useradd --create-home --comment "Mnemerynne screen" --shell /sbin/nologin \
            --groups video,render,audio "$SCREEN_USER"
    fi
}

enable_service() {
    step "Service"
    sys restorecon -R /etc/mnemerynne-screen.conf /usr/local/bin /usr/local/share/mnemerynne-screen \
        /etc/firefox/policies /etc/systemd/system/mnemerynne-screen.service /etc/pam.d/mnemerynne-screen
    sys systemctl daemon-reload
    sys systemctl enable mnemerynne-screen.service
    if [[ $AUTO_UPDATES == 1 ]]; then
        sys systemctl enable --now dnf5-automatic.timer
    else
        sys systemctl disable --now dnf5-automatic.timer
    fi
    sys systemctl restart mnemerynne-screen.service
}

# ------------------------------------------------------------------ check
ok() { printf '   ok    %s\n' "$*"; }
bad() { printf '   FAIL  %s\n' "$*"; FAILED=1; }

check() {
    FAILED=0
    local url sample render
    url=$(conf_value MNEMERYNNE_URL)
    step "Codecs and video decoding"
    if rpm -q ffmpeg-libs >/dev/null 2>&1; then ok "RPM Fusion FFmpeg"; else bad "RPM Fusion FFmpeg isn't installed: H.264 won't play"; fi
    sample=$(mktemp --suffix=.mp4)
    if ffmpeg -v error -y -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 2 -c:v libx264 -profile:v high \
            -pix_fmt yuv420p "$sample" && ffmpeg -v error -i "$sample" -f null -; then
        ok "H.264 (High) decodes"
    else
        bad "H.264 doesn't decode"
    fi
    render=$(ls /dev/dri/renderD* 2>/dev/null | head -n 1 || true)
    if [[ -z $render ]]; then
        bad "no GPU render device (/dev/dri/renderD*)"
    elif ffmpeg -v error -hwaccel vaapi -hwaccel_device "$render" -hwaccel_output_format vaapi -i "$sample" -f null -; then
        ok "the GPU decodes H.264 (VA-API, $(vainfo --display drm --device "$render" 2>/dev/null | sed -n 's/.*Driver version: //p' | head -n 1))"
    else
        bad "the GPU doesn't decode H.264: video plays on the CPU (fine at 1080p on a Core i5, but warmer)"
    fi
    rm -f "$sample"

    step "The server"
    if [[ -z $url ]]; then
        bad "no address set (run the installer with it)"
    else
        if curl -fsS -o /dev/null --max-time 10 "$url/healthz"; then ok "$url answers"; else bad "$url doesn't answer (or its certificate isn't trusted)"; fi
        case $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url/media/screen") in
            200) ok "Media is on: the screen page answers" ;;
            404) bad "the screen page answers 404: turn Media on (Settings → Media) and set its groups" ;;
            *) bad "the screen page didn't answer" ;;
        esac
    fi

    step "The screen"
    if systemctl is-active --quiet mnemerynne-screen.service; then ok "running on tty1"; else bad "not running: journalctl -u mnemerynne-screen -b"; fi
    if [[ -n $(pactl --server "unix:/run/user/$(id -u "$SCREEN_USER" 2>/dev/null || echo 0)/pulse/native" get-default-sink 2>/dev/null | grep -i hdmi) ]]; then
        ok "sound goes to HDMI"
    else
        note "sound: not on HDMI yet (is the TV on? it switches within 30 s)"
    fi
    return $FAILED
}

uninstall() {
    step "Removing the Mnemerynne screen"
    sys systemctl disable --now mnemerynne-screen.service
    for f in "$UNIT" "$PAM_FILE" "$CONF" "$POLICIES" "$BIN/mnemerynne-screen-session" "$BIN/mnemerynne-screen-audio" "$DNF_AUTOMATIC"; do
        sys rm -f "${f#"$ROOT"}"
    done
    sys rm -rf /usr/local/share/mnemerynne-screen
    sys systemctl daemon-reload
    if id "$SCREEN_USER" >/dev/null 2>&1; then
        sys userdel --remove "$SCREEN_USER"
    fi
    note "The packages (cage, Firefox, RPM Fusion's FFmpeg) are left installed. Remove the screen under"
    note "Media → Screens too, so its key stops working."
}

# ------------------------------------------------------------------- main
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --audio) AUDIO=${2:-}; shift
                     [[ $AUDIO == hdmi || $AUDIO == auto ]] || die "--audio is hdmi or auto" ;;
            --youtube-any-codec) YOUTUBE_H264=0 ;;
            --no-auto-updates) AUTO_UPDATES=0 ;;
            --no-packages) PACKAGES=0 ;;
            --dry-run) DRY_RUN=1 ;;
            --check) MODE=check ;;
            --uninstall) MODE=uninstall ;;
            -h|--help) sed -n '2,/^set -E/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
            -*) die "unknown option $1 (see --help)" ;;
            *) [[ -z $URL ]] || die "one address only"; URL=$1 ;;
        esac
        shift
    done
}

main() {
    parse_args "$@"
    if [[ -z $ROOT && $DRY_RUN == 0 && $EUID -ne 0 ]]; then
        die "run it as root: sudo bash $0 $*"
    fi
    grep -qs '^ID=fedora' "$ROOT/etc/os-release" || die "this is for Fedora (see deploy/screen/README.md)"
    case $MODE in
        check) check; exit ;;
        uninstall) uninstall; exit 0 ;;
    esac
    [[ -n $URL ]] || URL=$(conf_value MNEMERYNNE_URL)
    [[ -n $URL ]] || die "give the Mnemerynne address: sudo bash $0 https://erynne.example.com"
    URL=$(clean_url "$URL") || exit 1
    [[ $URL == https://* ]] || note "warning: $URL isn't HTTPS; the screen key would cross the network in the clear"

    printf 'Mnemerynne screen for %s (sound: %s, YouTube: %s, nightly updates: %s)\n' "$URL" "$AUDIO" \
        "$([[ $YOUTUBE_H264 == 1 ]] && echo H.264 || echo any codec)" "$([[ $AUTO_UPDATES == 1 ]] && echo yes || echo no)"
    if [[ $PACKAGES == 1 ]]; then install_packages; fi
    make_user
    write_files
    enable_service
    if [[ $DRY_RUN == 1 || -n $ROOT ]]; then
        exit 0
    fi
    check || true
    cat <<EOF

The TV now shows a pairing code. On your phone: Media → Screens → Pair a TV, type the code (or scan
it) and name the screen. Check again any time with: sudo bash $0 --check
EOF
}

if [[ -z ${SCREEN_SOURCE_ONLY:-} ]]; then
    main "$@"
fi
