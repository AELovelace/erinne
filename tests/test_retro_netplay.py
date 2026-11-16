"""Retro netplay's rooms (media/netplay.py) and their websocket (web/retro.py): who may open and join a room, what
is passed on to whom, and the host's say over the room. The lockstep itself runs in the browser
(static/retro-netplay.js, tests/retro-netplay-browser.mjs)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mnemerynne.media import netplay
from test_retro import _by_title, _login, _put, _turn_on, client, conn, checker, directory, root, settings  # noqa: F401
from mnemerynne import share


class Inbox:
    """A browser's end of the room: what it was sent."""

    def __init__(self):
        self.got = []

    async def send(self, message):
        self.got.append(json.loads(message) if isinstance(message, str) else message)

    def last(self, kind):
        return [m for m in self.got if isinstance(m, dict) and m.get("t") == kind][-1]


def run(coro):
    return asyncio.run(coro)


def test_a_room_relays_to_the_others_and_the_host_steers_it():
    async def scene():
        rooms = netplay.Rooms()
        host_box, guest_box, third_box = Inbox(), Inbox(), Inbox()
        room, host = await rooms.open(game_id=7, title="Lumi Test", console="nes", user_id=1, name="boss", send=host_box.send)
        assert host.slot == 0 and host_box.last("welcome")["code"] == room.code
        assert rooms.open_for([7]) == [room.summary()] and rooms.open_for([8]) == []

        with pytest.raises(netplay.NetplayError):
            await rooms.join(room.code, game_id=8, user_id=2, name="jane", send=guest_box.send)   # another game
        _, guest = await rooms.join(room.code.lower(), game_id=7, user_id=2, name="jane", send=guest_box.send)
        assert guest.slot == 1 and [p["name"] for p in host_box.last("players")["players"]] == ["boss", "jane"]

        await rooms.relay(room, guest, json.dumps({"t": "i", "f": 9, "c": [[8, 1]], "e": 1}))
        assert host_box.got[-1] == {"t": "i", "f": 9, "c": [[8, 1]], "e": 1, "from": 1}   # to the others, tagged
        assert not any(m.get("t") == "i" for m in guest_box.got if isinstance(m, dict))   # never back to its sender

        before = len(host_box.got)
        await rooms.relay(room, guest, json.dumps({"t": "start", "delay": 3}))           # only the host starts
        await rooms.relay(room, guest, b"a guest's state")                               # or sends a state
        await rooms.relay(room, guest, json.dumps({"t": "anything"}))                    # unknown: dropped
        await rooms.relay(room, guest, "not json")
        assert len(host_box.got) == before and room.started is False

        await rooms.relay(room, host, json.dumps({"t": "start", "delay": 3, "epoch": 1}))
        await rooms.relay(room, host, b"STATE")
        assert room.started is True and guest_box.got[-2]["t"] == "start" and guest_box.got[-1] == b"\x00STATE"

        _, third = await rooms.join(room.code, game_id=7, user_id=3, name="lumi", send=third_box.send)
        assert third.slot == 2 and third_box.last("welcome")["started"] is True
        await rooms.relay(room, host, json.dumps({"t": "kick", "slot": 2}))              # the host lets someone go
        assert third_box.got[-1]["t"] == "closed"
        await rooms.leave(room, third)
        assert [p["slot"] for p in guest_box.last("players")["players"]] == [0, 1]

        await rooms.leave(room, host)                                                   # the host leaving closes it
        assert guest_box.got[-1] == {"t": "closed", "why": "boss closed the room."}
        assert rooms.get(room.code) is None and rooms.open_for([7]) == []
        with pytest.raises(netplay.NetplayError):
            await rooms.join(room.code, game_id=7, user_id=4, name="late", send=Inbox().send)
    run(scene())


def test_a_room_holds_four():
    async def scene():
        rooms = netplay.Rooms()
        room, _ = await rooms.open(game_id=1, title="x", console="snes", user_id=1, name="a", send=Inbox().send)
        for n in range(2, 5):
            await rooms.join(room.code, game_id=1, user_id=n, name=str(n), send=Inbox().send)
        with pytest.raises(netplay.NetplayError, match="full"):
            await rooms.join(room.code, game_id=1, user_id=9, name="fifth", send=Inbox().send)
    run(scene())


def test_the_websocket_lets_in_only_who_may_play_the_game(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "NES" / "Lumi Test.nes", b"NES\x1a" + b"\0" * 32)
    share.sync(conn, settings)
    game = _by_title(client)["Lumi Test"]["id"]
    second = TestClient(client.app)
    _login(second, conn, directory, name="jane", account="jsmith")

    csp = client.get(f"/media/retro/play/{game}").headers["content-security-policy"]
    assert "connect-src 'self' wss://testserver ws://testserver blob: data:" in csp   # the room's websocket, by name
    with client.websocket_connect(f"/api/media/retro/netplay/ws?game={game}") as host:
        welcome = host.receive_json()
        assert welcome["t"] == "welcome" and welcome["slot"] == 0
        rooms = second.get(f"/api/media/retro/netplay/rooms?game={game}").json()["rooms"]
        assert [r["code"] for r in rooms] == [welcome["code"]] and rooms[0]["host"] == "boss"
        with second.websocket_connect(f"/api/media/retro/netplay/ws?game={game}&room={welcome['code']}") as guest:
            assert guest.receive_json()["slot"] == 1
            assert [p["name"] for p in host.receive_json()["players"]] == ["boss", "jane"]
            guest.receive_json()                                          # its own roster
            guest.send_text(json.dumps({"t": "i", "f": 5, "c": [], "e": 1}))
            assert host.receive_json() == {"t": "i", "f": 5, "c": [], "e": 1, "from": 1}
            host.send_bytes(b"STATE")
            assert guest.receive_bytes() == b"\x00STATE"
        assert [p["name"] for p in host.receive_json()["players"]] == ["boss"]   # jane left

    with pytest.raises(WebSocketDisconnect) as refused:                   # a game that isn't there (or theirs)
        with client.websocket_connect("/api/media/retro/netplay/ws?game=999999") as ws:
            ws.receive_json()
    assert refused.value.code == 4403
    assert client.get("/api/media/retro/netplay/rooms?game=999999").status_code == 404
    signed_out = TestClient(client.app)
    with pytest.raises(WebSocketDisconnect):
        with signed_out.websocket_connect(f"/api/media/retro/netplay/ws?game={game}") as ws:
            ws.receive_json()
