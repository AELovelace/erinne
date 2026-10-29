#!/usr/bin/python3
"""Wi-Fi setup and the start page for a Mnemerynne screen. install.sh puts it at
/usr/local/libexec/mnemerynne-screen-wifi, run as root by mnemerynne-screen-wifi.service.

The kiosk's Firefox starts on this helper's page (http://127.0.0.1:9187/), which waits for the Mnemerynne
server and then opens the screen page. When the box has had no network for OFFLINE_SECONDS, the helper
starts a hotspot (NetworkManager) and the TV shows how to join it from a phone. On the hotspot every name
resolves to the box (dnsmasq), so the phone's "sign in to the network" sheet opens the setup page
(http://10.42.0.1/), which lists the networks the box saw before and joins the one chosen. The same page
is on the TV at /wifi, for a keyboard. With nobody on the hotspot for RETRY_SECONDS, it steps aside for a
moment so NetworkManager can rejoin a network it knows (after a power cut the router may just be slow).

Who can reach it: the box itself (127.0.0.1) and phones on its hotspot, nobody on the real network.
Every request's Host must be the box's own address (no DNS rebinding); changes need a same-origin JSON
POST; nothing is sent with CORS headers, so other sites (a signage web page) can't read the answers.
Python's standard library, plus python3-qrcode for the QR codes (without it, the TV shows text only).

Ctrl+Shift+Alt+R on any keyboard restarts the kiosk (RestartKeys): read from the kernel's input devices,
so it works whatever Firefox shows, or when Firefox has stopped answering.
"""

from __future__ import annotations

import hashlib
import hmac
import http.cookies
import http.server
import json
import os
import re
import secrets
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

CONF = "/etc/mnemerynne-screen.conf"
LOCAL_PORT = 9187
HOTSPOT_IP = "10.42.0.1"           # NetworkManager's address for a shared (hotspot) connection
HOTSPOT_CON = "mnemerynne-setup"
OFFLINE_SECONDS = 90               # no network this long: the hotspot
RETRY_SECONDS = 180                # nobody on the hotspot this long: try the known networks again
RETRY_WAIT = 45
CONNECT_WAIT = 40
SCAN_CACHE = 15
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # easy to type off a TV

# Kiosk settings (Kiosk, below)
SOURCE_DIR = "/opt/mnemerynne-screen"        # the box's copy of the repository (install.sh); SOURCE_DIR in the conf
STATE_DIR = "/var/lib/mnemerynne-screen"      # "previous": the release before the last update
PIN_FILE = "/etc/mnemerynne-screen.pin"
SCREEN_USER = "mnemerynne-screen"
UNLOCK_SECONDS = 600                          # settings left alone this long lock again
PIN_TRIES = 5
PIN_LOCK_SECONDS = 300
COOKIE = "mnemerynne_kiosk"
RELEASE_RE = re.compile(r"^screen-v\d+\.\d+\.\d+$")
URL_RE = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$")
AUDIO_SCRIPT = "/usr/local/bin/mnemerynne-screen-audio"
# The settings file is sourced by bash (install.sh's scripts): only these, as install.sh's valid_audio.
AUDIO_RE = re.compile(r"^(hdmi|auto|card:[A-Za-z0-9._-]+:[A-Za-z0-9:+._-]+)$")
DISPLAY_SCRIPT = "/usr/local/bin/mnemerynne-screen-display"
DISPLAY_SCALES = (100, 150, 200, 300)    # percent, as install.sh's valid_scale
JOBS = {"update": "mnemerynne-screen-update", "config": "mnemerynne-screen-config",
        "system": "mnemerynne-screen-osupdate"}
JOB_NAMES = {"update": "updating the screen", "config": "changing the address", "system": "installing system updates"}

# The restart keys (RestartKeys, below)
INPUT_DIR = "/dev/input"
RESTART_KEYS_WAIT = 10                       # at most one restart this often (seconds)
INPUT_EVENT = struct.Struct("llHHi")         # struct input_event: the time (two longs), type, code, value
EV_KEY = 1
KEY_R = 19
RESTART_MODIFIERS = ((29, 97), (42, 54), (56, 100))   # Ctrl, Shift and Alt: the left and the right one


# ------------------------------------------------------------------ helpers
def read_conf(path: str = CONF) -> dict:
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def nm_split(line: str) -> list[str]:
    """One line of nmcli's terse output: fields split on ':', where '\\:' and '\\\\' are literal."""
    fields, cur, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ":":
            fields.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    fields.append(cur)
    return fields


