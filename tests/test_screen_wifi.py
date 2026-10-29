"""deploy/screen/wifi-setup.py, the screen box's start page and Wi-Fi setup: reading nmcli, the hotspot
when there's no network (and stepping aside for known networks), joining a network, and who may ask."""

import http.client
import importlib.util
import io
import json
import threading
import time
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "deploy" / "screen" / "wifi-setup.py"
_spec = importlib.util.spec_from_file_location("wifi_setup", PATH)
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)


class FakeNM:
    def __init__(self):
        self.state_value = "disconnected"
        self.device = "wlp2s0"
        self.seen = [{"ssid": "Home", "signal": 80, "secure": True, "security": "WPA2", "in_use": False}]
        self.on_air = 0
        self.result = (True, "")
        self.known = set()
        self.calls = []

    def state(self):
        return self.state_value

    def has_network(self):
        return self.state_value in ("connected", "connected (site only)")

    def wifi_device(self):
        return self.device

    def current_ssid(self):
        return "Home" if self.has_network() else None

    def scan(self, device):
        self.calls.append(("scan", device))
        return list(self.seen)

    def profiles(self):
        return set(self.known)

    def hotspot_up(self, device, ssid, password):
        self.calls.append(("up", ssid, password))
        self.state_value = "connected (local only)"
        return True

    def hotspot_down(self):
        self.calls.append(("down",))
        self.state_value = "disconnected"

    def stations(self, device):
        return self.on_air

    def connect(self, device, ssid, password, hidden):
        self.calls.append(("connect", ssid, password, hidden))
        if self.result[0]:
            self.state_value = "connected"
        return self.result

    def delete(self, name):
        self.calls.append(("delete", name))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def box():
    nm, clock = FakeNM(), Clock()
    setup = ws.Setup(nm, "https://erynne.example.com", clock=clock, sleep=clock.sleep,
                     check_server=lambda: True, suffix="AB12")
    events = []
    setup.on_hotspot = events.append
    return nm, clock, setup, events


def _settle(setup):
    for _ in range(200):
        if not setup.busy:
            return
        time.sleep(0.01)
    raise AssertionError("still busy")


# ------------------------------------------------------------------- nmcli
def test_nmcli_lists_are_read():
    out = ("*:Home:80:WPA2\n"
           ":Home:55:WPA2\n"                    # the same network's weaker access point
           r":Cafe\:Guest:61:--" "\n"           # a colon in a name, and an open network
           ":::\n"                              # a hidden network: no name
           ":Back\\\\Office:30:WPA1 WPA2\n")
    nets = ws.parse_networks(out)
    assert [(n["ssid"], n["signal"], n["secure"], n["in_use"]) for n in nets] == [
        ("Home", 80, True, True), ("Cafe:Guest", 61, False, False), ("Back\\Office", 30, True, False)]


def test_the_join_code_escapes_what_it_must():
    assert ws.wifi_qr('My;Net', 'a"b\\c:d') == 'WIFI:T:WPA;S:My\\;Net;P:a\\"b\\\\c\\:d;;'


def test_errors_are_put_plainly():
    assert ws.friendly("Error: Connection activation failed: Secrets were required, but not provided.") \
        == "The password wasn't accepted."
    assert "can't see" in ws.friendly("Error: No network with SSID 'Nope' found.")


# ------------------------------------------------------------------ hotspot
def test_no_network_for_a_while_brings_up_the_hotspot(box):
    nm, clock, setup, events = box
    setup.tick()
    assert setup.hotspot is None and setup.offline_since == clock.now
    clock.now += ws.OFFLINE_SECONDS - 1
    setup.tick()
    assert setup.hotspot is None
    clock.now += 1
    setup.tick()
    assert setup.hotspot["ssid"] == "Mnemerynne-Setup-AB12" and len(setup.hotspot["password"]) == 10
    assert nm.calls[:2] == [("scan", "wlp2s0"), ("up", "Mnemerynne-Setup-AB12", setup.hotspot["password"])]
    assert events == [True]
    assert setup.status(local=True)["hotspot"]["password"] == setup.hotspot["password"]
    assert "password" not in setup.status(local=False)["hotspot"]        # phones on it know it already
    assert setup.networks_now()[0]["ssid"] == "Home"                    # seen before the hotspot


