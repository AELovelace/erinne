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
"""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
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
                 suffix: str | None = None) -> None:
        self.nm, self.server, self.clock, self.sleep = nm, server, clock, sleep
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
                if self.device and now - self.offline_since >= OFFLINE_SECONDS:
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
        if path == "/" and local:
            self._send(200, START_PAGE, "text/html")
        elif path == "/wifi" or (path == "/" and not local):
            self._send(200, WIFI_PAGE.replace("{{LOCAL}}", "true" if local else "false"), "text/html")
        elif path == "/api/status":
            self._json(200, self.box.status(local))
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
        if urlsplit(self.path).path != "/api/connect":
            self._json(404, {"detail": "Not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
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


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, setup: Setup, local: bool) -> None:
        self.box, self.local = setup, local
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
main { max-width: 64rem; margin: 0 auto; padding: 4vh 1.2rem; }
h1 { font-size: 2.2rem; margin: 0 0 .6rem; color: #ff5cad; }
.muted { color: #b597c4; }
button, .button { font: inherit; padding: .55rem 1.1rem; border: 0; border-radius: .5rem; background: #ff2e97; color: #12031f;
                  cursor: pointer; text-decoration: none; display: inline-block; }
button.secondary, .button.secondary { background: #3b1450; color: #f6d6ea; }
button:focus-visible, .button:focus-visible, input:focus-visible { outline: 3px solid #4de8ff; outline-offset: 2px; }
input { font: inherit; padding: .5rem .7rem; border-radius: .5rem; border: 1px solid #6b3a85; background: #1f0a2e; color: inherit; width: 100%; box-sizing: border-box; }
.error { color: #ff8a8a; }
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
    <p><a class="button secondary" href="/wifi" id="wifi-link" hidden>Set up Wi-Fi with a keyboard</a></p>
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
let shownFor = null;
async function poll() {
  let s;
  try { s = await (await fetch("/api/status", { cache: "no-store" })).json(); }
  catch (e) { setTimeout(poll, 3000); return; }
  const host = (s.server || "").replace(/^https?:\\/\\//, "");
  if (s.server_ok) { location.replace(s.server + "/media/screen?kiosk=1"); return; }
  const wait = document.getElementById("wait"), setup = document.getElementById("setup");
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
    if (a && a.state === "connected") { $("msg").textContent = "Joined " + a.ssid + "."; if (LOCAL) setTimeout(function () { location.href = "/"; }, 1500); return; }
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
$("cancel").addEventListener("click", function () { $("form").hidden = true; });
$("refresh").addEventListener("click", load);
load();
</script></body></html>
"""


# --------------------------------------------------------------------- main
def main() -> None:
    conf = read_conf()
    setup = Setup(NM(), conf.get("MNEMERYNNE_URL", "").rstrip("/"))
    local = Server(("127.0.0.1", LOCAL_PORT), setup, local=True)
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
    while True:
        try:
            setup.tick()
        except Exception as exc:   # noqa: BLE001 - keep the loop alive; the journal gets why
            print(f"mnemerynne-screen-wifi: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
