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
    for path in ("usr/local/bin/mnemerynne-screen-session", "usr/local/bin/mnemerynne-screen-audio",
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