def test_a_cable_ends_the_hotspot(box):
    nm, clock, setup, events = box
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS
    setup.tick()
    assert setup.hotspot
    nm.state_value = "connected"
    setup.tick()
    assert setup.hotspot is None and events == [True, False] and setup.server_ok


def test_an_empty_hotspot_steps_aside_for_known_networks(box):
    nm, clock, setup, events = box
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS
    setup.tick()
    nm.on_air = 1                                    # someone's on it: it stays
    clock.now += ws.RETRY_SECONDS
    setup.tick()
    assert setup.hotspot and ("down",) not in nm.calls
    nm.on_air = 0
    clock.now += ws.RETRY_SECONDS
    real_sleep = clock.sleep

    def router_back(seconds):                        # the router finished booting meanwhile
        real_sleep(seconds)
        nm.state_value = "connected"
    setup.sleep = router_back
    setup.tick()
    assert setup.hotspot is None and setup.network and events == [True, False]

    # And when nothing comes back, the hotspot does (a new password).
    nm.state_value = "disconnected"
    setup.sleep = clock.sleep
    old = [c for c in nm.calls if c[0] == "up"][-1][2]
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS
    setup.tick()
    clock.now += ws.RETRY_SECONDS
    setup.tick()
    assert setup.hotspot and setup.hotspot["password"] != old and not setup.busy


# --------------------------------------------------------------- joining
def test_a_wrong_password_brings_the_hotspot_back(box):
    nm, clock, setup, events = box
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS
    setup.tick()
    nm.result = (False, "Error: Connection activation failed: Secrets were required, but not provided.")
    setup.connect("Home", "wrongpassword", False)
    _settle(setup)
    assert setup.attempt == {"ssid": "Home", "state": "failed", "error": "The password wasn't accepted."}
    assert ("delete", "Home") in nm.calls and setup.hotspot is not None    # no profile kept; the hotspot again
    assert events == [True, False, True]

    nm.result = (True, "")
    nm.known = {"Home"}
    setup.connect("Home", "rightpassword", False)
    _settle(setup)
    assert setup.attempt["state"] == "connected" and setup.hotspot is None
    assert nm.calls.count(("delete", "Home")) == 1                   # a profile that was there stays


# ------------------------------------------------------------------- web
def _serve(setup, local):
    srv = ws.Server(("127.0.0.1", 0), setup, local=local)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _request(srv, method, path, host, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host)
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else b""
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    r = conn.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def test_the_tv_page_and_who_may_use_it(box):
    nm, clock, setup, events = box
    srv = _serve(setup, local=True)
    own = f"127.0.0.1:{srv.server_address[1]}"
    try:
        status, _, page = _request(srv, "GET", "/", own)
        assert status == 200 and "/media/screen?kiosk=1" in page
        status, headers, body = _request(srv, "GET", "/api/status", own)
        assert status == 200 and json.loads(body)["server"] == "https://erynne.example.com"
        assert "Access-Control-Allow-Origin" not in headers
        assert _request(srv, "GET", "/api/status", "evil.example:9187")[0] == 403        # DNS rebinding
        good = {"Origin": f"http://{own}", "Content-Type": "application/json"}
        assert _request(srv, "POST", "/api/connect", own, {"ssid": "Home", "password": "secret123"},
                        {"Content-Type": "application/json"})[0] == 403                   # no Origin
        assert _request(srv, "POST", "/api/connect", own, {"ssid": "Home", "password": "secret123"},
                        {**good, "Origin": "null"})[0] == 403
        assert _request(srv, "POST", "/api/connect", own, {"ssid": "Home", "password": "short"}, good)[0] == 422
        assert _request(srv, "POST", "/api/connect", own, {"ssid": "x" * 33, "password": ""}, good)[0] == 422
        setup.device = "wlp2s0"
        status, _, body = _request(srv, "POST", "/api/connect", own, {"ssid": "Home", "password": "secret123"}, good)
        assert status == 202, body
        _settle(setup)
        assert ("connect", "Home", "secret123", False) in nm.calls
    finally:
        srv.shutdown()
        srv.server_close()


