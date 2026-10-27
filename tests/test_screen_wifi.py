"""deploy/screen/wifi-setup.py, the screen box's start page and Wi-Fi setup: reading nmcli, the hotspot
when there's no network (and stepping aside for known networks), joining a network, and who may ask."""

import http.client
import importlib.util
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
