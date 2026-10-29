"""deploy/screen/install.sh, the Fedora kiosk for a Mnemerynne screen: the files it writes (Firefox's
policies and preferences, the session, the unit), the address check, the HDMI profile it picks from
pactl's list, and a dry run and an install under a test root. Runs under bash (Git Bash on Windows);
skipped where there is none."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from fakes import find_bash

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "deploy" / "screen" / "install.sh"
BASH = find_bash()

pytestmark = pytest.mark.skipif(not BASH, reason="needs bash")

# `pactl list cards` on a NUC with Intel HDA (analog and HDMI on one card), trimmed; HDMI 2 has no TV.
CARDS = """Card #42
\tName: alsa_card.pci-0000_00_1b.0
\tDriver: alsa
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)
\t\toutput:hdmi-stereo-extra1: Digital Stereo (HDMI 2) Output (sinks: 1, sources: 0, priority: 5700, available: no)
\t\toutput:hdmi-stereo+input:analog-stereo: Digital Stereo (HDMI) Output + Analog Stereo Input (sinks: 1, sources: 1, priority: 5965, available: yes)
\t\toutput:hdmi-stereo: Digital Stereo (HDMI) Output (sinks: 1, sources: 0, priority: 5900, available: yes)
\tActive Profile: output:analog-stereo
"""


def posix(path: Path) -> str:
    """The path as bash sees it (C:\\x -> /c/x under Git Bash)."""
    return subprocess.run([BASH, "-c", 'cd "$(dirname "$1")" && printf "%s/%s" "$(pwd)" "$(basename "$1")"', "_",
                           str(path)], capture_output=True, text=True, check=True).stdout.strip()


def bash(script: str, *, root: Path | None = None, check=True, stdin: str | None = None):
    env = {**os.environ, "SCREEN_SOURCE_ONLY": "1"}
    if root is not None:
        env["SCREEN_ROOT"] = posix(root)
    return subprocess.run([BASH, "-c", f'source "{posix(INSTALL)}"; {script}'], capture_output=True, text=True,
                          env=env, check=check, input=stdin)


def run(*args: str, root: Path, check=True):
    env = {k: v for k, v in os.environ.items() if k != "SCREEN_SOURCE_ONLY"} | {"SCREEN_ROOT": posix(root)}
    return subprocess.run([BASH, posix(INSTALL), *args], capture_output=True, text=True, env=env, check=check)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text("NAME=Fedora Linux\nID=fedora\n")
    return root


def test_the_script_parses():
    subprocess.run([BASH, "-n", posix(INSTALL)], check=True)


@pytest.mark.parametrize("given, url", [
    ("https://erynne.example.com", "https://erynne.example.com"),
    ("https://erynne.example.com/", "https://erynne.example.com"),
    ("https://erynne.example.com/media/screen", "https://erynne.example.com"),
    ("http://10.1.1.5:38200", "http://10.1.1.5:38200"),
])
def test_addresses_become_the_servers_origin(given, url):
    assert bash(f'clean_url "{given}"').stdout == url


@pytest.mark.parametrize("given", ["erynne.example.com", "https://erynne.example.com/files", "ftp://x",
                                   'https://x"; alert(1); "', ""])
def test_other_addresses_are_refused(given):
    r = bash(f"clean_url '{given}'", check=False)
    assert r.returncode != 0 and "Give the address" in r.stderr


def test_firefox_policies_allow_autoplay_for_the_server_only():
    policies = json.loads(bash('URL=https://erynne.example.com; render_policies').stdout)["policies"]
    assert policies["Permissions"]["Autoplay"] == {"Allow": ["https://erynne.example.com"],
                                                   "Default": "allow-audio-video", "Locked": True}
    assert policies["ExtensionSettings"] == {"*": {"installation_mode": "blocked"}}
    assert policies["DisableAppUpdate"] is True


def test_youtube_is_asked_for_h264_unless_told_otherwise():
    assert 'media.mediasource.vp9.enabled", false' in bash("render_user_js").stdout
    assert "vp9" not in bash("YOUTUBE_H264=0; render_user_js").stdout
    assert 'media.autoplay.default", 0' in bash("render_user_js").stdout


def test_the_start_page_waits_for_the_server():
    page = bash("URL=https://erynne.example.com; render_start").stdout
    assert 'const SERVER = "https://erynne.example.com"' in page
    assert 'SERVER + "/healthz"' in page and 'location.replace(SERVER + "/media/screen")' in page
    assert "Connecting to erynne.example.com" in page


def test_the_hdmi_profile_is_picked_from_pactl(tmp_path):
    audio = tmp_path / "audio.sh"
    audio.write_text(bash("render_audio").stdout, newline="\n")
    pick = lambda text: subprocess.run([BASH, posix(audio), "--pick"], input=text, capture_output=True,
                                       text=True, check=True).stdout.strip()
    assert pick(CARDS) == "alsa_card.pci-0000_00_1b.0 output:hdmi-stereo"
    # Only the combined profile: that one, then.
    only_combined = "\n".join(l for l in CARDS.splitlines() if not l.startswith("\t\toutput:hdmi-stereo: "))
    assert pick(only_combined) == "alsa_card.pci-0000_00_1b.0 output:hdmi-stereo+input:analog-stereo"
    # The TV is off: no HDMI profile is available, so nothing changes.
    tv_off = "\n".join(l for l in CARDS.splitlines() if "hdmi" not in l or "available: no" in l)
    assert pick(tv_off) == ""


# `wlr-randr` inside cage on a 4K TV over HDMI 1.4 (4K at 30 Hz at most), trimmed.
RANDR = """HDMI-A-1 "LG Electronics LG TV SSCR2 0x01010101 (HDMI-A-1)"
  Make: LG Electronics
  Enabled: yes
  Modes:
    3840x2160 px, 30.000000 Hz (preferred, current)
    3840x2160 px, 29.969999 Hz
    1920x1080 px, 59.939999 Hz
    1920x1080 px, 60.000000 Hz
    1920x1080 px, 50.000000 Hz
    1280x720 px, 60.000000 Hz
  Position: 0,0
  Scale: 1.000000