def test_on_the_hotspot_everything_leads_to_the_setup_page(box):
    nm, clock, setup, events = box
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS
    setup.tick()
    srv = _serve(setup, local=False)
    try:
        status, headers, _ = _request(srv, "GET", "/generate_204", "connectivitycheck.gstatic.com")
        assert status == 302 and headers["Location"] == "http://10.42.0.1/"            # the sign-in sheet
        status, _, page = _request(srv, "GET", "/", "10.42.0.1")
        assert status == 200 and "const LOCAL = false" in page
        assert "password" not in json.loads(_request(srv, "GET", "/api/status", "10.42.0.1")[2])["hotspot"]
        assert _request(srv, "GET", "/qr/join.svg", "10.42.0.1")[0] == 302               # the TV's only
        assert _request(srv, "POST", "/api/connect", "evil.example", {"ssid": "Home"},
                        {"Origin": "http://10.42.0.1", "Content-Type": "application/json"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------ kiosk settings
class FakeRun:
    """systemctl, systemd-run, journalctl, runuser…: recorded, with canned answers; git really runs."""

    def __init__(self):
        self.calls = []
        self.active = set()
        self.journal = {}

    def __call__(self, args, timeout=60):
        if args[0] == "git":
            return ws.run_cmd(args, timeout)
        self.calls.append(args)
        if args[:3] == ["systemctl", "is-active", "--quiet"]:
            return (0 if args[3] in self.active else 3), "", ""
        if args[0] == "journalctl":
            return 0, self.journal.get(args[2], ""), ""
        if args[0] == "hostname":
            return 0, "192.168.1.50 fd00::50\n", ""
        if args[0] == "uname":
            return 0, "6.11.4-301.fc41.x86_64\n", ""
        if args[0] == "rpm":
            return 0, "kernel-core-6.11.5-300.fc41.x86_64   Mon 27 Oct 2026\nkernel-core-6.11.4-301.fc41.x86_64   Fri\n", ""
        return 0, "", ""


class FakeServer:
    """The Mnemerynne server's kiosk routes (web/kiosk.py) as the helper sees them."""

    def __init__(self):
        self.calls = []
        self.top_bar = False
        self.healthy = True

    def __call__(self, method, url, body=None, headers=None, timeout=10):
        self.calls.append((method, url.split("://", 1)[1].split("/", 1)[1], body, headers))
        if url.endswith("/healthz"):
            return (200, {"status": "ok", "schema": 46}) if self.healthy else (0, {"detail": "refused"})
        if url.endswith("/api/media/kiosk/redeem"):
            if body["grant"] != "good-grant":
                return 403, {"detail": "That approval has run out or was used already."}
            return 200, {"session": "srv-session", "admin": "boss", "expires_in": 600,
                         "screen": {"id": 3, "name": "Lobby", "top_bar": self.top_bar}}
        if url.endswith("/api/media/kiosk/screen"):
            assert headers == {"X-Kiosk-Session": "srv-session"}
            if method == "PUT":
                self.top_bar = body["top_bar"]
            return 200, {"screen": {"id": 3, "name": "Lobby", "top_bar": self.top_bar}}
        return 204, {}


@pytest.fixture
def kiosk(tmp_path, box):
    nm, clock, setup, events = box
    conf = tmp_path / "screen.conf"
    conf.write_text("MNEMERYNNE_URL=https://erynne.example.com\nSYSTEM_VOLUME=80\n"
                    f"SOURCE_DIR={(tmp_path / 'opt').as_posix()}\n")
    run, server = FakeRun(), FakeServer()
    k = ws.Kiosk(setup, conf=str(conf), pins=ws.Pins(str(tmp_path / "pin"), clock=clock), run=run, http=server,
                 clock=clock, state_dir=str(tmp_path / "state"), uid=lambda: 990)
    return k, run, server, clock, setup, conf


def _started(run):
    return [c for c in run.calls if c[0] == "systemd-run"][-1]


def test_the_pin_is_optional_hashed_and_locks_after_wrong_tries(kiosk, tmp_path):
    k, run, server, clock, setup, conf = kiosk
    with pytest.raises(ws.KioskError) as exc:
        k.unlock_pin("1234")                                   # a new box has none: only a phone approval
    assert exc.value.status == 403
    with pytest.raises(ws.KioskError):
        k.pins.set("12a4")
    k.pins.set("482913")
    assert "482913" not in (tmp_path / "pin").read_text()
    for _ in range(ws.PIN_TRIES):
        with pytest.raises(ws.KioskError):
            k.unlock_pin("000000")
    with pytest.raises(ws.KioskError) as exc:
        k.unlock_pin("482913")                                 # locked, even the right one
    assert exc.value.status == 429
    clock.now += ws.PIN_LOCK_SECONDS
    token = k.unlock_pin("482913")
    assert k.state(token)["unlocked"] and k.state(token)["via"] == "pin" and k.state(None)["unlocked"] is False
    clock.now += ws.UNLOCK_SECONDS
    assert k.session(token) is None                            # left alone: locked again


def test_the_address_changes_through_the_installer_after_a_check(kiosk):
    k, run, server, clock, setup, conf = kiosk
    setup.server_ok = False
    # No PIN and the server out of reach: the "can't connect" page may change it without unlocking.
    assert k.address_open(None)
    for bad in ("erynne.example.com", "https://x/files", "javascript:alert(1)"):
        with pytest.raises(ws.KioskError):
            k.set_address(None, bad)
    server.healthy = False
    with pytest.raises(ws.KioskError) as exc:
        k.set_address(None, "https://new.example.com")
    assert "isn't answering like a Mnemerynne server" in str(exc.value)
    server.healthy = True
    assert k.set_address(None, "https://new.example.com/media/screen") == "https://new.example.com"
    started = _started(run)
    assert "--unit=mnemerynne-screen-config" in started
    assert started[-1].startswith("bash " + k.source() + "/deploy/screen/install.sh https://new.example.com --reconfigure;")

    k.pins.set("4321")                                         # with a PIN it must be unlocked first
    assert not k.address_open(None)
    with pytest.raises(ws.KioskError) as exc:
        k.set_address(None, "https://new.example.com")
    assert exc.value.status == 401
    setup.server_ok = True
    k.pins.clear()
    assert not k.address_open(None)                            # connected: only once unlocked

    run.active.add("mnemerynne-screen-update")                 # one job at a time
    token = k.unlock_grant("good-grant")
    with pytest.raises(ws.KioskError) as exc:
        k.set_address(token, "https://new.example.com")
    assert "busy" in str(exc.value)


def test_a_phone_approval_opens_the_top_bar_and_the_volume(kiosk):
    k, run, server, clock, setup, conf = kiosk
    with pytest.raises(ws.KioskError):
        k.unlock_grant("used-grant")
    token = k.unlock_grant("good-grant")
    st = k.state(token)
    assert st["via"] == "phone" and st["admin"] == "boss" and st["screen"]["name"] == "Lobby" and st["volume"] == 80
    assert st["details"]["addresses"] == ["192.168.1.50", "fd00::50"] and st["reboot_needed"] is True
    assert k.set_top_bar(token, True)["top_bar"] is True and server.top_bar is True

    assert k.set_volume(token, 35) == 35
    assert "SYSTEM_VOLUME=35\n" in conf.read_text() and "MNEMERYNNE_URL=https://erynne.example.com\n" in conf.read_text()
    assert run.calls[-1] == ["runuser", "-u", "mnemerynne-screen", "--", "env", "XDG_RUNTIME_DIR=/run/user/990",
                             "pactl", "set-sink-volume", "@DEFAULT_SINK@", "35%"]
    for bad in (101, -1, "50", True):
        with pytest.raises(ws.KioskError):
            k.set_volume(token, bad)

    k.pins.set("4321")
    pin_token = k.unlock_pin("4321")
    with pytest.raises(ws.KioskError) as exc:
        k.set_top_bar(pin_token, False)                        # kept on the server: a phone approval only
    assert exc.value.status == 403
    k.lock_up(token)
    assert ("POST", "api/media/kiosk/lock", None, {"X-Kiosk-Session": "srv-session"}) in server.calls
    with pytest.raises(ws.KioskError):
        k.set_volume(token, 50)


def _git(path, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True)


def test_releases_newer_than_the_installed_one_update_and_roll_back(kiosk, tmp_path):
    k, run, server, clock, setup, conf = kiosk
    assert k.releases()["available"] is False                  # no copy of the repository yet
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "--quiet")
    _git(upstream, "config", "user.email", "t@example.com")
    _git(upstream, "config", "user.name", "t")
    for n, names in enumerate([["screen-v0.1.0"], ["screen-v0.2.0", "files-v1.0.0"], ["screen-v0.3.0"]]):
        (upstream / "f.txt").write_text(str(n))
        _git(upstream, "add", "-A")
        _git(upstream, "commit", "--quiet", "-m", f"c{n}")
        for name in names:
            _git(upstream, "tag", "-a", name, "-m", f"{name}: release {n}")
    _git(tmp_path, "clone", "--quiet", str(upstream), str(tmp_path / "opt"))
    _git(tmp_path / "opt", "-c", "advice.detachedHead=false", "checkout", "--quiet", "screen-v0.2.0")

    r = k.releases()
    assert r["current"] == "screen-v0.2.0" and r["fetched"] and r["previous"] is None
    assert [(n["tag"], n["notes"]) for n in r["newer"]] == [("screen-v0.3.0", "screen-v0.3.0: release 2")]

    token = k.unlock_grant("good-grant")
    for tag in ("screen-v0.1.0", "files-v1.0.0", "screen-v9.0.0"):
        with pytest.raises(ws.KioskError):
            k.update(token, tag)
    k.update(token, "screen-v0.3.0")
    started = _started(run)
    assert "--unit=mnemerynne-screen-update" in started and "install.sh --update screen-v0.3.0;" in started[-1]

    with pytest.raises(ws.KioskError):
        k.rollback(token)                                      # nothing recorded yet
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "previous").write_text("screen-v0.1.0\n")
    k.rollback(token)
    assert "install.sh --rollback;" in _started(run)[-1]

    run.journal["mnemerynne-screen-osupdate"] = "Upgrading 3 packages\nError: no space\n== finished: 1\n"
    jobs = k.jobs()
    assert jobs["system"]["ok"] is False and jobs["system"]["log"][-1] == "== finished: 1" and jobs["update"]["ok"] is None