def parse_networks(text: str) -> list[dict]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`: each named network once (its strongest
    access point), strongest first."""
    best: dict[str, dict] = {}
    for line in text.splitlines():
        f = nm_split(line)
        if len(f) < 4 or not f[1]:
            continue
        try:
            signal = int(f[2])
        except ValueError:
            signal = 0
        security = "" if f[3] in ("", "--") else f[3]
        seen = best.get(f[1])
        if seen is None or signal > seen["signal"]:
            best[f[1]] = {"ssid": f[1], "signal": signal, "secure": bool(security), "security": security,
                          "in_use": f[0] == "*" or (seen or {}).get("in_use", False)}
        elif f[0] == "*":
            seen["in_use"] = True
    return sorted(best.values(), key=lambda n: (-n["signal"], n["ssid"].lower()))


def wifi_qr(ssid: str, password: str) -> str:
    """What a phone camera joins a network from."""
    esc = lambda s: re.sub(r'([\\;,:"])', r"\\\1", s)  # noqa: E731
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def qr_svg(data: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    out = img.to_string()
    return out.decode() if isinstance(out, bytes) else out


def friendly(message: str) -> str:
    m = message.lower()
    if "secrets were required" in m or "psk" in m or "password" in m or "4-way handshake" in m:
        return "The password wasn't accepted."
    if "no network with ssid" in m or "not found" in m:
        return "The box can't see that network. Is it in range, and is the name exactly right?"
    if "timeout" in m or "timed out" in m:
        return "The network didn't answer in time."
    return message or "It didn't connect."


def box_suffix() -> str:
    """Four characters that stay the same for this box (its hotspot's name)."""
    try:
        with open("/etc/machine-id", encoding="ascii") as f:
            seed = f.read().strip()
    except OSError:
        seed = socket.gethostname()
    return hashlib.sha256(seed.encode()).hexdigest()[:4].upper()


# ------------------------------------------------------------ NetworkManager
class NM:
    """NetworkManager through nmcli (and iw, for who is on the hotspot)."""

    def __init__(self, run=None) -> None:
        self.run = run or self._run

    @staticmethod
    def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, "", str(exc)

    def state(self) -> str:
        return self.run(["nmcli", "-t", "-f", "STATE", "general"])[1].strip()

    def has_network(self) -> bool:
        # "connected (local only)" is what the hotspot alone looks like.
        return self.state() in ("connected", "connected (site only)")

    def wifi_device(self) -> str | None:
        for line in self.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])[1].splitlines():
            f = nm_split(line)
            if len(f) >= 2 and f[1] == "wifi":
                return f[0]
        return None

    def current_ssid(self) -> str | None:
        for line in self.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"])[1].splitlines():
            f = nm_split(line)
            if len(f) >= 2 and f[0] == "yes" and f[1]:
                return f[1]
        return None

    def scan(self, device: str) -> list[dict]:
        rc, out, _ = self.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                               "ifname", device, "--rescan", "yes"], timeout=30)
        return parse_networks(out) if rc == 0 else []

    def profiles(self) -> set[str]:
        return {nm_split(line)[0] for line in self.run(["nmcli", "-t", "-f", "NAME", "connection", "show"])[1].splitlines() if line}

    def hotspot_up(self, device: str, ssid: str, password: str) -> bool:
        self.run(["nmcli", "connection", "delete", "id", HOTSPOT_CON])
        rc = self.run(["nmcli", "device", "wifi", "hotspot", "ifname", device, "con-name", HOTSPOT_CON,
                       "ssid", ssid, "password", password])[0]
        if rc == 0:
            self.run(["nmcli", "connection", "modify", HOTSPOT_CON, "connection.autoconnect", "no"])
        return rc == 0

    def hotspot_down(self) -> None:
        self.run(["nmcli", "connection", "down", "id", HOTSPOT_CON])

    def stations(self, device: str) -> int:
        out = self.run(["iw", "dev", device, "station", "dump"])[1]
        return sum(1 for line in out.splitlines() if line.startswith("Station "))

    def connect(self, device: str, ssid: str, password: str, hidden: bool) -> tuple[bool, str]:
        self.run(["nmcli", "device", "wifi", "rescan", "ifname", device])
        args = ["nmcli", "--wait", str(CONNECT_WAIT), "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        args += ["ifname", device]
        if hidden:
            args += ["hidden", "yes"]
        rc, out, err = self.run(args, timeout=CONNECT_WAIT + 20)
        lines = (err or out).strip().splitlines()
        return rc == 0, lines[-1] if lines else ""

    def delete(self, name: str) -> None:
        self.run(["nmcli", "connection", "delete", "id", name])


# -------------------------------------------------------------------- setup
class Setup:
    """The state: network or not, the hotspot, the networks seen, the last attempt to join one."""

    def __init__(self, nm: NM, server: str, *, clock=time.monotonic, sleep=time.sleep, check_server=None,
                 suffix: str | None = None, hotspot: bool = True) -> None:
        self.nm, self.server, self.clock, self.sleep = nm, server, clock, sleep
        self.hotspot_enabled = hotspot          # WIFI_SETUP=no (install.sh --no-wifi-setup): never the hotspot
        self.check_server = check_server or self._check_server
        self.lock = threading.RLock()
        self.offline_since: float | None = None
        self.hotspot: dict | None = None
        self.networks: list[dict] = []
        self.scanned_at = -1e9
        self.attempt: dict | None = None
        self.busy = False
        self.server_ok = False
        self.network = False
        self.device: str | None = None
        self.ssid = "Mnemerynne-Setup-" + (suffix or box_suffix())
        self.on_hotspot = lambda up: None      # starts and stops the hotspot's web server

    def _check_server(self) -> bool:
        if not self.server:
            return False
        try:
            with urllib.request.urlopen(self.server + "/healthz", timeout=5) as r:
                return r.status == 200
        except Exception:   # noqa: BLE001 - any failure means "not reachable"
            return False

    # -- the loop, every few seconds
    def tick(self) -> None:
        """Decide under the lock; do the slow parts (scans, the hotspot, waiting) outside it, so the
        TV's status requests never wait on them."""
        action = None
        with self.lock:
            if self.busy:
                return
            now = self.clock()
            self.device = self.nm.wifi_device()
            self.network = self.nm.has_network()
            if self.hotspot is not None:
                if self.network:                       # a cable went in, say
                    action = "stop"
                elif self.device and self.nm.stations(self.device):
                    self.hotspot["quiet_since"] = now
                elif now - self.hotspot["quiet_since"] >= RETRY_SECONDS:
                    action, self.busy = "retry", True
            elif self.network:
                self.offline_since = None
            else:
                self.offline_since = self.offline_since if self.offline_since is not None else now
                if self.device and self.hotspot_enabled and now - self.offline_since >= OFFLINE_SECONDS:
                    action = "start"
        if action == "stop":
            self.stop_hotspot()
        elif action == "start":
            self.start_hotspot()
        elif action == "retry":
            self._retry_known()
        ok = self.network and self.check_server()
        with self.lock:
            self.server_ok = ok

    def start_hotspot(self) -> bool:
        device = self.device
        if not device:
            return False
        seen = self.nm.scan(device)                 # before: the radio can't scan while it is the hotspot
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(10))
        if not self.nm.hotspot_up(device, self.ssid, password):
            return False
        with self.lock:
            now = self.clock()
            if seen:
                self.networks, self.scanned_at = seen, now
            self.hotspot = {"ssid": self.ssid, "password": password, "since": now, "quiet_since": now}
        self.on_hotspot(True)
        return True

    def stop_hotspot(self) -> None:
        with self.lock:
            if self.hotspot is None:
                return
            self.hotspot = None
        self.nm.hotspot_down()
        self.on_hotspot(False)

    def _retry_known(self) -> None:
        """Step aside so NetworkManager can rejoin a network it knows; back if none comes. (busy is set.)"""
        try:
            self.stop_hotspot()
            waited = 0
            while waited < RETRY_WAIT:
                self.sleep(3)
                waited += 3
                if self.nm.has_network():
                    self.network, self.offline_since = True, None
                    return
            self.start_hotspot()
        finally:
            self.busy = False

    # -- joining a network
    def networks_now(self) -> list[dict]:
        with self.lock:
            scan = (self.hotspot is None and self.device and not self.busy
                    and self.clock() - self.scanned_at >= SCAN_CACHE)
            device = self.device
        if scan:
            seen = self.nm.scan(device)
            with self.lock:
                self.networks, self.scanned_at = seen, self.clock()
        with self.lock:
            return list(self.networks)

    def connect(self, ssid: str, password: str, hidden: bool) -> None:
        """In the background (the hotspot goes away meanwhile, with the phone that asked)."""
        with self.lock:
            if self.busy:
                raise ValueError("The box is already joining a network.")
            if not self.device:
                raise ValueError("This box has no Wi-Fi.")
            self.busy = True
            self.attempt = {"ssid": ssid, "state": "connecting", "error": None}
        threading.Thread(target=self._connect, args=(ssid, password, hidden), daemon=True).start()

    def _connect(self, ssid: str, password: str, hidden: bool) -> None:
        try:
            if self.hotspot is not None:
                self.stop_hotspot()
                self.sleep(2)
            existed = ssid in self.nm.profiles()
            ok, message = self.nm.connect(self.device, ssid, password, hidden)
            with self.lock:
                if ok:
                    self.attempt = {"ssid": ssid, "state": "connected", "error": None}
                    self.offline_since = None
                    return
                self.attempt = {"ssid": ssid, "state": "failed", "error": friendly(message)}
            if not existed:
                self.nm.delete(ssid)                 # don't keep a profile with a wrong password
            if not self.nm.has_network():
                self.start_hotspot()
        finally:
            with self.lock:
                self.busy = False

    def status(self, local: bool) -> dict:
        with self.lock:
            hotspot = None
            if self.hotspot is not None:
                hotspot = {"ssid": self.hotspot["ssid"], "setup_url": f"http://{HOTSPOT_IP}/"}
                if local:                            # the TV shows it; phones on the hotspot know it already
                    hotspot["password"] = self.hotspot["password"]
            out = {"server": self.server, "server_ok": self.server_ok, "network": self.network,
                   "wifi": bool(self.device), "ssid": None, "hotspot": hotspot,
                   "attempt": dict(self.attempt) if self.attempt else None, "busy": self.busy}
        if local and out["network"] and hotspot is None:
            out["ssid"] = self.nm.current_ssid()
        return out


# ------------------------------------------------------------ kiosk settings
class KioskError(ValueError):
    """A refusal; the message is safe to show. status is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def run_cmd(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def set_conf_value(path: str, key: str, value: str) -> None:
    """One KEY=value line of the settings file changed (or added), the rest kept; written atomically."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out, done = [], False
    for line in lines:
        if line.partition("=")[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def clean_url(url: str) -> str:
    """The server's address as an origin, like install.sh's clean_url; KioskError otherwise."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/media/screen"):
        url = url[: -len("/media/screen")]
    if not URL_RE.match(url):
        raise KioskError("Give the address people open Mnemerynne at, like https://erynne.example.com.", 422)
    return url


def parse_cards(text: str) -> list[dict]:
    """Each sound card's output profiles from `pactl list cards`: {card, device, profile, label, available}.
    Output-only profiles; one that also records (…+input:…) only when the card has no output-only one
    for the same output."""
    out: list[dict] = []
    card = device = None
    in_profiles = False
    for line in text.splitlines():
        if line.startswith("Card #"):
            card = device = None
            in_profiles = False
        elif line.startswith("\tName: "):
            card = line.split(": ", 1)[1].strip()
        elif line.strip().startswith("device.description = ") and device is None:
            device = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("\tProfiles:"):
            in_profiles = True
        elif in_profiles and line.startswith("\t\t") and not line.startswith("\t\t\t"):
            m = re.match(r"^\t\t(output:[^ ]+): (.+?) \(sinks: (\d+).*available: (\w+)\)\s*$", line)
            if m and card and int(m.group(3)) > 0:
                out.append({"card": card, "device": device or card, "profile": m.group(1), "label": m.group(2),
                            "available": m.group(4) != "no"})
        elif in_profiles and not line.startswith("\t\t"):
            in_profiles = False
    plain = {(c["card"], c["profile"]) for c in out if "+input:" not in c["profile"]}
    return [c for c in out if "+input:" not in c["profile"]
            or (c["card"], c["profile"].split("+input:")[0]) not in plain]


class Pins:
    """The kiosk PIN: optional (none on a new box), PIN_LENGTHS digits, kept as a scrypt hash in a root-only
    file. PIN_TRIES wrong ones lock it for PIN_LOCK_SECONDS (in memory: a restart of the helper resets it)."""

    def __init__(self, path: str = PIN_FILE, clock=time.monotonic) -> None:
        self.path, self.clock = path, clock
        self.fails: list[float] = []

    def is_set(self) -> bool:
        return os.path.exists(self.path)

    def set(self, pin: str) -> None:
        if not re.fullmatch(r"\d{4,12}", pin or ""):
            raise KioskError("The PIN is 4 to 12 digits.", 422)
        salt = os.urandom(16)
        digest = hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"scrypt$16384$8$1${salt.hex()}${digest.hex()}\n")
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    def check(self, pin: str) -> bool:
        now = self.clock()
        self.fails = [t for t in self.fails if t > now - PIN_LOCK_SECONDS]
        if len(self.fails) >= PIN_TRIES:
            wait = int(self.fails[0] + PIN_LOCK_SECONDS - now) + 1
            raise KioskError(f"Too many wrong PINs. Try again in {(wait + 59) // 60} minutes.", 429)
        try:
            with open(self.path, encoding="ascii") as f:
                _, n, r, p, salt, digest = f.read().strip().split("$")
            ok = hmac.compare_digest(hashlib.scrypt((pin or "").encode(), salt=bytes.fromhex(salt), n=int(n),
                                                    r=int(r), p=int(p), dklen=32).hex(), digest)
        except (OSError, ValueError):
            ok = False
        if not ok:
            self.fails.append(now)
        return ok


def http_json(method: str, url: str, body: dict | None = None, headers: dict | None = None,
              timeout: int = 10) -> tuple[int, dict]:
    """(status, JSON body) from the Mnemerynne server; status 0 when it couldn't be reached."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}),
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except Exception as exc:   # noqa: BLE001 - unreachable, bad certificate, not JSON
        return 0, {"detail": str(getattr(exc, "reason", exc))}


class Kiosk:
    """Kiosk settings: who has them open (a phone approval through the server, or the PIN), and what they
    change. The slow and disruptive work (the installer, updates, dnf) runs in its own transient systemd
    unit (JOBS), so it carries on when the installer restarts this helper and Firefox."""

    def __init__(self, setup: Setup, *, conf: str = CONF, pins: Pins | None = None, run=None, http=None,
                 clock=time.monotonic, state_dir: str = STATE_DIR, uid=None) -> None:
        self.setup, self.conf_path, self.clock, self.state_dir = setup, conf, clock, state_dir
        self.pins = pins or Pins(clock=clock)
        self.run = run or run_cmd
        self.http = http or http_json
        self.uid = uid or (lambda: __import__("pwd").getpwnam(SCREEN_USER).pw_uid)
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}       # cookie -> {until, via, admin, server, screen}

    # -- who has them open
    def _open(self, via: str, admin: str | None = None, server: str | None = None, screen: dict | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.sessions = {k: v for k, v in self.sessions.items() if v["until"] > now}
            self.sessions[token] = {"until": now + UNLOCK_SECONDS, "via": via, "admin": admin, "server": server,
                                    "screen": screen}
        return token

    def unlock_pin(self, pin: str) -> str:
        if not self.pins.is_set():
            raise KioskError("This box has no kiosk PIN. Open Kiosk settings from the TV's menu and approve "
                             "from an admin's phone.", 403)
        if not self.pins.check(pin):
            raise KioskError("That PIN isn't right.", 403)
        return self._open("pin")

    def unlock_grant(self, grant: str) -> str:
        if not self.setup.server:
            raise KioskError("No Mnemerynne address is set.", 409)
        status, body = self.http("POST", self.setup.server + "/api/media/kiosk/redeem", {"grant": grant})
        if status != 200:
            raise KioskError(body.get("detail") or "The server didn't accept the approval. Try again from the TV.", 403)
        return self._open("phone", body.get("admin"), body.get("session"), body.get("screen"))

    def session(self, token: str | None) -> dict | None:
        with self.lock:
            hit = self.sessions.get(token or "")
            now = self.clock()
            if hit is None or hit["until"] <= now:
                self.sessions.pop(token or "", None)
                return None
            hit["until"] = now + UNLOCK_SECONDS
            return dict(hit)

    def lock_up(self, token: str | None) -> None:
        with self.lock:
            hit = self.sessions.pop(token or "", None)
        if hit and hit["server"] and self.setup.server:
            self.http("POST", self.setup.server + "/api/media/kiosk/lock", None, {"X-Kiosk-Session": hit["server"]})

    def need(self, token: str | None) -> dict:
        found = self.session(token)
        if found is None:
            raise KioskError("Kiosk settings are locked.", 401)
        return found

    def address_open(self, token: str | None) -> bool:
        """The address may change when unlocked; and without a PIN while the server can't be reached (a
        new box, or a server that moved): the "can't connect" page's way out."""
        return self.session(token) is not None or (not self.pins.is_set() and not self.setup.server_ok)

    # -- settings
    def conf(self) -> dict:
        return read_conf(self.conf_path)

    def source(self) -> str:
        return self.conf().get("SOURCE_DIR") or SOURCE_DIR

    def set_address(self, token: str | None, url: str) -> str:
        if not self.address_open(token):
            raise KioskError("Kiosk settings are locked: unlock them with the PIN first.", 401)
        url = clean_url(url)
        status, body = self.http("GET", url + "/healthz")
        if status != 200 or "schema" not in body:
            detail = str(body.get("detail") or "")
            reason = (f"it answered {status}" if status else
                      "its certificate isn't trusted" if "CERTIFICATE" in detail.upper() else
                      "that name isn't known on this network" if "getaddrinfo" in detail or "Name or service" in detail else
                      "nothing answered there" if "refused" in detail.lower() else
                      "it didn't answer")
            raise KioskError(f"{url} isn't answering like a Mnemerynne server: {reason}.", 422)
        self.start_job("config", ["bash", self.source() + "/deploy/screen/install.sh", url, "--reconfigure"])
        return url

    def set_pin(self, token: str | None, pin: str | None) -> None:
        self.need(token)
        if pin:
            self.pins.set(pin)
        else:
            self.pins.clear()

    def volume(self) -> int:
        v = self.conf().get("SYSTEM_VOLUME", "100")
        return int(v) if v.isdigit() and int(v) <= 100 else 100

    def set_volume(self, token: str | None, level) -> int:
        self.need(token)
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            raise KioskError("The volume is 0 to 100.", 422)
        set_conf_value(self.conf_path, "SYSTEM_VOLUME", str(level))   # the HDMI script keeps it after a restart
        code, _, err = self.run(["runuser", "-u", SCREEN_USER, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{self.uid()}",
                                 "pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], 15)
        if code != 0:
            raise KioskError(f"Saved, but the sound didn't change now: {err.strip() or 'no sound server'}.", 409)
        return level

    def _as_screen_user(self, cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
        """A command in the kiosk's own sound session (PipeWire runs as the screen's user)."""
        return self.run(["runuser", "-u", SCREEN_USER, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{self.uid()}", *cmd],
                        timeout)

    def audio(self, token: str | None) -> dict:
        """The sound output setting and the choices: HDMI (following the TV), automatic, and each output
        profile of each sound card the box has (`pactl list cards`)."""
        self.need(token)
        _, out, _ = self._as_screen_user(["pactl", "list", "cards"])
        current = self.conf().get("AUDIO_OUTPUT", "hdmi")
        choices = [{"value": "hdmi", "label": "HDMI to the TV (switches over when the TV is turned on)", "available": True},
                   {"value": "auto", "label": "Automatic (the system chooses)", "available": True}]
        choices += [{"value": f"card:{c['card']}:{c['profile']}", "label": f"{c['device']} · {c['label']}",
                     "available": c["available"]} for c in parse_cards(out)]
        if not any(c["value"] == current for c in choices):     # a card that's unplugged right now
            choices.append({"value": current, "label": current.removeprefix("card:") + " (not connected)",
                            "available": False})
        return {"current": current, "choices": choices}

    def set_audio(self, token: str | None, value: str) -> str:
        if not AUDIO_RE.match(value or "") or value not in {c["value"] for c in self.audio(token)["choices"]}:
            raise KioskError("That isn't one of this box's sound outputs.", 422)
        set_conf_value(self.conf_path, "AUDIO_OUTPUT", value)   # the sound script re-reads it every 30 s
        self._as_screen_user([AUDIO_SCRIPT, "--once"], 30)       # and now
        return value

    def display(self, token: str | None) -> dict:
        """Display scaling (DISPLAY_SCALE, in percent) and the mode the TV is in now, from cage."""
        self.need(token)
        v = self.conf().get("DISPLAY_SCALE", "100")
        _, now, _ = self._as_screen_user([DISPLAY_SCRIPT, "--now"])
        return {"scale": int(v) if v.isdigit() and int(v) in DISPLAY_SCALES else 100, "choices": list(DISPLAY_SCALES),
                "now": now.strip()}

    def set_display_scale(self, token: str | None, scale) -> int:
        self.need(token)
        if isinstance(scale, bool) or scale not in DISPLAY_SCALES:
            raise KioskError("Display scaling is 100%, 150%, 200% or 300%.", 422)
        set_conf_value(self.conf_path, "DISPLAY_SCALE", str(scale))   # the session applies it at every start
        code, _, err = self._as_screen_user([DISPLAY_SCRIPT], 30)      # and now
        if code != 0:
            raise KioskError(f"Saved; it applies when the screen restarts ({err.strip() or 'the screen is not running'}).", 409)
        return scale

    def screen(self, token: str | None) -> dict | None:
        """The screen's own settings on the server (the top bar), with a phone approval only."""
        found = self.need(token)
        if not found["server"]:
            return None
        status, body = self.http("GET", self.setup.server + "/api/media/kiosk/screen", None,
                                 {"X-Kiosk-Session": found["server"]})
        return body.get("screen") if status == 200 else None

    def set_top_bar(self, token: str | None, on: bool) -> dict:
        found = self.need(token)
        if not found["server"]:
            raise KioskError("The top bar is kept on the server: open Kiosk settings from the TV's menu and "
                             "approve from an admin's phone to change it.", 403)
        status, body = self.http("PUT", self.setup.server + "/api/media/kiosk/screen", {"top_bar": bool(on)},
                                 {"X-Kiosk-Session": found["server"]})
        if status != 200:
            raise KioskError(body.get("detail") or "The server didn't take the change.", 409)
        return body["screen"]

    # -- releases and updates
    def _git(self, *args: str, timeout: int = 30) -> tuple[int, str]:
        code, out, _ = self.run(["git", "-C", self.source(), *args], timeout)
        return code, out.strip()

    def releases(self, fetch: bool = True) -> dict:
        """The installed release, the newer screen-v* releases (newest first) and the one to roll back to."""
        if self._git("rev-parse", "--git-dir")[0] != 0:
            return {"available": False, "reason": f"There's no copy of the repository in {self.source()}: run the "
                                                  "installer from a clone once (README)."}
        fetched = not fetch or self._git("fetch", "--quiet", "--tags", "--force", "origin", timeout=60)[0] == 0
        head = self._git("rev-parse", "HEAD")[1]
        code, current = self._git("describe", "--tags", "--exact-match", "--match", "screen-v*", "HEAD")
        current = current if code == 0 else head[:12]
        _, listing = self._git("tag", "-l", "screen-v*", "--sort=-v:refname",
                               "--format=%(refname:short)%09%(creatordate:short)%09%(contents:subject)")
        newer = []
        for line in listing.splitlines():
            tag, _, rest = line.partition("\t")
            date, _, subject = rest.partition("\t")
            if not RELEASE_RE.match(tag) or tag == current:
                continue
            if self._git("rev-parse", f"{tag}^{{commit}}")[1] == head:
                continue
            if self._git("merge-base", "--is-ancestor", "HEAD", f"refs/tags/{tag}")[0] == 0:
                newer.append({"tag": tag, "date": date, "notes": subject})
        try:
            with open(os.path.join(self.state_dir, "previous"), encoding="utf-8") as f:
                previous = f.readline().strip() or None
        except OSError:
            previous = None
        return {"available": True, "current": current, "newer": newer, "previous": previous, "fetched": fetched}

    def update(self, token: str | None, tag: str) -> None:
        self.need(token)
        if not RELEASE_RE.match(tag or "") or tag not in {r["tag"] for r in self.releases(fetch=False)["newer"]}:
            raise KioskError(f"{tag} isn't a newer release for this box.", 422)
        self.start_job("update", ["bash", self.source() + "/deploy/screen/install.sh", "--update", tag])

    def rollback(self, token: str | None) -> None:
        self.need(token)
        if not self.releases(fetch=False).get("previous"):
            raise KioskError("There's nothing to roll back to: no update was installed since.", 409)
        self.start_job("update", ["bash", self.source() + "/deploy/screen/install.sh", "--rollback"])

    def system_update(self, token: str | None) -> None:
        self.need(token)
        self.start_job("system", ["dnf5", "upgrade", "-y", "--refresh"])

    def reboot_needed(self) -> bool:
        """A newer kernel is installed than the one running."""
        _, running, _ = self.run(["uname", "-r"], 10)
        code, newest, _ = self.run(["rpm", "-q", "--last", "kernel-core"], 20)
        if code != 0 or not newest.strip():
            return False
        latest = newest.splitlines()[0].split()[0].removeprefix("kernel-core-")
        return bool(running.strip()) and latest != running.strip()

    # -- jobs (transient units)
    def job_active(self, kind: str) -> bool:
        return self.run(["systemctl", "is-active", "--quiet", JOBS[kind]], 10)[0] == 0

    def active_job(self) -> str | None:
        return next((kind for kind in JOBS if self.job_active(kind)), None)

    def start_job(self, kind: str, cmd: list[str]) -> None:
        busy = self.active_job()
        if busy:
            raise KioskError(f"The box is busy ({JOB_NAMES[busy]}). Wait for it to finish.", 409)
        self.run(["systemctl", "reset-failed", JOBS[kind]], 10)
        # The last line in the journal says how it ended (jobs() reads it).
        script = " ".join(shlex.quote(c) for c in cmd) + '; echo "== finished: $?"'
        code, _, err = self.run(["systemd-run", f"--unit={JOBS[kind]}", "--collect", "--quiet",
                                 f"--description=Mnemerynne screen: {JOB_NAMES[kind]}", "--", "bash", "-c", script], 30)
        if code != 0:
            raise KioskError(f"Couldn't start {JOB_NAMES[kind]}: {err.strip()}", 500)

    def jobs(self) -> dict:
        out = {}
        for kind, unit in JOBS.items():
            _, log, _ = self.run(["journalctl", "-u", unit, "-n", "12", "-o", "cat", "--no-pager"], 10)
            lines = [l for l in log.splitlines() if l.strip()]
            finished = next((l for l in reversed(lines) if l.startswith("== finished: ")), None)
            out[kind] = {"name": JOB_NAMES[kind], "active": self.job_active(kind), "log": lines[-8:],
                         "ok": None if finished is None else finished.endswith(": 0")}
        return out

    def restart(self, token: str | None, what: str) -> None:
        self.need(token)
        cmd = {"kiosk": ["systemctl", "restart", "mnemerynne-screen.service"], "box": ["systemctl", "reboot"]}[what]
        threading.Timer(1.5, self.run, args=(cmd, 30)).start()    # after this answer has gone

    # -- what the page shows
    def details(self) -> dict:
        conf = self.conf()
        os_name = read_conf("/etc/os-release").get("PRETTY_NAME", "").strip('"')
        _, addresses, _ = self.run(["hostname", "-I"], 5)
        st = self.setup.status(True)
        return {"hostname": socket.gethostname(), "addresses": addresses.split(), "os": os_name,
                "ssid": st["ssid"], "wifi": st["wifi"],
                "server": self.setup.server, "wifi_setup": conf.get("WIFI_SETUP", "yes") == "yes",
                "audio": conf.get("AUDIO_OUTPUT", "hdmi"), "source": self.source()}

    def state(self, token: str | None) -> dict:
        found = self.session(token)
        out = {"unlocked": found is not None, "pin_set": self.pins.is_set(), "address_open": self.address_open(token),
               "server": self.setup.server, "server_ok": self.setup.server_ok}
        if found is not None:
            out.update(via=found["via"], admin=found["admin"], screen=found["screen"], volume=self.volume(),
                       details=self.details(), reboot_needed=self.reboot_needed())
        return out


# ---------------------------------------------------------------------- web
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "mnemerynne-screen-wifi"

    def log_message(self, fmt, *args) -> None:   # the journal only needs errors
        pass

    @property
    def box(self) -> Setup:   # not "setup": BaseRequestHandler.setup() is called for every request
        return self.server.box

    def _send(self, code: int, body: str | bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json", "image/svg")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj))

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.hosts

    def do_GET(self) -> None:   # noqa: N802
        local = self.server.local
        if not self._host_ok():
            if not local:   # a phone's connectivity check, or any site: the setup page (its sign-in sheet)
                self._send(302, "", "text/plain", {"Location": f"http://{HOTSPOT_IP}/"})
            else:
                self._send(403, "Wrong address", "text/plain")
            return
        path = urlsplit(self.path).path
        if local and path.startswith(("/settings", "/api/kiosk/")):
            self._kiosk_get(path)
        elif path == "/" and local:
            self._send(200, START_PAGE, "text/html")
        elif local and path in ("/wifi", "/api/networks") and not self._wifi_open():
            if path == "/wifi":       # online: Wi-Fi is part of Kiosk settings, behind its unlock
                self._send(302, "", "text/plain", {"Location": "/settings"})
            else:
                self._json(401, {"detail": "Kiosk settings are locked."})
        elif path == "/wifi" or (path == "/" and not local):
            self._send(200, WIFI_PAGE.replace("{{LOCAL}}", "true" if local else "false"), "text/html")
        elif path == "/api/status":
            status = self.box.status(local)
            if local and self.kiosk is not None:     # the start page: "Updating…" while a job runs
                status["job"] = self.kiosk.active_job()
                status["address_open"] = self.kiosk.address_open(self._cookie())
            self._json(200, status)
        elif path == "/api/networks":
            self._json(200, {"networks": self.box.networks_now()})
        elif path in ("/qr/join.svg", "/qr/setup.svg") and local:
            st = self.box.status(True)
            if not st["hotspot"]:
                self._send(404, "No hotspot", "text/plain")
                return
            data = (wifi_qr(st["hotspot"]["ssid"], st["hotspot"]["password"]) if path == "/qr/join.svg"
                    else st["hotspot"]["setup_url"])
            svg = qr_svg(data)
            self._send(200, svg, "image/svg+xml") if svg else self._send(404, "No QR codes here", "text/plain")
        elif not local:
            self._send(302, "", "text/plain", {"Location": f"http://{HOTSPOT_IP}/"})
        else:
            self._send(404, "Not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or self.headers.get("Origin") not in self.server.origins \
                or not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(403, {"detail": "Not from this page."})
            return
        path = urlsplit(self.path).path
        if self.server.local and self.kiosk is not None and path.startswith("/api/kiosk/"):
            self._kiosk_post(path)
            return
        if path != "/api/connect":
            self._json(404, {"detail": "Not found"})
            return
        if self.server.local and not self._wifi_open():
            self._json(401, {"detail": "Kiosk settings are locked."})
            return
        try:
            body = self._body()
            ssid = str(body.get("ssid", "")).strip()
            password = str(body.get("password", ""))
            hidden = bool(body.get("hidden", False))
        except (ValueError, TypeError):
            self._json(400, {"detail": "That wasn't understood."})
            return
        if not ssid or len(ssid.encode()) > 32:
            self._json(422, {"detail": "A network's name is 1 to 32 characters."})
            return
        if password and not 8 <= len(password) <= 63:
            self._json(422, {"detail": "Wi-Fi passwords are 8 to 63 characters (or none, for an open network)."})
            return
        try:
            self.box.connect(ssid, password, hidden)
        except ValueError as exc:
            self._json(409, {"detail": str(exc)})
            return
        self._json(202, {"status": "connecting"})

    # -- Kiosk settings: the box's own screen only (127.0.0.1), never the hotspot
    @property
    def kiosk(self) -> "Kiosk | None":
        return getattr(self.server, "kiosk", None)

    def _wifi_open(self) -> bool:
        """Wi-Fi on the TV: with Kiosk settings unlocked; or open while the box has no network (a new box,
        the start page's "Set up Wi-Fi with a keyboard") or its hotspot is up."""
        k = self.kiosk
        if k is None or not self.box.network or self.box.hotspot is not None:
            return True
        return k.session(self._cookie()) is not None

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("not an object")
        return body

    def _cookie(self) -> str | None:
        try:
            morsel = http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
        except http.cookies.CookieError:
            return None
        return morsel.value if morsel else None

    def _kiosk_get(self, path: str) -> None:
        k, token = self.kiosk, self._cookie()
        if k is None:
            self._send(404, "Not found", "text/plain")
            return
        try:
            if path == "/settings":
                self._send(200, SETTINGS_PAGE, "text/html")
            elif path == "/api/kiosk/state":
                self._json(200, k.state(token))
            elif path == "/api/kiosk/releases":
                k.need(token)
                self._json(200, k.releases())
            elif path == "/api/kiosk/jobs":
                k.need(token)
                self._json(200, k.jobs())
            elif path == "/api/kiosk/screen":
                self._json(200, {"screen": k.screen(token)})
            elif path == "/api/kiosk/audio":
                self._json(200, k.audio(token))
            elif path == "/api/kiosk/display":
                self._json(200, k.display(token))
            else:
                self._send(404, "Not found", "text/plain")
        except KioskError as exc:
            self._json(exc.status, {"detail": str(exc)})

    def _kiosk_post(self, path: str) -> None:
        k, token = self.kiosk, self._cookie()
        try:
            body = self._body()
        except (ValueError, TypeError):
            self._json(400, {"detail": "That wasn't understood."})
            return
        cookie = "{}={}; HttpOnly; SameSite=Strict; Path=/; Max-Age={}"
        try:
            if path == "/api/kiosk/unlock":
                fresh = (k.unlock_grant(str(body["grant"])) if body.get("grant")
                         else k.unlock_pin(str(body.get("pin", ""))))
                self._send(200, json.dumps(k.state(fresh)), extra={"Set-Cookie": cookie.format(COOKIE, fresh, UNLOCK_SECONDS)})
            elif path == "/api/kiosk/lock":
                k.lock_up(token)
                self._send(200, json.dumps({"unlocked": False}), extra={"Set-Cookie": cookie.format(COOKIE, "", 0)})
            elif path == "/api/kiosk/address":
                self._json(202, {"address": k.set_address(token, str(body.get("url", "")))})
            elif path == "/api/kiosk/pin":
                k.set_pin(token, str(body["pin"]) if body.get("pin") else None)
                self._json(200, {"pin_set": k.pins.is_set()})
            elif path == "/api/kiosk/volume":
                self._json(200, {"volume": k.set_volume(token, body.get("level"))})
            elif path == "/api/kiosk/audio":
                self._json(200, {"current": k.set_audio(token, str(body.get("output", "")))})
            elif path == "/api/kiosk/display-scale":
                self._json(200, {"scale": k.set_display_scale(token, body.get("scale"))})
            elif path == "/api/kiosk/top-bar":
                self._json(200, {"screen": k.set_top_bar(token, bool(body.get("on")))})
            elif path == "/api/kiosk/update":
                k.update(token, str(body.get("tag", "")))
                self._json(202, {"job": "update"})
            elif path == "/api/kiosk/rollback":
                k.rollback(token)
                self._json(202, {"job": "update"})
            elif path == "/api/kiosk/system-update":
                k.system_update(token)
                self._json(202, {"job": "system"})
            elif path in ("/api/kiosk/restart", "/api/kiosk/reboot"):
                k.restart(token, "kiosk" if path.endswith("restart") else "box")
                self._json(202, {"status": "restarting"})
            else:
                self._json(404, {"detail": "Not found"})
        except KioskError as exc:
            self._json(exc.status, {"detail": str(exc)})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, setup: Setup, local: bool, kiosk: "Kiosk | None" = None) -> None:
        self.box, self.local, self.kiosk = setup, local, (kiosk if local else None)
        super().__init__(address, Handler)
        if local:
            own = f"127.0.0.1:{self.server_address[1]}"
            self.hosts = {own}
            self.origins = {f"http://{own}"}
        else:
            self.hosts = {HOTSPOT_IP, f"{HOTSPOT_IP}:80"}
            self.origins = {f"http://{HOTSPOT_IP}"}


# -------------------------------------------------------------------- pages
_STYLE = """
:root { color-scheme: dark; }
html, body { margin: 0; min-height: 100%; background: #12031f; color: #f6d6ea; font: 18px/1.5 system-ui, sans-serif; }
body { font-size: 1rem; }
/* A 4K TV drawn at its full size (install.sh --display native): bigger, like the TV page's automatic text size. */
@media (min-width: 2400px) { html { font-size: 27px; } }
@media (min-width: 3000px) { html { font-size: 36px; } }
main { max-width: 64rem; margin: 0 auto; padding: 4vh 1.2rem; }
h1 { font-size: 2.2rem; margin: 0 0 .6rem; color: #ff5cad; }
.muted { color: #b597c4; }
button, .button { font: inherit; padding: .55rem 1.1rem; border: 0; border-radius: .5rem; background: #ff2e97; color: #12031f;
                  cursor: pointer; text-decoration: none; display: inline-block; }
button.secondary, .button.secondary { background: #3b1450; color: #f6d6ea; }
button:focus-visible, .button:focus-visible, input:focus-visible { outline: 3px solid #4de8ff; outline-offset: 2px; }
input { font: inherit; padding: .5rem .7rem; border-radius: .5rem; border: 1px solid #6b3a85; background: #1f0a2e; color: inherit; width: 100%; box-sizing: border-box; }
.error { color: #ff8a8a; }
[hidden] { display: none !important; }
"""

START_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mnemerynne screen</title><style>""" + _STYLE + """
body { cursor: none; }
main { min-height: 92vh; display: grid; place-content: center; text-align: center; max-width: 80rem; }
.steps { display: flex; gap: 4rem; justify-content: center; align-items: flex-start; margin: 2rem 0; text-align: left; }
.step { max-width: 22rem; font-size: 1.25rem; }
.qr { width: 14rem; height: 14rem; background: #fff; padding: .6rem; border-radius: .6rem; box-sizing: border-box; }
.qr svg, .qr img { width: 100%; height: 100%; display: block; }
.big { font-size: 1.6rem; font-weight: 600; color: #fff; }
</style></head>
<body><main>
  <section id="wait">
    <h1>📺 <span id="title">Connecting…</span></h1>
    <p class="muted" id="detail"></p>
    <p><a class="button secondary" href="/wifi" id="wifi-link" hidden>Set up Wi-Fi with a keyboard</a>
       <a class="button secondary" href="/settings#address" id="addr-link" hidden>Change the Mnemerynne address</a></p>
  </section>
  <section id="setup" hidden>
    <h1>📶 Set up Wi-Fi</h1>
    <p>This screen isn't on a network. Use your phone:</p>
    <div class="steps">
      <div class="step"><div class="qr"><img id="qr-join" alt=""></div>
        <p><b>1.</b> Scan to join the Wi-Fi<br><span class="big" id="hs-ssid"></span><br>password <span class="big" id="hs-pass"></span></p></div>
      <div class="step"><div class="qr"><img id="qr-setup" alt=""></div>
        <p><b>2.</b> Your phone may open the setup page by itself. If not, scan this, or open <span class="big">http://10.42.0.1</span></p></div>
    </div>
    <p class="error" id="setup-error"></p>
    <p class="muted">Or plug in a network cable. <a href="/wifi" style="color:inherit">Set up with a keyboard</a></p>
  </section>
</main>
<script>
"use strict";
let shownFor = null, failingSince = null;
const JOB_TITLES = { update: "Updating the screen…", config: "Changing the Mnemerynne address…", system: "Installing system updates…" };
async function poll() {
  let s;
  try { s = await (await fetch("/api/status", { cache: "no-store" })).json(); }
  catch (e) { setTimeout(poll, 3000); return; }
  const host = (s.server || "").replace(/^https?:\\/\\//, "");
  const wait = document.getElementById("wait"), setup = document.getElementById("setup");
  if (s.job) {                                       // Kiosk settings started it; the screen restarts after
    setup.hidden = true; wait.hidden = false;
    document.getElementById("title").textContent = JOB_TITLES[s.job] || "Working…";
    document.getElementById("detail").textContent = "This takes a few minutes. The screen carries on by itself.";
    document.getElementById("wifi-link").hidden = document.getElementById("addr-link").hidden = true;
    setTimeout(poll, 3000);
    return;
  }
  if (s.server_ok) { location.replace(s.server + "/media/screen?kiosk=1"); return; }
  failingSince = failingSince || Date.now();
  // Not connecting for a while (or no address yet): a way to change the address (the PIN, when one is set).
  document.getElementById("addr-link").hidden = !(s.server === "" || Date.now() - failingSince > 20000);
  if (s.hotspot) {
    wait.hidden = true; setup.hidden = false;
    document.getElementById("hs-ssid").textContent = s.hotspot.ssid;
    document.getElementById("hs-pass").textContent = s.hotspot.password;
    if (shownFor !== s.hotspot.password) {          // a new hotspot: new codes
      shownFor = s.hotspot.password;
      document.getElementById("qr-join").src = "/qr/join.svg?" + Date.now();
      document.getElementById("qr-setup").src = "/qr/setup.svg?" + Date.now();
    }
    const a = s.attempt;
    document.getElementById("setup-error").textContent = a && a.state === "failed" ? "Couldn't join " + a.ssid + ": " + a.error : "";
  } else {
    setup.hidden = true; wait.hidden = false;
    const a = s.attempt;
    document.getElementById("title").textContent =
      a && a.state === "connecting" ? "Joining " + a.ssid + "…" :
      s.network ? "Connecting to " + host + "…" : "Waiting for the network…";
    document.getElementById("detail").textContent =
      s.network ? "The network is up, but " + host + " isn't answering yet." :
      s.wifi ? "If no network turns up within a minute or two, this screen offers a Wi-Fi hotspot to set it up from a phone." :
      "Check the network cable.";
    document.getElementById("wifi-link").hidden = !s.wifi;
  }
  setTimeout(poll, 3000);
}
poll();
</script></body></html>
"""

WIFI_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wi-Fi · Mnemerynne screen</title><style>""" + _STYLE + """
ul { list-style: none; padding: 0; margin: 1rem 0; display: grid; gap: .5rem; }
li button { width: 100%; text-align: left; display: flex; justify-content: space-between; background: #26103a; color: #f6d6ea; }
li button.on { outline: 2px solid #ff2e97; }
form { display: grid; gap: .7rem; margin: 1rem 0; max-width: 30rem; }
.row { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
</style></head>
<body><main>
  <h1>📶 Wi-Fi</h1>
  <p class="muted" id="now"></p>
  <ul id="list"><li class="muted">Looking for networks…</li></ul>
  <div class="row"><button class="secondary" type="button" id="other">Another network (hidden)…</button>
    <button class="secondary" type="button" id="refresh">Look again</button>
    <a class="button secondary" href="/" id="back" hidden>Back to the screen</a></div>
  <form id="form" hidden>
    <label>Network <input id="ssid" maxlength="32" autocomplete="off" spellcheck="false"></label>
    <label>Password <input id="password" type="password" maxlength="63" autocomplete="off"></label>
    <label class="row"><input type="checkbox" id="show" style="width:auto"> Show the password</label>
    <div class="row"><button type="submit">Join</button><button class="secondary" type="button" id="cancel">Cancel</button></div>
  </form>
  <p id="msg" role="status"></p>
</main>
<script>
"use strict";
const LOCAL = {{LOCAL}};
const $ = function (id) { return document.getElementById(id); };
let hidden = false, chosen = null;
$("back").hidden = !LOCAL;
if (/[?&]from=settings/.test(location.search)) { $("back").href = "/settings"; $("back").textContent = "Back to Kiosk settings"; }
function bars(n) { return n >= 75 ? "▂▄▆█" : n >= 50 ? "▂▄▆" : n >= 25 ? "▂▄" : "▂"; }
function choose(net) {
  chosen = net; hidden = !net;
  $("form").hidden = false;
  $("ssid").value = net ? net.ssid : "";
  $("ssid").readOnly = !!net;
  $("password").value = "";
  $("password").parentElement.hidden = !!(net && !net.secure);
  (net ? $("password") : $("ssid")).focus();
}
async function load() {
  try {
    const s = await (await fetch("/api/status", { cache: "no-store" })).json();
    $("now").textContent = s.ssid ? "Now on " + s.ssid + "." : s.hotspot ? "Choose the network this screen should use." : "";
    const nets = (await (await fetch("/api/networks", { cache: "no-store" })).json()).networks;
    const list = $("list");
    list.replaceChildren();
    if (!nets.length) { const li = document.createElement("li"); li.className = "muted"; li.textContent = "No networks found."; list.appendChild(li); }
    nets.forEach(function (n) {
      const li = document.createElement("li"), b = document.createElement("button");
      b.type = "button";
      if (n.in_use) b.className = "on";
      const name = document.createElement("span"); name.textContent = n.ssid + (n.in_use ? " (connected)" : "");
      const sig = document.createElement("span"); sig.textContent = (n.secure ? "🔒 " : "") + bars(n.signal);
      b.append(name, sig);
      b.addEventListener("click", function () { choose(n); });
      li.appendChild(b); list.appendChild(li);
    });
  } catch (e) { $("msg").textContent = "The screen didn't answer. Are you still on its Wi-Fi?"; }
}
async function watch() {
  try {
    const s = await (await fetch("/api/status", { cache: "no-store" })).json();
    const a = s.attempt;
    if (a && a.state === "connecting") { setTimeout(watch, 2000); return; }
    if (a && a.state === "connected") { $("msg").textContent = "Joined " + a.ssid + "."; if (LOCAL) setTimeout(function () { location.href = $("back").href; }, 1500); return; }
    if (a && a.state === "failed") { $("msg").className = "error"; $("msg").textContent = "Couldn't join " + a.ssid + ": " + a.error; load(); return; }
  } catch (e) { setTimeout(watch, 3000); }
}
$("form").addEventListener("submit", async function (e) {
  e.preventDefault();
  $("msg").className = "";
  const body = { ssid: $("ssid").value.trim(), password: $("password").value, hidden: hidden };
  const r = await fetch("/api/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(function () { return null; });
  if (!r) { $("msg").textContent = "The screen didn't answer."; return; }
  if (r.status !== 202) { $("msg").className = "error"; $("msg").textContent = ((await r.json().catch(function () { return {}; })).detail) || "It didn't work."; return; }
  $("form").hidden = true;
  $("msg").textContent = LOCAL ? "Joining " + body.ssid + "…"
    : "Joining " + body.ssid + "… This hotspot goes away now. If the TV moves on to Mnemerynne, it worked; if the hotspot comes back, join it again and check the password.";
  setTimeout(watch, 2000);
});
$("show").addEventListener("change", function () { $("password").type = $("show").checked ? "text" : "password"; });
$("other").addEventListener("click", function () { choose(null); });
$("cancel").addEventListener("click", function () { $("form").hidden = true; chosen = null; $("msg").textContent = ""; $("other").focus(); });
$("refresh").addEventListener("click", load);
load();
</script></body></html>
"""


SETTINGS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kiosk settings · Mnemerynne screen</title><style>""" + _STYLE + """
main { max-width: 72rem; }
header { display: flex; flex-wrap: wrap; gap: .8rem 1.2rem; align-items: center; margin-bottom: 1rem; }
header h1 { margin: 0; flex: 1 1 auto; }
section.card { background: #1f0a2e; border: 1px solid #3b1450; border-radius: .8rem; padding: 1rem 1.3rem; margin: 0 0 1rem; }
section.card h2 { margin: 0 0 .6rem; font-size: 1.3rem; color: #ffd2ea; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(30rem, 1fr)); gap: 1rem; }
.grid section.card { margin: 0; }
.row { display: flex; gap: .7rem; flex-wrap: wrap; align-items: center; margin: .5rem 0; }
.row input[type=text], .row input[type=url], .row input[type=password] { flex: 1 1 16rem; width: auto; }
input[type=range] { flex: 1 1 14rem; accent-color: #ff2e97; }
select { font: inherit; padding: .45rem .6rem; border-radius: .5rem; border: 1px solid #6b3a85; background: #1f0a2e;
         color: inherit; flex: 1 1 16rem; min-width: 0; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; margin: 0; }
dt { color: #b597c4; } dd { margin: 0; overflow-wrap: anywhere; }
ul.releases { list-style: none; padding: 0; margin: .4rem 0; display: grid; gap: .4rem; }
ul.releases li { display: flex; gap: .8rem; align-items: center; justify-content: space-between; background: #26103a;
                 padding: .5rem .8rem; border-radius: .5rem; }
pre { background: #0d0216; padding: .6rem .8rem; border-radius: .5rem; white-space: pre-wrap; max-height: 14rem; overflow: auto;
      font-size: .85rem; margin: .4rem 0 0; }
.ok { color: #7dffb3; }
label.switch { display: flex; gap: .7rem; align-items: center; cursor: pointer; }
label.switch input { width: 1.5rem; height: 1.5rem; accent-color: #ff2e97; }
[hidden] { display: none !important; }
</style></head>
<body><main>
  <header>
    <h1>⚙ Kiosk settings</h1>
    <span class="muted" id="who"></span>
    <a class="button secondary" href="/">Back to the TV</a>
    <button class="secondary" type="button" id="lock" hidden>Lock</button>
  </header>
  <p id="msg" role="status"></p>

  <section class="card" id="locked" hidden>
    <h2>Locked</h2>
    <p id="locked-how">Open <b>Kiosk settings</b> from the TV's menu, and an admin approves on their phone.</p>
    <form class="row" id="pin-form" hidden>
      <input type="password" id="pin" inputmode="numeric" autocomplete="off" maxlength="12" placeholder="Kiosk PIN" aria-label="Kiosk PIN">
      <button type="submit">Unlock</button>
    </form>
  </section>

  <section class="card" id="address-card" hidden>
    <h2>Mnemerynne address</h2>
    <p class="muted" id="address-note">The server this screen shows. Changing it restarts the screen on the new one, where it shows a code to pair it again.</p>
    <form class="row" id="address-form">
      <input type="url" id="address" spellcheck="false" autocomplete="off" placeholder="https://erynne.example.com" aria-label="Mnemerynne address">
      <button type="submit">Change</button>
    </form>
  </section>

  <div id="open" hidden>
    <div class="grid">
      <section class="card">
        <h2>Screen</h2>
        <label class="switch"><input type="checkbox" id="top-bar"> Show the top bar (Ask, Files, the Wiki…)</label>
        <p class="muted" id="top-bar-note">Whoever signs in on the TV is signed out when they go back to the TV, or after ten minutes untouched.</p>
        <div class="row"><label for="audio">Sound output</label>
          <select id="audio" aria-label="Sound output"></select></div>
        <div class="row"><label for="volume">System volume</label>
          <input type="range" id="volume" min="0" max="100" step="5"><span id="volume-value"></span></div>
        <p class="muted">The sound card's level. Media's own volume (the remote's − and +) is on top of it.</p>
        <div class="row"><label for="display-scale">Display scaling</label>
          <select id="display-scale" aria-label="Display scaling">
            <option value="100">100%</option><option value="150">150%</option>
            <option value="200">200%</option><option value="300">300%</option>
          </select></div>
        <p class="muted" id="display-note">Everything on the TV, these settings too. 200% on a 4K TV is the size of 1080p, but sharper.</p>
        <div class="row"><button class="secondary" type="button" id="screen-size">Screen size…</button>
          <span class="muted">For a TV that cuts off the edges of the picture.</span></div>
        <div class="row"><button class="secondary" type="button" id="text-size">Text size…</button>
          <span class="muted">Bigger or smaller text and buttons on the TV's pages.</span></div>
      </section>
      <section class="card">
        <h2>Kiosk PIN</h2>
        <p class="muted" id="pin-state"></p>
        <form class="row" id="new-pin-form">
          <input type="password" id="new-pin" inputmode="numeric" autocomplete="off" maxlength="12" placeholder="New PIN, 4 to 12 digits" aria-label="New PIN">
          <button type="submit">Save</button>
          <button class="secondary" type="button" id="remove-pin" hidden>Remove the PIN</button>
        </form>
      </section>
    </div>
    <section class="card">
      <h2>Updates</h2>
      <p id="release"></p>
      <ul class="releases" id="releases"></ul>
      <div class="row">
        <button class="secondary" type="button" id="check">Check again</button>
        <button class="secondary" type="button" id="rollback" hidden></button>
        <button class="secondary" type="button" id="system-update">Install system updates now</button>
        <button class="secondary" type="button" id="reboot-now" hidden>Reboot to finish</button>
      </div>
      <p class="muted">System updates also install every night by themselves, unless that was turned off.</p>
      <div id="job" hidden><p id="job-title"></p><pre id="job-log"></pre></div>
    </section>
    <section class="card">
      <h2>Wi-Fi</h2>
      <p id="wifi-now"></p>
      <div class="row"><a class="button secondary" href="/wifi?from=settings">Change network…</a></div>
    </section>
    <section class="card">
      <h2>This box</h2>
      <dl id="details"></dl>
      <div class="row">
        <button class="secondary" type="button" id="restart">Restart the screen</button>
        <button class="secondary" type="button" id="reboot">Reboot the box</button>
      </div>
    </section>
  </div>
</main>
<script>
"use strict";
const $ = function (id) { return document.getElementById(id); };
let state = null, jobTimer = null;

function say(text, bad) { $("msg").className = bad ? "error" : ""; $("msg").textContent = text || ""; }

async function call(method, path, body) {
  const r = await fetch(path, { method: method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {},
                                body: body ? JSON.stringify(body) : undefined }).catch(function () { return null; });
  if (!r) throw new Error("The box didn't answer.");
  const out = await r.json().catch(function () { return {}; });
  if (r.status === 401 && state && state.unlocked) { load(); }
  if (!r.ok) throw new Error(out.detail || "That didn't work.");
  return out;
}

function text(tag, value, cls) { const n = document.createElement(tag); n.textContent = value; if (cls) n.className = cls; return n; }

async function load() {
  try { state = await call("GET", "/api/kiosk/state"); } catch (e) { say(e.message, true); return; }
  $("locked").hidden = state.unlocked;
  $("open").hidden = !state.unlocked;
  $("lock").hidden = !state.unlocked;
  $("pin-form").hidden = state.unlocked || !state.pin_set;
  $("locked-how").textContent = state.pin_set
    ? "Open Kiosk settings from the TV's menu for an admin to approve on their phone, or type the kiosk PIN."
    : "Open Kiosk settings from the TV's menu, and an admin approves on their phone. (This box has no kiosk PIN.)";
  $("address-card").hidden = !state.address_open;
  if (!$("address").value) $("address").value = state.server || "";
  $("address-note").textContent = state.unlocked
    ? "The server this screen shows. Changing it restarts the screen on the new one, where it shows a code to pair it again."
    : "This screen can't reach " + (state.server || "a server") + ". Give the address it should use; the screen restarts on it.";
  if (!state.unlocked) { $("who").textContent = ""; if (location.hash === "#address") $("address").focus(); else if (state.pin_set) $("pin").focus(); return; }
  $("who").textContent = state.via === "phone" ? "Approved by " + state.admin : "Unlocked with the PIN";
  // The screen's own settings on the server: with a phone approval only.
  const scr = state.screen;
  $("top-bar").disabled = !scr;
  $("top-bar").checked = !!(scr && scr.top_bar);
  if (!scr) $("top-bar-note").textContent = "Approve from an admin's phone (Kiosk settings in the TV's menu) to change this.";
  $("volume").value = state.volume; $("volume-value").textContent = state.volume + "%";
  $("pin-state").textContent = state.pin_set ? "Set. The \\u201ccan't connect\\u201d page asks for it before the address can change."
    : "Not set. Without one, anyone at the TV can change the address while the screen can't connect.";
  $("remove-pin").hidden = !state.pin_set;
  const d = state.details, dl = $("details");
  dl.replaceChildren();
  [["Screen", scr ? scr.name : "(approve from a phone to see)"], ["Server", d.server], ["Name", d.hostname],
   ["Addresses", d.addresses.join(", ") || "none"], ["System", d.os],
   ["Wi-Fi setup hotspot", d.wifi_setup ? "on" : "off"], ["Repository", d.source]].forEach(function (p) {
    dl.append(text("dt", p[0]), text("dd", p[1]));
  });
  $("reboot-now").hidden = !state.reboot_needed;
  outputs();
  display();
  $("wifi-now").textContent = d.ssid ? "On " + d.ssid + "." : d.wifi ? "Not on Wi-Fi (a cable, or no network)." : "This box has no Wi-Fi.";
  releases();
  jobs();
}

async function outputs() {
  let a;
  try { a = await call("GET", "/api/kiosk/audio"); } catch (e) { return; }
  const sel = $("audio");
  sel.replaceChildren();
  a.choices.forEach(function (c) {
    const o = text("option", c.label + (c.available ? "" : " (nothing plugged in)"));
    o.value = c.value;
    sel.appendChild(o);
  });
  sel.value = a.current;
}

async function display() {
  let d;
  try { d = await call("GET", "/api/kiosk/display"); } catch (e) { return; }
  $("display-scale").value = String(d.scale);
  $("display-note").textContent = (d.now ? "The TV is at " + d.now + ". " : "") +
    "Everything on the TV, these settings too. 200% on a 4K TV is the size of 1080p, but sharper.";
}

async function releases() {
  $("release").textContent = "Checking for releases…";
  let r;
  try { r = await call("GET", "/api/kiosk/releases"); } catch (e) { $("release").textContent = e.message; return; }
  const list = $("releases");
  list.replaceChildren();
  if (!r.available) { $("release").textContent = r.reason; return; }
  $("release").textContent = "Installed: " + r.current + (r.fetched ? "" : " (couldn't reach the repository just now)") +
    (r.newer.length ? "" : ". Nothing newer.");
  r.newer.forEach(function (n) {
    const li = document.createElement("li"), b = text("button", "Install");
    b.type = "button";
    b.addEventListener("click", function () { job("/api/kiosk/update", { tag: n.tag }, "Updating to " + n.tag + "…"); });
    li.append(text("span", n.tag + " · " + n.date + (n.notes ? " · " + n.notes : "")), b);
    list.appendChild(li);
  });
  $("rollback").hidden = !r.previous;
  $("rollback").textContent = "Roll back to " + r.previous;
}

async function job(path, body, message) {
  try { await call("POST", path, body || {}); say(message); jobs(); } catch (e) { say(e.message, true); }
}

async function jobs() {
  clearTimeout(jobTimer);
  let j;
  try { j = await call("GET", "/api/kiosk/jobs"); } catch (e) { return; }
  const kinds = Object.keys(j), live = kinds.find(function (k) { return j[k].active; });
  const shown = live || kinds.find(function (k) { return j[k].log.length && j[k].ok === false; });
  $("job").hidden = !shown;
  if (shown) {
    const it = j[shown];
    $("job-title").className = it.active ? "" : "error";
    $("job-title").textContent = it.active ? "Now: " + it.name + "…" : "Last time, " + it.name + " didn't finish:";
    $("job-log").textContent = it.log.join("\\n");
  }
  if (live) jobTimer = setTimeout(jobs, 3000);
}

$("pin-form").addEventListener("submit", async function (e) {
  e.preventDefault();
  try { await call("POST", "/api/kiosk/unlock", { pin: $("pin").value }); $("pin").value = ""; say(""); load(); }
  catch (err) { say(err.message, true); $("pin").select(); }
});
$("address-form").addEventListener("submit", async function (e) {
  e.preventDefault();
  say("Checking " + $("address").value + "…");
  try { const r = await call("POST", "/api/kiosk/address", { url: $("address").value });
        say("Changing to " + r.address + ". The screen restarts in a moment."); }
  catch (err) { say(err.message, true); }
});
$("top-bar").addEventListener("change", async function () {
  try { await call("POST", "/api/kiosk/top-bar", { on: $("top-bar").checked });
        say($("top-bar").checked ? "The top bar shows when you go back to the TV." : "The top bar is off."); }
  catch (err) { say(err.message, true); load(); }
});
$("audio").addEventListener("change", async function () {
  try { await call("POST", "/api/kiosk/audio", { output: $("audio").value }); say("Sound now goes to " + $("audio").selectedOptions[0].textContent + "."); }
  catch (err) { say(err.message, true); outputs(); }
});
$("display-scale").addEventListener("change", async function () {
  try { await call("POST", "/api/kiosk/display-scale", { scale: Number($("display-scale").value) });
        say("Display scaling is " + $("display-scale").value + "%."); display(); }
  catch (err) { say(err.message, true); display(); }
});
$("volume").addEventListener("input", function () { $("volume-value").textContent = $("volume").value + "%"; });
$("volume").addEventListener("change", async function () {
  $("volume-value").textContent = $("volume").value + "%";
  try { await call("POST", "/api/kiosk/volume", { level: Number($("volume").value) }); say(""); }
  catch (err) { say(err.message, true); }
});
$("new-pin-form").addEventListener("submit", async function (e) {
  e.preventDefault();
  try { await call("POST", "/api/kiosk/pin", { pin: $("new-pin").value }); $("new-pin").value = ""; say("The PIN is saved."); load(); }
  catch (err) { say(err.message, true); }
});
$("remove-pin").addEventListener("click", async function () {
  try { await call("POST", "/api/kiosk/pin", { pin: null }); say("The PIN is removed."); load(); }
  catch (err) { say(err.message, true); }
});
$("check").addEventListener("click", releases);
// Calibration runs on the TV page itself (media.js, ?calibrate=1), and comes back here when done.
$("screen-size").addEventListener("click", function () { if (state && state.server) location.href = state.server + "/media/screen?calibrate=1"; });
$("text-size").addEventListener("click", function () { if (state && state.server) location.href = state.server + "/media/screen?textsize=1"; });
$("rollback").addEventListener("click", function () { job("/api/kiosk/rollback", {}, "Rolling back…"); });
$("system-update").addEventListener("click", function () { job("/api/kiosk/system-update", {}, "Installing system updates…"); });
$("reboot-now").addEventListener("click", function () { job("/api/kiosk/reboot", {}, "Rebooting…"); });
$("restart").addEventListener("click", function () { job("/api/kiosk/restart", {}, "Restarting the screen…"); });
$("reboot").addEventListener("click", function () { job("/api/kiosk/reboot", {}, "Rebooting…"); });
$("lock").addEventListener("click", async function () { await call("POST", "/api/kiosk/lock", {}).catch(function () {}); load(); });

// From the TV's menu after an admin approved: the one-time grant, in the fragment (never sent anywhere else).
(async function () {
  const m = /^#grant=([^&]+)/.exec(location.hash);
  if (m) {
    history.replaceState(null, "", location.pathname);
    try { await call("POST", "/api/kiosk/unlock", { grant: decodeURIComponent(m[1]) }); }
    catch (err) { say(err.message, true); }
  }
  load();
})();
</script></body></html>
"""


# ------------------------------------------------------------- restart keys
class RestartKeys:
    """Ctrl+Shift+Alt+R on any keyboard restarts the kiosk (Firefox under cage; systemd starts it again).
    The devices are only read, never grabbed: cage gets every key as before."""

    def __init__(self, restart, *, clock=time.monotonic, input_dir: str = INPUT_DIR) -> None:
        self.restart, self.clock, self.input_dir = restart, clock, input_dir
        self.down: set[int] = set()
        self.last = float("-inf")
        self.watching: set[str] = set()
        self.lock = threading.Lock()

    def key(self, code: int, value: int) -> bool:
        """One key event (value 1 pressed, 0 released, 2 held down); True when it restarted the kiosk."""
        with self.lock:
            if value == 0:
                self.down.discard(code)
                return False
            self.down.add(code)
            if code != KEY_R or value != 1 or not all(self.down & set(keys) for keys in RESTART_MODIFIERS):
                return False
            now = self.clock()
            if now - self.last < RESTART_KEYS_WAIT:
                return False
            self.last = now
        self.restart()
        return True

    def read(self, stream) -> None:
        """Key events from one device until it goes away (unplugged)."""
        try:
            while True:
                data = stream.read(INPUT_EVENT.size)
                if not data or len(data) < INPUT_EVENT.size:
                    return
                _, _, kind, code, value = INPUT_EVENT.unpack(data)
                if kind == EV_KEY:
                    self.key(code, value)
        except OSError:
            return

    def scan(self) -> None:
        """Start reading the devices that appeared since (a keyboard plugged in after boot)."""
        try:
            names = sorted(n for n in os.listdir(self.input_dir) if n.startswith("event"))
        except OSError:
            return
        for name in names:
            path = os.path.join(self.input_dir, name)
            with self.lock:
                if path in self.watching:
                    continue
            try:
                stream = open(path, "rb", buffering=0)   # noqa: SIM115 - the thread below closes it
            except OSError:
                continue
            with self.lock:
                self.watching.add(path)
            threading.Thread(target=self._watch, args=(path, stream), daemon=True).start()

    def _watch(self, path: str, stream) -> None:
        try:
            self.read(stream)
        finally:
            stream.close()
            with self.lock:
                self.watching.discard(path)
                self.down.clear()


def restart_kiosk() -> None:
    print("mnemerynne-screen-wifi: Ctrl+Shift+Alt+R: restarting the kiosk", flush=True)
    run_cmd(["systemctl", "restart", "mnemerynne-screen.service"], 30)


# --------------------------------------------------------------------- main
def main() -> None:
    conf = read_conf()
    setup = Setup(NM(), conf.get("MNEMERYNNE_URL", "").rstrip("/"), hotspot=conf.get("WIFI_SETUP", "yes") == "yes")
    local = Server(("127.0.0.1", LOCAL_PORT), setup, local=True, kiosk=Kiosk(setup))
    threading.Thread(target=local.serve_forever, daemon=True).start()
    hotspot_server: list[Server] = []

    def on_hotspot(up: bool) -> None:
        if up and not hotspot_server:
            for _ in range(10):                      # the address appears a moment after the hotspot
                try:
                    srv = Server((HOTSPOT_IP, 80), setup, local=False)
                    break
                except OSError:
                    time.sleep(1)
            else:
                print("mnemerynne-screen-wifi: couldn't listen on the hotspot", file=sys.stderr, flush=True)
                return
            hotspot_server.append(srv)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print(f"mnemerynne-screen-wifi: hotspot {setup.ssid} up", flush=True)
        elif not up and hotspot_server:
            srv = hotspot_server.pop()
            srv.shutdown()
            srv.server_close()
            print("mnemerynne-screen-wifi: hotspot down", flush=True)

    setup.on_hotspot = on_hotspot
    keys = RestartKeys(restart_kiosk)
    while True:
        keys.scan()
        try:
            setup.tick()
        except Exception as exc:   # noqa: BLE001 - keep the loop alive; the journal gets why
            print(f"mnemerynne-screen-wifi: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