"""


def test_the_tvs_picture_follows_the_display_options(tmp_path):
    display = tmp_path / "display.sh"
    display.write_text(bash("render_display").stdout, newline="\n")
    plan = lambda mode, text=RANDR, scale="100": subprocess.run(
        [BASH, posix(display), "--plan", mode, scale], input=text, capture_output=True, text=True, check=True).stdout.strip()
    assert plan("auto") == "--output HDMI-A-1 --mode 1920x1080@60.000000Hz"     # the best 1080p the TV offers
    assert plan("1080p") == plan("auto")
    assert plan("native") == ""
    # Display scaling, with the mode or alone; nothing when the TV is already at it.
    assert plan("native", scale="150") == "--output HDMI-A-1 --scale 1.5"
    assert plan("auto", scale="200") == "--output HDMI-A-1 --mode 1920x1080@60.000000Hz --scale 2"
    scaled = RANDR.replace("Scale: 1.000000", "Scale: 2.000000")
    assert plan("native", scaled, "200") == "" and plan("native", scaled, "100") == "--output HDMI-A-1 --scale 1"
    # A 1080p TV: automatic leaves it alone; so does 1080p once it's there.
    full_hd = RANDR.replace(" (preferred, current)", " (preferred)").replace("60.000000 Hz\n    1920x1080 px, 50",
                                                                           "60.000000 Hz (current)\n    1920x1080 px, 50")
    assert plan("auto", full_hd) == "" and plan("1080p", full_hd) == ""
    small = "HDMI-A-1 \"TV\"\n  Modes:\n    1280x720 px, 60.000000 Hz (preferred, current)\n"
    assert plan("auto", small) == "" and plan("1080p", small) == ""              # no 1080p to ask for
    assert plan("auto", "") == ""                                                 # no TV at all
    # A box that drives 4K at 60 Hz (HDMI 2.0) keeps it; one that started at 30 Hz moves up to 60 when it can.
    at_60 = RANDR.replace("30.000000 Hz (preferred, current)", "60.000000 Hz (preferred, current)")
    assert plan("auto", at_60) == "" and plan("1080p", at_60) == "--output HDMI-A-1 --mode 1920x1080@60.000000Hz"
    offers_60 = RANDR.replace("3840x2160 px, 29.969999 Hz", "3840x2160 px, 60.000000 Hz")
    assert plan("auto", offers_60) == "--output HDMI-A-1 --mode 3840x2160@60.000000Hz"
    assert plan("native", offers_60) == ""
    # --check asks it for the mode the TV is in now.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "wlr-randr").write_text(f"#!/bin/sh\ncat <<'X'\n{RANDR}X\n", newline="\n")
    (fake / "wlr-randr").chmod(0o755)
    now = subprocess.run([BASH, "-c", f'PATH="{posix(fake)}:$PATH" "$BASH" "$0" --now', posix(display)], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert now == "3840x2160 at 30.000000 Hz on HDMI-A-1, scaling 100%"

    # The session sets it before Firefox starts; the option is checked.
    session = bash("render_session").stdout
    assert session.index("mnemerynne-screen-display") < session.index("exec firefox")
    for good in ("auto", "native", "1080p"):
        assert bash(f"valid_display {good}", check=False).returncode == 0, good
    for bad in ("4k", "scale:2", "1080p;reboot", "1080p "):
        assert bash(f"valid_display '{bad}'", check=False).returncode != 0, bad
    for good in ("100", "150", "200", "300"):
        assert bash(f"valid_scale {good}", check=False).returncode == 0, good
    for bad in ("125", "1.5", "200%", "200;reboot", ""):
        assert bash(f"valid_scale '{bad}'", check=False).returncode != 0, bad


def test_the_start_page_grows_on_a_4k_tv():
    page = bash("URL=https://erynne.example.com; render_start").stdout
    assert "@media (min-width: 3000px) { html { font-size: 200%; } }" in page


def test_the_unit_runs_cage_on_tty1_as_the_screen_user():
    unit = bash("render_unit").stdout
    assert "User=mnemerynne-screen" in unit and "PAMName=mnemerynne-screen" in unit
    assert "TTYPath=/dev/tty1" in unit and "Conflicts=getty@tty1.service" in unit and "Restart=always" in unit
    assert "ExecStart=/usr/bin/cage -d -- /usr/local/bin/mnemerynne-screen-session" in unit
    assert "pam_systemd.so" in bash("render_pam").stdout


def test_a_dry_run_writes_nothing(tmp_path):
    root = _root(tmp_path)
    out = run("https://erynne.example.com/", "--dry-run", root=root).stdout
    assert "Mnemerynne screen for https://erynne.example.com" in out
    assert "rpmfusion-free-release" in out and "dnf install" in out and "useradd" in out
    assert "write /etc/firefox/policies/policies.json" in out and "systemctl enable mnemerynne-screen.service" in out
    assert "write /usr/local/libexec/mnemerynne-screen-wifi" in out and "python3-qrcode" in out
    assert "firewall-cmd --permanent --zone=nm-shared --add-service=http" in out
    # Installed from this clone: its commit goes to the box's own copy, for the updater.
    assert "git clone --quiet --no-checkout" in out and "/opt/mnemerynne-screen" in out
    assert sorted(p.name for p in root.rglob("*") if p.is_file()) == ["os-release"]


def test_install_under_a_root_and_run_again(tmp_path):
    root = _root(tmp_path)
    run("https://erynne.example.com", "--no-packages", "--youtube-any-codec", root=root)
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "MNEMERYNNE_URL=https://erynne.example.com\n" in conf and "AUDIO_OUTPUT=hdmi\n" in conf
    assert "DISPLAY_MODE=auto\n" in conf
    for path in ("usr/local/bin/mnemerynne-screen-session", "usr/local/bin/mnemerynne-screen-audio",
                 "usr/local/bin/mnemerynne-screen-display",
                 "usr/local/share/mnemerynne-screen/start.html", "etc/systemd/system/mnemerynne-screen.service",
                 "etc/pam.d/mnemerynne-screen", "etc/dnf/dnf5-plugins/automatic.conf",
                 "etc/systemd/system/mnemerynne-screen-wifi.service",
                 "etc/NetworkManager/dnsmasq-shared.d/mnemerynne-screen.conf"):
        assert (root / path).is_file(), path
    helper = root / "usr/local/libexec/mnemerynne-screen-wifi"
    assert helper.read_bytes() == (INSTALL.parent / "wifi-setup.py").read_bytes()
    assert "WIFI_SETUP=yes\n" in conf
    assert "address=/#/10.42.0.1" in (root / "etc/NetworkManager/dnsmasq-shared.d/mnemerynne-screen.conf").read_text()
    assert "vp9" not in (root / "usr/local/share/mnemerynne-screen/user.js").read_text()
    session = (root / "usr/local/bin/mnemerynne-screen-session").read_text()
    assert "firefox --kiosk" in session and "start.html" in session and "http://127.0.0.1:9187/" in session
    subprocess.run([BASH, "-n", posix(root / "usr/local/bin/mnemerynne-screen-session")], check=True)
    subprocess.run([BASH, "-n", posix(root / "usr/local/bin/mnemerynne-screen-audio")], check=True)
    subprocess.run([BASH, "-n", posix(root / "usr/local/bin/mnemerynne-screen-display")], check=True)

    # Again without the address: it keeps the one it has; the options are as given this time.
    out = run("--no-packages", "--audio", "auto", "--no-auto-updates", "--no-wifi-setup", root=root).stdout
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "MNEMERYNNE_URL=https://erynne.example.com\n" in conf and "AUDIO_OUTPUT=auto\n" in conf
    assert "vp9" in (root / "usr/local/share/mnemerynne-screen/user.js").read_text()
    # The helper stays (the start page, Kiosk settings); only its hotspot is off.
    assert "WIFI_SETUP=no\n" in conf and "systemctl restart mnemerynne-screen-wifi.service" in out
    assert "firewall-cmd" not in out

    # --reconfigure: the box's options kept, a new address, no packages.
    out = run("https://other.example.com", "--reconfigure", root=root).stdout
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "MNEMERYNNE_URL=https://other.example.com\n" in conf and "AUDIO_OUTPUT=auto\n" in conf
    assert "WIFI_SETUP=no\n" in conf and "AUTO_UPDATES=no\n" in conf and "dnf install" not in out
    assert '"Allow": ["https://other.example.com"]' in (root / "etc/firefox/policies/policies.json").read_text()
    # --keep-options with one changed: the rest stay.
    run("--keep-options", "--no-packages", "--volume", "35", "--wifi-setup", root=root)
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "SYSTEM_VOLUME=35\n" in conf and "WIFI_SETUP=yes\n" in conf and "AUDIO_OUTPUT=auto\n" in conf
    assert run("--keep-options", "--no-packages", "--volume", "101", root=root, check=False).returncode != 0
    # The TV's picture: kept by --keep-options and --reconfigure, refused when it's nonsense.
    out = run("--keep-options", "--no-packages", "--display", "1080p", "--display-scale", "200", root=root).stdout
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "picture: 1080p at 200%" in out and "DISPLAY_MODE=1080p\n" in conf and "DISPLAY_SCALE=200\n" in conf
    run("--reconfigure", root=root)
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "DISPLAY_MODE=1080p\n" in conf and "DISPLAY_SCALE=200\n" in conf and "SYSTEM_VOLUME=35\n" in conf
    for bad in (["--display", "4k"], ["--display-scale", "125"]):
        assert run("--keep-options", "--no-packages", *bad, root=root, check=False).returncode != 0
    run("--no-packages", root=root)                                         # without --keep-options: the defaults
    conf = (root / "etc" / "mnemerynne-screen.conf").read_text()
    assert "DISPLAY_MODE=auto\n" in conf and "DISPLAY_SCALE=100\n" in conf


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_updates_move_between_screen_releases_and_roll_back(tmp_path):
    """--update and --rollback (Kiosk settings' updater) on the box's copy of the repository: newer
    screen-v* releases only, never over local changes; each runs that release's own installer."""
    upstream = tmp_path / "upstream"
    script = upstream / "deploy" / "screen" / "install.sh"
    script.parent.mkdir(parents=True)
    _git(tmp_path, "init", "--quiet", str(upstream))
    _git(upstream, "config", "user.email", "t@example.com")
    _git(upstream, "config", "user.name", "t")
    for n, tags in ((1, ["screen-v0.1.0"]), (2, ["screen-v0.2.0", "files-v1.0.0"])):
        # This release's installer, as the box would run it: says which release it is.
        script.write_text('#!/bin/bash\necho "installed $(git -C "$(dirname "$0")" describe --tags --match '
                          f"'screen-v*') $*\"\n# release {n}\n", newline="\n")
        _git(upstream, "add", "-A")
        _git(upstream, "commit", "--quiet", "-m", f"release {n}")
        for tag in tags:
            _git(upstream, "tag", "-a", tag, "-m", tag)
    root = _root(tmp_path)
    box = root / "opt" / "mnemerynne-screen"
    _git(tmp_path, "clone", "--quiet", str(upstream), str(box))
    _git(box, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", "screen-v0.1.0")
    previous = root / "var" / "lib" / "mnemerynne-screen" / "previous"

    for target, error in (("files-v1.0.0", "isn't a screen release"), ("screen-v9.9.9", "no release")):
        r = run("--update", target, root=root, check=False)
        assert r.returncode != 0 and error in r.stderr, r.stderr
    out = run("--update", "screen-v0.2.0", root=root).stdout
    assert "installed screen-v0.2.0 --keep-options" in out and previous.read_text() == "screen-v0.1.0\n"
    r = run("--update", "screen-v0.1.0", root=root, check=False)             # older: only by rolling back
    assert r.returncode != 0 and "doesn't follow on" in r.stderr

    out = run("--rollback", root=root).stdout
    assert "installed screen-v0.1.0 --keep-options" in out and not previous.exists()
    r = run("--rollback", root=root, check=False)
    assert r.returncode != 0 and "nothing to roll back to" in r.stderr

    (box / "deploy" / "screen" / "install.sh").write_text("# changed on the box\n")
    r = run("--update", "screen-v0.2.0", root=root, check=False)
    assert r.returncode != 0 and "local changes" in r.stderr


def test_it_is_for_fedora_and_needs_an_address(tmp_path):
    root = _root(tmp_path)
    r = run("--no-packages", root=root, check=False)
    assert r.returncode != 0 and "give the Mnemerynne address" in r.stderr
    (root / "etc" / "os-release").write_text("ID=debian\n")
    r = run("https://erynne.example.com", root=root, check=False)
    assert r.returncode != 0 and "this is for Fedora" in r.stderr


def _sound(tmp_path, output, default_sink="alsa_output.pci-0000_00_1b.0.hdmi-stereo"):
    """The sound script once (--once) with this AUDIO_OUTPUT, against a pactl that logs what it's told."""
    audio = tmp_path / "audio.sh"
    audio.write_text(bash("render_audio").stdout, newline="\n")
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (tmp_path / "cards.txt").write_text(CARDS, newline="\n")
    (fake / "pactl").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{posix(tmp_path)}/pactl.log"\n'
        'case "$*" in\n'
        f'  "list cards") cat "{posix(tmp_path)}/cards.txt" ;;\n'
        '  "list short sinks") printf "1\\talsa_output.pci-0000_00_1b.0.analog-stereo\\tPipeWire\\n"\n'
        '                      printf "2\\talsa_output.usb-Generic_USB_Audio-00.analog-stereo\\tPipeWire\\n" ;;\n'
        f'  get-default-sink) echo {default_sink} ;;\n'
        "esac\n", newline="\n")
    conf = tmp_path / "screen.conf"
    conf.write_text(f"AUDIO_OUTPUT={output}\nSYSTEM_VOLUME=40\n", newline="\n")
    (tmp_path / "pactl.log").unlink(missing_ok=True)
    subprocess.run([BASH, "-c", f'chmod +x "{posix(fake)}/pactl"; PATH="{posix(fake)}:$PATH" '
                                f'MNEMERYNNE_SCREEN_CONF="{posix(conf)}" bash "{posix(audio)}" --once'],
                   check=True, capture_output=True, text=True)
    log = tmp_path / "pactl.log"
    return log.read_text().splitlines() if log.exists() else []


def test_the_sound_goes_where_kiosk_settings_says(tmp_path):
    told = _sound(tmp_path, "card:alsa_card.usb-Generic_USB_Audio-00:output:analog-stereo+input:mono-fallback")
    assert "set-card-profile alsa_card.usb-Generic_USB_Audio-00 output:analog-stereo+input:mono-fallback" in told
    assert "set-default-sink alsa_output.usb-Generic_USB_Audio-00.analog-stereo" in told
    assert "set-sink-volume alsa_output.usb-Generic_USB_Audio-00.analog-stereo 40%" in told
    # HDMI: the card's HDMI profile (the TV is on in CARDS), as before.
    told = _sound(tmp_path, "hdmi")
    assert "set-card-profile alsa_card.pci-0000_00_1b.0 output:hdmi-stereo" in told
    # Automatic: nothing touched.
    assert not any(t.startswith("set-") for t in _sound(tmp_path, "auto"))


def test_a_chosen_sound_output_is_kept_and_checked(tmp_path):
    root = _root(tmp_path)
    card = "card:alsa_card.pci-0000_00_1b.0:output:analog-stereo"
    run("https://erynne.example.com", "--no-packages", "--audio", card, root=root)
    run("--keep-options", "--no-packages", root=root)
    assert f"AUDIO_OUTPUT={card}\n" in (root / "etc" / "mnemerynne-screen.conf").read_text()
    for bad in ("card:x;reboot", "hdmi2", "card:x"):
        r = run("--keep-options", "--no-packages", "--audio", bad, root=root, check=False)
        assert r.returncode != 0 and "--audio is" in r.stderr