def test_kiosk_settings_answer_on_the_box_only(kiosk):
    k, run, server, clock, setup, conf = kiosk
    srv = ws.Server(("127.0.0.1", 0), setup, local=True, kiosk=k)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    hot = _serve(setup, local=False)
    own = f"127.0.0.1:{srv.server_address[1]}"
    good = {"Origin": f"http://{own}", "Content-Type": "application/json"}
    try:
        status, _, page = _request(srv, "GET", "/settings", own)
        assert status == 200 and "Kiosk settings" in page
        assert json.loads(_request(srv, "GET", "/api/kiosk/state", own)[2])["unlocked"] is False
        assert _request(srv, "GET", "/api/kiosk/releases", own)[0] == 401
        assert _request(srv, "POST", "/api/kiosk/unlock", own, {"grant": "good-grant"},
                        {"Content-Type": "application/json"})[0] == 403          # not from the page
        status, headers, body = _request(srv, "POST", "/api/kiosk/unlock", own, {"grant": "good-grant"}, good)
        assert status == 200 and json.loads(body)["via"] == "phone"
        cookie = headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        session = {"Cookie": cookie.split(";", 1)[0]}
        assert json.loads(_request(srv, "GET", "/api/kiosk/state", own, headers=session)[2])["unlocked"] is True
        status, _, body = _request(srv, "POST", "/api/kiosk/volume", own, {"level": 40}, {**good, **session})
        assert status == 200 and json.loads(body) == {"volume": 40}
        assert _request(srv, "POST", "/api/kiosk/volume", own, {"level": 40}, good)[0] == 401   # without the cookie

        # The hotspot's listener (phones) has none of it.
        assert _request(hot, "GET", "/settings", "10.42.0.1")[0] == 302
        assert _request(hot, "POST", "/api/kiosk/unlock", "10.42.0.1", {"grant": "good-grant"},
                        {"Origin": "http://10.42.0.1", "Content-Type": "application/json"})[0] == 404
    finally:
        for s in (srv, hot):
            s.shutdown()
            s.server_close()


