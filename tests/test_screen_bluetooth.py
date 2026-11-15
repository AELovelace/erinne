"""deploy/screen/wifi-setup.py's 🎮 Controllers: Bluetooth game controllers on a screen box, through a pretend
bluetoothctl. Only game controllers are listed or paired (never a keyboard, mouse or phone); the TV page needs a
pass the server gave for a remote's OK, redeemed with the server; only the Mnemerynne address may ask, across
origins, and never on the hotspot."""

import json
import threading

import pytest

from test_screen_wifi import _request, box, ws  # noqa: F401  (box is a fixture)

SERVER = "https://erynne.example.com"
XBOX, SONY, KEYBOARD, PHONE, MYSTERY = ("AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:03",
                                        "AA:BB:CC:00:00:04", "AA:BB:CC:00:00:05")


class FakeBluetoothctl:
    """bluetoothctl's one-shot commands over a little world of devices."""

    def __init__(self, adapter=True):
        self.adapter = adapter
        self.calls = []
        self.pair_ok = True
        self.pair_silent = False     # bluetoothctl's --timeout ran out: exit 0, nothing printed, nothing paired
        self.pair_connects = False   # a Bluetooth LE pad comes up connected from the pairing itself
        self.devices = {
            XBOX: {"Name": "Xbox Wireless Controller", "Icon": "input-gaming", "Paired": "yes", "Connected": "yes",
                   "Battery Percentage": "0x5a (90)"},
            KEYBOARD: {"Name": "Logitech K380", "Icon": "input-keyboard", "Paired": "no", "Connected": "no"},
            PHONE: {"Name": "Wireless Controller Phone", "Icon": "phone", "Paired": "no", "Connected": "no"},
        }
        self.nearby = {   # what a scan finds
            SONY: {"Name": "DualSense Wireless Controller", "Icon": "input-gaming", "Paired": "no", "Connected": "no"},
            MYSTERY: {"Name": "8BitDo Pro 2", "Paired": "no", "Connected": "no"},   # says nothing about itself
        }

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        assert args[0] == "bluetoothctl"
        rest = [a for a in args[1:]]
        while rest and rest[0] in ("--timeout", "--agent"):
            rest = rest[2:]
        cmd = rest[0]
        if cmd == "show":
            return (0, "Controller 11:22:33:44:55:66 (public)\n\tPowered: yes\n", "") if self.adapter else (1, "", "No default controller available")
        if cmd == "power":
            return 0, "Changing power on succeeded\n", ""
        if cmd == "devices":
            return 0, "".join(f"Device {a} {d['Name']}\n" for a, d in self.devices.items()), ""
        if cmd == "info":
            d = self.devices.get(rest[1])
            if d is None:
                return 1, "", f"Device {rest[1]} not available"
            body = "".join(f"\t{k}: {v}\n" for k, v in d.items())
            return 0, f"Device {rest[1]} (public)\n\tAlias: {d['Name']}\n{body}\tUUID: Human Interface Device...\n", ""
        if cmd == "scan":
            self.devices.update(self.nearby)
            for address in self.nearby:
                self.devices[address]["RSSI"] = "-52"      # heard advertising in this scan
            return 0, "Discovery started\n", ""
        if cmd == "pair":
            if not self.pair_ok:
                return 1, "", "Failed to pair: org.bluez.Error.AuthenticationFailed"
            if self.pair_silent:
                return 0, "", ""
            self.devices[rest[1]]["Paired"] = "yes"
            if self.pair_connects:
                self.devices[rest[1]]["Connected"] = "yes"
            return 0, "Pairing successful\n", ""
        if cmd == "trust":
            return 0, "trust succeeded\n", ""
        if cmd == "connect":
            self.devices[rest[1]]["Connected"] = "yes"
            return 0, "Connection successful\n", ""
        if cmd == "remove":
            self.devices.pop(rest[1], None)
            return 0, "Device has been removed\n", ""
        raise AssertionError(args)