def test_settings_file_lines_change_one_at_a_time(tmp_path):
    path = tmp_path / "c.conf"
    path.write_text("# note\nA=1\nB=2\n")
    ws.set_conf_value(str(path), "B", "3")
    ws.set_conf_value(str(path), "C", "4")
    assert path.read_text() == "# note\nA=1\nB=3\nC=4\n"


def test_no_wifi_setup_means_no_hotspot(box):
    nm, clock, setup, events = box
    setup.hotspot_enabled = False
    setup.tick()
    clock.now += ws.OFFLINE_SECONDS * 3
    setup.tick()
    assert setup.hotspot is None and events == []


def test_wifi_on_the_box_is_part_of_kiosk_settings(kiosk):
    k, run, server, clock, setup, conf = kiosk
    srv = ws.Server(("127.0.0.1", 0), setup, local=True, kiosk=k)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    own = f"127.0.0.1:{srv.server_address[1]}"
    good = {"Origin": f"http://{own}", "Content-Type": "application/json"}
    join = {"ssid": "Home", "password": "secret123"}
    try:
        # No network (a new box, the start page's keyboard setup): open to anyone at the TV.
        setup.network = False
        assert _request(srv, "GET", "/wifi", own)[0] == 200
        assert _request(srv, "GET", "/api/networks", own)[0] == 200
        # Online: behind the settings' unlock.
        setup.network = True
        status, headers, _ = _request(srv, "GET", "/wifi", own)
        assert status == 302 and headers["Location"] == "/settings"
        assert _request(srv, "GET", "/api/networks", own)[0] == 401
        assert _request(srv, "POST", "/api/connect", own, join, good)[0] == 401
        cookie = _request(srv, "POST", "/api/kiosk/unlock", own, {"grant": "good-grant"}, good)[1]["Set-Cookie"]
        session = {"Cookie": cookie.split(";", 1)[0]}
        assert _request(srv, "GET", "/wifi", own, headers=session)[0] == 200
        assert _request(srv, "GET", "/api/networks", own, headers=session)[0] == 200
        settings = _request(srv, "GET", "/settings", own)[2]
        assert 'href="/wifi?from=settings"' in settings
    finally:
        srv.shutdown()
        srv.server_close()