class FakeServer:
    """The server's /api/media/kiosk/controllers/redeem: one good pass, once."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body=None, headers=None, timeout=10):
        self.calls.append((method, url, body))
        if url == SERVER + "/api/media/kiosk/controllers/redeem" and body == {"pass": "good-pass"}:
            return 200, {"screen": "Lobby", "by": "jane"}
        return 403, {"detail": "That has run out or was used already. Press OK on your remote again."}


@pytest.fixture
def pads(box):
    nm, clock, setup, events = box
    bt, server = FakeBluetoothctl(), FakeServer()
    c = ws.Controllers(setup, ws.Bluetooth(run=bt, spawn=lambda fn: fn(), sleep=lambda s: None), http=server, clock=clock,
                       thread=lambda fn: fn())   # threads run inline, and nothing really waits
    return c, bt, server, clock, setup


def test_only_game_controllers_are_listed(pads):
    c, bt, *_ = pads
    assert [d["id"] for d in c.devices()] == [XBOX]                           # the keyboard and the phone never show
    xbox = c.devices()[0]
    assert xbox == {"id": XBOX, "name": "Xbox Wireless Controller", "kind": "xbox", "paired": True, "connected": True,
                    "battery": 90, "busy": None, "error": None}
    bt.devices.update(bt.nearby)
    listed = {d["id"]: d for d in c.devices()}
    assert set(listed) == {XBOX, SONY, MYSTERY}                               # by its icon, or by its name if it gives none
    assert listed[SONY]["kind"] == "playstation" and listed[MYSTERY]["kind"] == "other"
    assert [d["id"] for d in c.devices()][0] == XBOX                          # paired and connected first


def test_is_gamepad_never_takes_a_keyboard_or_phone_by_name():
    assert ws.is_gamepad({"Icon": "input-gaming", "Name": "Anything"})
    assert ws.is_gamepad({"Name": "Pro Controller"})
    assert not ws.is_gamepad({"Icon": "input-keyboard", "Name": "Xbox keyboard"})
    assert not ws.is_gamepad({"Icon": "phone", "Name": "Wireless Controller"})
    assert not ws.is_gamepad({"Icon": "audio-headset", "Name": "Xbox headset"})
    assert not ws.is_gamepad({"Name": "Living room speaker"})
    assert ws.bt_battery("0x64 (100)") == 100 and ws.bt_battery(None) is None


def test_opening_needs_a_pass_the_server_accepts(pads):
    c, bt, server, clock, setup = pads
    with pytest.raises(ws.KioskError) as refused:
        c.open("stale-pass")
    assert refused.value.status == 403 and "remote" in str(refused.value)
    token = c.open("good-pass")
    assert server.calls[-1] == ("POST", SERVER + "/api/media/kiosk/controllers/redeem", {"pass": "good-pass"})
    assert ["bluetoothctl", "power", "on"] in bt.calls                         # the radio is switched on
    c.need(token)
    clock.sleep(ws.CONTROLLERS_SECONDS + 1)
    with pytest.raises(ws.KioskError) as closed:
        c.need(token)
    assert closed.value.status == 401


def test_find_pair_and_forget(pads):
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    with pytest.raises(ws.KioskError):
        c.scan("not-a-token")
    c.scan(token)
    assert any("scan" in call for call in bt.calls) and c.scanning is False
    c.pair(token, SONY.lower())                                               # any case
    assert bt.devices[SONY]["Paired"] == "yes" and bt.devices[SONY]["Connected"] == "yes"
    assert ["bluetoothctl", "trust", SONY] in bt.calls
    pair_call = next(call for call in bt.calls if "pair" in call)
    assert pair_call[1:3] == ["--agent", "NoInputNoOutput"]                   # no PIN: Just Works
    c.forget(token, SONY)
    assert SONY not in bt.devices


def test_only_controllers_can_be_paired_and_a_failure_says_how(pads):
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    for address, status in ((KEYBOARD, 403), (PHONE, 403), ("AA:BB:CC:00:00:99", 404), ("not-an-address", 422)):
        with pytest.raises(ws.KioskError) as refused:
            c.pair(token, address)
        assert refused.value.status == status, address
    with pytest.raises(ws.KioskError):
        c.forget(token, KEYBOARD)                                              # nor forgotten from here
    bt.devices.update(bt.nearby)
    bt.pair_ok = False
    c.pair(token, SONY)
    sony = next(d for d in c.devices() if d["id"] == SONY)
    assert sony["paired"] is False and "pairing button" in sony["error"]


def test_a_pairing_that_times_out_says_so_and_is_tried_again(pads):
    """bluetoothctl's --timeout ends a pair that never happened with exit 0 and nothing printed (a Bluetooth LE
    Xbox pad that wasn't being heard): that's a failure, not a pairing, and it's tried twice."""
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    bt.devices.update(bt.nearby)
    bt.pair_silent = True
    c.pair(token, SONY)
    sony = next(d for d in c.devices() if d["id"] == SONY)
    assert sony["paired"] is False and "heard, but didn't answer" in sony["error"]   # it was heard: not the light
    assert sum(1 for call in bt.calls if "pair" in call) == ws.BT_PAIR_TRIES
    assert not any("connect" in call for call in bt.calls)                    # never got as far as connecting


def test_pairing_scans_alongside_like_doll_os(pads):
    """As Doll-OS's DS-Slave pairs an LE pad: while scanning and the pad is heard advertising, then straight in.
    One that comes up connected from the pairing isn't connected again."""
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    bt.devices.update({SONY: dict(bt.nearby[SONY])})                         # listed by an earlier Find, not heard now
    bt.pair_connects = True
    c.pair(token, SONY)
    names = [next(a for a in call[1:] if not a.startswith("-") and not a.isdigit() and a != "NoInputNoOutput")
             for call in bt.calls]
    assert names.index("scan") < names.index("pair")                          # the scan runs first and alongside
    assert bt.devices[SONY]["Paired"] == "yes" and bt.devices[SONY]["Connected"] == "yes"
    assert not any("connect" in call for call in bt.calls)
    assert next(d for d in c.devices() if d["id"] == SONY)["error"] is None


def test_each_find_starts_afresh(pads):
    """BlueZ keeps every device a scan turned up, so the list grew each round: unpaired controllers from an
    earlier Find (and their errors) go when Find is pressed again; paired ones stay."""
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    stale = "AA:BB:CC:00:00:77"
    bt.devices[stale] = {"Name": "Xbox Wireless Controller", "Icon": "input-gaming", "Paired": "no", "Connected": "no"}
    c.errors[stale] = "It didn't pair."
    c.scan(token)
    listed = [d["id"] for d in c.devices()]
    assert stale not in listed and stale not in c.errors                     # last round's leftover is gone
    assert XBOX in listed and SONY in listed                                  # the paired one, and this round's find
    assert ["bluetoothctl", "remove", stale] in bt.calls
    assert ["bluetoothctl", "remove", KEYBOARD] not in bt.calls               # only game controllers are touched


def test_a_pad_never_heard_says_so(pads):
    c, bt, server, clock, setup = pads
    token = c.open("good-pass")
    quiet = "AA:BB:CC:00:00:66"                                               # listed once, not advertising now
    bt.devices[quiet] = {"Name": "Xbox Wireless Controller", "Icon": "input-gaming", "Paired": "no", "Connected": "no"}
    bt.pair_silent = True
    c.pair(token, quiet)
    assert "never heard it" in c.errors[quiet]


def test_the_tv_says_what_went_wrong():
    """pair_problem: the step in plain words, and BlueZ's own reason to look up; never just "the light"."""
    refused = ws.pair_problem("pair: Attempting to pair with AA\nFailed to pair: org.bluez.Error.AuthenticationCanceled")
    assert refused.startswith("It refused the pairing") and refused.endswith("(BlueZ: AuthenticationCanceled)")
    assert "never heard" in ws.pair_problem("not heard in 15 s | pair: no answer in 20 s | pair: no answer in 20 s")
    assert ws.pair_problem("not heard in 15 s | pair: Failed to pair: org.bluez.Error.ConnectionAttemptFailed") == \
        "It didn't pair. (BlueZ: ConnectionAttemptFailed)"
    assert "didn't connect" in ws.pair_problem("paired; connect: Failed to connect: org.bluez.Error.Failed le-connection-abort-by-local")
    assert "can't see it" in ws.pair_problem("pair: Device AA:BB:CC:00:00:66 not available")