def test_hidden_parts_of_the_pages_stay_hidden():
    # The Wi-Fi page's form is display: grid; without this its Cancel button did nothing.
    for page in (ws.START_PAGE, ws.WIFI_PAGE, ws.SETTINGS_PAGE):
        assert "[hidden] { display: none !important; }" in page
        assert "@media (min-width: 3000px) { html { font-size: 36px; } }" in page   # readable on a 4K TV


def test_kiosk_settings_open_the_tvs_text_size():
    assert 'id="text-size">Text size…</button>' in ws.SETTINGS_PAGE
    assert 'state.server + "/media/screen?textsize=1"' in ws.SETTINGS_PAGE


CARDS = """Card #42
\tName: alsa_card.pci-0000_00_1b.0
\tDriver: alsa
\tProperties:
\t\tdevice.description = "Built-in Audio"
\t\tdevice.bus = "pci"
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)
\t\toutput:hdmi-stereo: Digital Stereo (HDMI) Output (sinks: 1, sources: 0, priority: 5900, available: yes)
\t\toutput:hdmi-stereo+input:analog-stereo: Digital Stereo (HDMI) Output + Analog Stereo Input (sinks: 1, sources: 1, priority: 5965, available: yes)
\t\tinput:analog-stereo: Analog Stereo Input (sinks: 0, sources: 1, priority: 65, available: yes)
\tActive Profile: output:hdmi-stereo
\tPorts:
\t\tanalog-output-headphones: Headphones (type: Headphones, priority: 9900, available)
\t\t\tProperties:
\t\t\t\tport.type = "headphones"
Card #43
\tName: alsa_card.usb-Generic_USB_Audio-00
\tProperties:
\t\tdevice.description = "USB Speakers"
\tProfiles:
\t\toutput:analog-stereo+input:mono-fallback: Analog Stereo Output + Mono Input (sinks: 1, sources: 1, priority: 6501, available: no)
\tActive Profile: off
"""