def test_no_adapter(box):
    nm, clock, setup, events = box
    c = ws.Controllers(setup, ws.Bluetooth(run=FakeBluetoothctl(adapter=False)), http=FakeServer(), clock=clock,
                       thread=lambda fn: fn())
    assert c.state() == {"available": False, "scanning": False, "devices": []}
    token = c.open("good-pass")
    with pytest.raises(ws.KioskError) as refused:
        c.scan(token)
    assert refused.value.status == 409 and "USB Bluetooth" in str(refused.value)


def test_the_tv_page_asks_across_origins_and_nobody_else(pads, box):
    c, bt, server, clock, setup = pads
    srv = ws.Server(("127.0.0.1", 0), setup, local=True, controllers=c)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    hot = ws.Server(("127.0.0.1", 0), setup, local=False, controllers=c)
    threading.Thread(target=hot.serve_forever, daemon=True).start()
    own = f"127.0.0.1:{srv.server_address[1]}"
    page = {"Origin": SERVER, "Content-Type": "application/json"}
    try:
        status, headers, _ = _request(srv, "OPTIONS", "/api/controllers/open", own, headers={"Origin": SERVER})
        assert status == 204 and headers["Access-Control-Allow-Origin"] == SERVER
        assert "X-Controllers" in headers["Access-Control-Allow-Headers"]
        assert _request(srv, "OPTIONS", "/api/controllers/open", own, headers={"Origin": "https://evil.example"})[0] == 403

        status, headers, body = _request(srv, "POST", "/api/controllers/open", own, {"pass": "good-pass"}, page)
        assert status == 200 and headers["Access-Control-Allow-Origin"] == SERVER
        opened = json.loads(body)
        assert opened["available"] is True and [d["id"] for d in opened["devices"]] == [XBOX]
        token = {"X-Controllers": opened["token"]}
        assert _request(srv, "GET", "/api/controllers", own, headers={"Origin": SERVER, **token})[0] == 200
        assert _request(srv, "GET", "/api/controllers", own, headers={"Origin": SERVER})[0] == 401          # no token
        assert _request(srv, "POST", "/api/controllers/scan", own, {}, {**page, **token})[0] == 202
        assert _request(srv, "POST", "/api/controllers/pair", own, {"id": SONY}, {**page, **token})[0] == 202
        assert bt.devices[SONY]["Paired"] == "yes"

        # Anyone else: another site, the helper's own pages, a wrong Host, or the hotspot.
        for origin in ("https://evil.example", f"http://{own}", None):
            headers = {"Content-Type": "application/json", **token, **({"Origin": origin} if origin else {})}
            assert _request(srv, "POST", "/api/controllers/scan", own, {}, headers)[0] == 403, origin
        assert _request(srv, "POST", "/api/controllers/open", "evil.example", {"pass": "good-pass"}, page)[0] == 403
        asked = len(server.calls)
        assert _request(hot, "POST", "/api/controllers/open", "10.42.0.1", {"pass": "good-pass"},
                        {"Origin": SERVER, "Content-Type": "application/json"})[0] in (403, 404)   # not there at all
        assert len(server.calls) == asked                                       # the hotspot never asked the server
        assert _request(srv, "POST", "/api/controllers/open", own, {"pass": "stale"}, page)[0] == 403
    finally:
        for s in (srv, hot):
            s.shutdown()
            s.server_close()


def test_the_box_pages_take_a_controller():
    for page in (ws.START_PAGE, ws.WIFI_PAGE, ws.SETTINGS_PAGE):
        assert page.count(ws.PAD_SCRIPT) == 1 and page.rstrip().endswith("</body></html>")