def test_the_sound_outputs_come_from_the_cards(kiosk):
    k, run, server, clock, setup, conf = kiosk
    assert [(c["device"], c["profile"], c["available"]) for c in ws.parse_cards(CARDS)] == [
        ("Built-in Audio", "output:analog-stereo", True),
        ("Built-in Audio", "output:hdmi-stereo", True),               # not again with the microphone
        ("USB Speakers", "output:analog-stereo+input:mono-fallback", False)]   # its only one

    real = run.__call__

    def with_cards(args, timeout=60):
        if args[-3:] == ["pactl", "list", "cards"]:
            run.calls.append(args)
            return 0, CARDS, ""
        return real(args, timeout)
    k.run = with_cards
    token = k.unlock_grant("good-grant")
    a = k.audio(token)
    assert a["current"] == "hdmi"
    assert [c["value"] for c in a["choices"]][:3] == ["hdmi", "auto", "card:alsa_card.pci-0000_00_1b.0:output:analog-stereo"]

    assert k.set_audio(token, "card:alsa_card.pci-0000_00_1b.0:output:analog-stereo").endswith("analog-stereo")
    assert "AUDIO_OUTPUT=card:alsa_card.pci-0000_00_1b.0:output:analog-stereo\n" in conf.read_text()
    assert run.calls[-1][-2:] == ["/usr/local/bin/mnemerynne-screen-audio", "--once"]
    assert run.calls[-1][:3] == ["runuser", "-u", "mnemerynne-screen"]
    # The settings file is sourced by bash: only the listed values, nothing that would run.
    for bad in ("card:alsa_card.pci-0000_00_1b.0:output:analog-stereo;reboot", "card:other:output:x", "$(id)", ""):
        with pytest.raises(ws.KioskError):
            k.set_audio(token, bad)
    with pytest.raises(ws.KioskError):
        k.audio(None)                                          # locked


# ------------------------------------------------------------- restart keys
CTRL, SHIFT, ALT, RIGHT_ALT = 29, 42, 56, 100


def _keys(clock=None):
    restarts = []
    keys = ws.RestartKeys(lambda: restarts.append(1), clock=clock or (lambda: 1000.0))
    return keys, restarts


def _press(keys, *codes):
    for code in codes:
        keys.key(code, 1)
    for code in reversed(codes):
        keys.key(code, 0)


def test_ctrl_shift_alt_r_restarts_the_kiosk():
    now = [1000.0]
    keys, restarts = _keys(lambda: now[0])
    _press(keys, CTRL, SHIFT, ws.KEY_R)                    # no Alt
    _press(keys, CTRL, ALT, ws.KEY_R)                      # no Shift
    _press(keys, ws.KEY_R)
    assert restarts == []
    _press(keys, CTRL, SHIFT, RIGHT_ALT, ws.KEY_R)         # either Alt
    assert restarts == [1]
    _press(keys, CTRL, SHIFT, ALT, ws.KEY_R)               # again at once: one restart is enough
    assert restarts == [1]
    now[0] += ws.RESTART_KEYS_WAIT
    keys.key(CTRL, 1), keys.key(SHIFT, 1), keys.key(ALT, 1)
    keys.key(ws.KEY_R, 2)                                  # held down, not pressed: nothing
    assert restarts == [1]
    keys.key(ws.KEY_R, 1)
    assert restarts == [1, 1]


def test_restart_keys_are_read_from_every_input_device(tmp_path):
    def events(*keys):
        return b"".join(ws.INPUT_EVENT.pack(0, 0, ws.EV_KEY, code, value) for code, value in keys) + \
            ws.INPUT_EVENT.pack(0, 0, 0, 0, 0)            # a sync event: not a key
    (tmp_path / "event0").write_bytes(events((CTRL, 1), (SHIFT, 1), (ALT, 1), (ws.KEY_R, 1)))
    (tmp_path / "event1").write_bytes(events((ws.KEY_R, 1)))
    (tmp_path / "mice").write_bytes(b"not an event device")
    restarts = []
    keys = ws.RestartKeys(lambda: restarts.append(1), clock=lambda: 1000.0, input_dir=str(tmp_path))
    keys.scan()
    for _ in range(100):                                   # each device is read in its own thread, to its end
        if restarts and not keys.watching:
            break
        time.sleep(0.02)
    assert restarts == [1] and keys.watching == set()
    keys.read(io.BytesIO(b"short"))                        # a device that went away mid-event
    ws.RestartKeys(lambda: None, input_dir=str(tmp_path / "missing")).scan()


def test_the_kiosk_opens_new_windows_in_place_and_backspace_goes_back():
    install = (PATH.parent / "install.sh").read_text(encoding="utf-8")
    for pref in ('"browser.backspace_action", 0', '"browser.link.open_newwindow", 1',
                 '"browser.link.open_newwindow.restriction", 0'):
        assert f"user_pref({pref});" in install
