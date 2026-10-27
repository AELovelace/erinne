"""Rooms: channels (open, private, group) and direct messages, threads, reactions, pins, edits, mentions,
unread counts, files and link previews, live delivery, Claude's room tools and Erynne answering @mentions
with only what the whole room may read."""

import asyncio
import io
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import api_tokens, permissions, retention, rooms, rooms_bus, rooms_erynne, users
from mnemerynne.clients.llm import ToolTurn
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from test_agent import AgentLLM


@pytest.fixture(autouse=True)
def _rooms_on(conn):
    """Rooms is off until an admin turns it on; these tests are about it being on."""
    rooms.set_enabled(conn, True)
    if conn.in_transaction:
        conn.commit()


@pytest.fixture
def people(conn):
    ids = {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "carol")}
    ids["boss"] = users.create_user(conn, "boss", "password123", role="admin")
    conn.execute("UPDATE users SET ad_account = username")
    return ids


@pytest.fixture
def directory(people):
    return FakeDirectory({"alice": {"Front Desk"}, "bob": {"Front Desk"}, "carol": {"IT"}, "boss": {"Domain Admins"}},
                         groups=["Front Desk", "IT", "Domain Admins"])


def _u(conn, name):
    return users.get_user(conn, name)


@contextmanager
def _web(settings, name, directory, agent_llm=None):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  agent_llm=agent_llm))) as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


# ------------------------------------------------------------------ access
def test_who_sees_which_room(conn, people, directory):
    alice, bob, carol = _u(conn, "alice"), _u(conn, "bob"), _u(conn, "carol")
    lobby = rooms.create_channel(conn, alice, "Lobby", topic="Everyone")
    desk = rooms.create_channel(conn, alice, "desk", visibility="group", group_ref="Front Desk")
    secret = rooms.create_channel(conn, alice, "managers", visibility="private")
    assert rooms.room(conn, lobby)["name"] == "lobby"
    with pytest.raises(rooms.RoomError, match="already"):
        rooms.create_channel(conn, bob, "#LOBBY")
    with pytest.raises(rooms.RoomError, match="channel name"):
        rooms.create_channel(conn, bob, "no way!")

    def sees(user, room_id):
        return rooms.can_see(conn, user, rooms.room(conn, room_id), directory)
    assert sees(carol, lobby) and sees(bob, desk) and not sees(carol, desk)
    assert not sees(bob, secret) and sees(alice, secret)
    with pytest.raises(rooms.NotFound):                      # a private room's existence isn't given away
        rooms.history(conn, bob, secret, directory)
    rooms.invite(conn, alice, secret, "bob", directory)
    assert sees(bob, secret)
    # Group channels follow the directory live.
    directory.members["carol"] = {"it", "front desk"}
    assert sees(carol, desk)
    names = {r["name"] for r in rooms.rooms_for(conn, carol, directory)}
    assert names == {"desk"}                                  # lobby is open but not joined
    rooms.join(conn, carol, lobby, directory)
    assert {r["name"] for r in rooms.rooms_for(conn, carol, directory)} == {"desk", "lobby"}
    with pytest.raises(rooms.NotFound):                      # can't see it, so it doesn't exist for her
        rooms.join(conn, carol, secret, directory)
    assert {c["name"] for c in rooms.browse(conn, bob, directory)} == {"lobby", "desk"}


def test_dms_are_one_room_per_set_of_people(conn, people, directory):
    alice = _u(conn, "alice")
    a = rooms.open_dm(conn, alice, ["bob"])
    assert rooms.open_dm(conn, _u(conn, "bob"), ["Alice"]) == a
    group = rooms.open_dm(conn, alice, ["bob", "carol"])
    assert group != a and rooms.room(conn, group)["kind"] == "group_dm"
    with pytest.raises(rooms.RoomError, match="nobody called"):
        rooms.open_dm(conn, alice, ["ghost"])
    assert not rooms.can_see(conn, _u(conn, "carol"), rooms.room(conn, a), directory)
    assert rooms.view_room(conn, rooms.room(conn, a), alice)["name"] == "bob"


# ---------------------------------------------------------------- messages
def test_messages_threads_reactions_pins_and_edits(conn, people, directory):
    alice, bob, carol = _u(conn, "alice"), _u(conn, "bob"), _u(conn, "carol")
    room_id = rooms.create_channel(conn, alice, "desk", visibility="group", group_ref="Front Desk")
    m, asked = rooms.post(conn, alice, room_id, "**Night audit** <script>x</script> @bob @Erynne", directory)
    assert asked and "<strong>Night audit</strong>" in m["html"] and "<script>" not in m["html"]
    assert m["mentions"] == ["bob"]
    with pytest.raises(rooms.NotFound):
        rooms.post(conn, carol, room_id, "hi", directory)             # not in the group
    reply, _ = rooms.post(conn, bob, room_id, "Done.", directory, thread_root_id=m["id"])
    deeper, _ = rooms.post(conn, alice, room_id, "Thanks", directory, thread_root_id=reply["id"])
    assert deeper["thread_root_id"] == m["id"]                       # one level of threads
    assert [x["body"] for x in rooms.thread(conn, bob, reply["id"], directory)][1:] == ["Done.", "Thanks"]
    assert [x["id"] for x in rooms.history(conn, bob, room_id, directory)] == [m["id"]]   # replies stay in the thread
    assert rooms.history(conn, bob, room_id, directory)[0]["replies"] == 2

    out = rooms.react(conn, bob, m["id"], "👍", True, directory)
    rooms.react(conn, alice, m["id"], "👍", True, directory)
    assert out["reactions"] == [{"emoji": "👍", "users": ["bob"]}]
    assert rooms.history(conn, bob, room_id, directory)[0]["reactions"][0]["users"] == ["bob", "alice"]
    with pytest.raises(rooms.RoomError):
        rooms.react(conn, bob, m["id"], "<b>", True, directory)
    assert rooms.pin(conn, bob, m["id"], True, directory)["pinned"]
    assert [p["id"] for p in rooms.pins(conn, alice, room_id, directory)] == [m["id"]]

    with pytest.raises(rooms.Forbidden):
        rooms.edit(conn, bob, m["id"], "mine now", directory)
    edited = rooms.edit(conn, alice, m["id"], "Night audit at 2", directory)
    assert edited["edited_at"] and edited["body"] == "Night audit at 2"
    with pytest.raises(rooms.Forbidden):
        rooms.delete(conn, bob, m["id"], directory)                  # bob isn't the owner
    gone = rooms.delete(conn, alice, m["id"], directory)
    assert gone["deleted"] and gone["body"] == "" and gone["reactions"] == [] and not gone["pinned"]
    assert rooms.delete(conn, _u(conn, "boss"), reply["id"], directory, admin=True)["deleted"]


def test_unread_mentions_and_the_read_cursor(conn, people, directory):
    alice, bob = _u(conn, "alice"), _u(conn, "bob")
    room_id = rooms.create_channel(conn, alice, "lobby")
    rooms.join(conn, bob, room_id, directory)
    m1, _ = rooms.post(conn, alice, room_id, "Morning all", directory)
    m2, _ = rooms.post(conn, alice, room_id, "@bob can you check the till?", directory)
    lobby = next(r for r in rooms.rooms_for(conn, bob, directory) if r["id"] == room_id)
    assert (lobby["unread"], lobby["mentions"]) == (2, 1)
    assert rooms.unread_summary(conn, bob, directory)["mentions"] == 1
    rooms.mark_read(conn, bob, room_id, m2["id"], directory)
    lobby = next(r for r in rooms.rooms_for(conn, bob, directory) if r["id"] == room_id)
    assert (lobby["unread"], lobby["mentions"]) == (0, 0)
    assert next(r for r in rooms.rooms_for(conn, alice, directory) if r["id"] == room_id)["unread"] == 0   # her own
    # @channel reaches the room's audience; @here only those online.
    rooms.join(conn, _u(conn, "carol"), room_id, directory)
    m3, _ = rooms.post(conn, alice, room_id, "@here coffee?", directory, online={people["carol"]})
    assert m3["mentions"] == ["carol"]
    m4, _ = rooms.post(conn, alice, room_id, "@channel fire drill", directory)
    assert sorted(m4["mentions"]) == ["bob", "carol"]


def test_archived_rooms_and_owners(conn, people, directory):
    alice, bob = _u(conn, "alice"), _u(conn, "bob")
    room_id = rooms.create_channel(conn, alice, "old")
    rooms.join(conn, bob, room_id, directory)
    with pytest.raises(rooms.Forbidden):
        rooms.update(conn, bob, room_id, directory, topic="mine")
    rooms.update(conn, alice, room_id, directory, archived=True, topic="Closed")
    with pytest.raises(rooms.Forbidden, match="archived"):
        rooms.post(conn, bob, room_id, "hello?", directory)
    assert room_id not in {r["id"] for r in rooms.rooms_for(conn, bob, directory)}


# --------------------------------------------------------------------- web
def test_the_web_api(settings, conn, people, directory):
    with _web(settings, "alice", directory) as c:
        assert "Rooms" in c.get("/rooms").text
        made = c.post("/api/rooms", json={"name": "front-office", "topic": "Desk"}).json()["room"]
        assert made["name"] == "front-office" and made["role"] == "owner"
        posted = c.post(f"/api/rooms/{made['id']}/messages", json={"text": "Hello **team**"})
        assert posted.status_code == 201 and "<strong>team</strong>" in posted.json()["message"]["html"]
        dm = c.post("/api/rooms/dm", json={"people": ["bob"]}).json()["room"]
        c.post(f"/api/rooms/{dm['id']}/messages", json={"text": "Lunch?"})
        listed = c.get("/api/rooms").json()
        assert [r["name"] for r in listed["rooms"]] == ["front-office", "bob"] and listed["me"] == "alice"
        assert listed["notes"] == 0                                           # the Inbox entry's badge
        assert c.post("/api/rooms", json={"name": "bad name!"}).status_code == 400
        for _ in range(40):
            last = c.post(f"/api/rooms/{made['id']}/messages", json={"text": "spam"})
        assert last.status_code == 429
    with _web(settings, "bob", directory) as c:
        assert c.get("/api/notify").json()["rooms"]["dms"] == 1
        page = c.get("/rooms").text
        assert ">1<" in page.replace(" ", "")                                 # the badge on the Messages button
        # With Rooms on, messages live in Rooms: the button links to the Inbox there, no fullscreen panel.
        assert 'id="notify-button" href="/rooms#inbox"' in page and 'id="msg-modal"' not in page
        assert 'id="rooms-inbox"' in page and 'id="rooms-inbox-link"' in page
        assert c.get(f"/api/rooms/{dm['id']}/messages").json()["messages"][0]["body"] == "Lunch?"
        assert c.get("/api/rooms/9999").status_code == 404
    with _web(settings, "carol", directory) as c:
        assert c.get(f"/api/rooms/{dm['id']}/messages").status_code == 404


# ------------------------------------------------------------------ files
def test_files_and_previews(settings, conn, people, directory):
    alice, carol = _u(conn, "alice"), _u(conn, "carol")
    lib = permissions.create_library(conn, "Manuals")
    permissions.grant_user(conn, lib, people["alice"])
    fid = store.add_file(conn, settings, store.receive(io.BytesIO(b"# Audit\n"), "Audit.md", settings), lib, None)[0]
    with _web(settings, "alice", directory) as c:
        room_id = c.post("/api/rooms", json={"name": "desk"}).json()["room"]["id"]
        up = c.post(f"/api/rooms/{room_id}/files", files={"file": ("shot.png", b"\x89PNG fake", "image/png")})
        assert up.status_code == 201, up.text
        f = up.json()["file"]
        m = c.post(f"/api/rooms/{room_id}/messages", json={"text": f"See [the manual](/files/{fid}/view)",
                                                           "attachments": [f["id"]]}).json()["message"]
        assert m["attachments"] == [{"id": f["id"], "name": "shot.png", "mime": "image/png", "bytes": 9}]
        got = c.get(f"/api/rooms/files/{f['id']}")
        assert got.status_code == 200 and got.headers["content-disposition"].startswith("inline")
        assert c.post("/api/rooms/previews", json={"links": [f"/files/{fid}/view"]}).json()["previews"][
            f"/files/{fid}/view"]["title"] == "Audit.md"
        settings.room_file_bytes = 4
        assert c.post(f"/api/rooms/{room_id}/files", files={"file": ("big.txt", b"12345", "text/plain")}).status_code == 400
    with _web(settings, "carol", directory) as c:
        c.post(f"/api/rooms/{room_id}/members", json={"action": "join"})
        assert c.get(f"/api/rooms/files/{f['id']}").status_code == 200         # open channel: anyone
        # carol can't read the manual: the preview hides it.
        assert c.post("/api/rooms/previews", json={"links": [f"/files/{fid}/view"]}).json()["previews"] == {
            f"/files/{fid}/view": {"hidden": True}}
    rooms.delete(conn, alice, m["id"], directory)
    with _web(settings, "carol", directory) as c:
        assert c.get(f"/api/rooms/files/{f['id']}").status_code == 404
    assert retention.purge(conn, settings).get("chat_attachments") == 1
    assert rooms.attachment.__name__ and not list((settings.data_dir / "rooms").glob("*.bin"))
    assert carol["id"]


# ------------------------------------------------------------------ stream
def test_the_stream_sends_a_persons_rooms_and_replays(settings, conn, people, directory):
    alice, bob, carol = _u(conn, "alice"), _u(conn, "bob"), _u(conn, "carol")
    desk = rooms.create_channel(conn, alice, "desk", visibility="group", group_ref="Front Desk")
    it = rooms.create_channel(conn, carol, "it", visibility="group", group_ref="IT")
    conn.commit()
    bus = rooms_bus.Bus(settings.db_path)

    async def collect(user, after, until):
        got, stop = [], asyncio.Event()

        async def disconnected():
            return stop.is_set()
        async for chunk in bus.stream(user, after, directory, disconnected):
            text = chunk.decode()
            for block in text.split("\n\n"):
                if block.startswith("id:") or block.startswith("event:"):
                    lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
                    got.append((lines.get("event"), json.loads(lines.get("data", "{}")), lines.get("id")))
            if until(got):
                stop.set()
                break
        return got

    async def scenario():
        reader = asyncio.create_task(collect(bob, 0, lambda g: any(e[0] == "message.new" for e in g)))
        await asyncio.sleep(0.05)
        assert bus.online() == {bob["id"]} and bus.presence() == {"bob": "online"}
        rooms.post(conn, carol, it, "IT only", directory)            # bob can't see #it
        rooms.post(conn, alice, desk, "Desk news", directory)
        conn.commit()
        bus.wake()
        return await asyncio.wait_for(reader, 5)

    got = asyncio.run(scenario())
    news = [e for e in got if e[0] == "message.new"]
    assert [e[1]["message"]["body"] for e in news] == ["Desk news"]
    cursor = int(news[0][2])
    # Reconnecting from before it replays it; typing/presence go only to streams that are open.
    replay = asyncio.run(asyncio.wait_for(collect(bob, cursor - 1, lambda g: any(e[0] == "message.new" for e in g)), 5))
    assert [e[1]["message"]["body"] for e in replay if e[0] == "message.new"] == ["Desk news"]


# --------------------------------------------------------------- MCP tools
def test_claude_reads_and_posts_in_rooms(settings, conn, people, directory):
    alice = _u(conn, "alice")
    room_id = rooms.create_channel(conn, alice, "desk", visibility="group", group_ref="Front Desk")
    rooms.post(conn, alice, room_id, "Who's on nights?", directory)
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    bob = api_tokens.verify(conn, api_tokens.create(conn, people["bob"], "laptop", can_edit=True)[1])
    listed = tools.list_rooms(conn, bob)[0]["rooms"]
    assert [r["room"] for r in listed] == ["#desk"] and listed[0]["unread"] == 1
    read = tools.read_room(conn, bob, "#desk")[0]
    assert read["messages"][0]["from"] == "alice" and read["messages"][0]["text"] == "Who's on nights?"
    posted = tools.post_to_room(conn, bob, "desk", "Bob is, from 22:00.")[0]
    assert rooms.history(conn, alice, room_id, directory)[-1]["who"] == "Claude for bob"
    dm = tools.post_to_room(conn, bob, "@alice", "Swapping shifts?")[0]
    assert rooms.room(conn, dm["room_id"])["kind"] == "dm"
    carol = api_tokens.verify(conn, api_tokens.create(conn, people["carol"], "laptop", can_edit=True)[1])
    with pytest.raises(Exception, match="no room called"):
        tools.read_room(conn, carol, "#desk")
    reader = api_tokens.verify(conn, api_tokens.create(conn, people["bob"], "ro", can_edit=False)[1])
    with pytest.raises(Exception, match="only search"):
        tools.post_to_room(conn, reader, "#desk", "hi")
    assert tools.react_in_room(conn, bob, posted["message_id"], "👍")[0]["reactions"] == {"👍": 1}


# ---------------------------------------------------------------- @Erynne
def test_erynne_reads_only_what_everyone_in_the_room_can(conn, people):
    shared = permissions.create_library(conn, "Shared")
    hers = permissions.create_library(conn, "Alice only")
    for name in ("alice", "bob"):
        permissions.grant_user(conn, shared, people[name])
    permissions.grant_user(conn, hers, people["alice"])
    access = rooms_erynne.shared_access(conn, [people["alice"], people["bob"]])
    assert access.readable == {shared}                       # not alice's library, nor either File box
    assert rooms_erynne.shared_access(conn, [people["alice"]]).readable >= {shared, hers}


def test_erynne_answers_an_at_mention_in_a_thread(settings, conn, people, directory):
    llm = AgentLLM()
    llm.turns(ToolTurn("The night audit runs at 2 am."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        room_id = c.post("/api/rooms", json={"name": "desk"}).json()["room"]["id"]
        asked = c.post(f"/api/rooms/{room_id}/messages", json={"text": "@Erynne when is the audit?"}).json()["message"]
        for _ in range(100):
            thread = c.get(f"/api/rooms/messages/{asked['id']}/thread").json()["messages"]
            if len(thread) > 1:
                break
            c.portal.call(asyncio.sleep, 0.05)
        assert thread[1]["who"] == "Erynne" and thread[1]["body"] == "The night audit runs at 2 am."
        system = llm.calls[0][0][0]["content"]
        assert "#desk" in system and "everyone here" in system
        assert set(llm.calls[0][1]) <= {"search_documents", "read_passage", "list_libraries", "browse_folder",
                                        "find_files", "open_file", "list_versions"}      # reading only
        # Off for the room: no answer.
        c.patch(f"/api/rooms/{room_id}", json={"erynne": False})
        c.post(f"/api/rooms/{room_id}/messages", json={"text": "@Erynne again?"})
        c.portal.call(asyncio.sleep, 0.2)
        assert len(llm.calls) == 1


# ----------------------------------------------------------------- on/off
def test_rooms_is_off_until_an_admin_turns_it_on(settings, conn, people, directory):
    alice = _u(conn, "alice")
    room_id = rooms.create_channel(conn, alice, "desk")
    rooms.post(conn, alice, room_id, "Kept while it's off", directory)
    rooms.set_enabled(conn, False)
    if conn.in_transaction:
        conn.commit()
    with _web(settings, "alice", directory) as c:
        assert 'href="/rooms"' not in c.get("/").text
        assert c.get("/rooms").status_code == 404
        assert c.get("/api/rooms").status_code == 404
        assert c.get("/api/rooms/stream").status_code == 404
        assert c.post(f"/api/rooms/{room_id}/messages", json={"text": "hi"}).status_code == 404
        assert c.get("/api/notify").json()["rooms"] == {"rooms": 0, "mentions": 0, "dms": 0}
        assert c.get("/api/admin/settings/rooms").status_code == 403          # admins only
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    bob = api_tokens.verify(conn, api_tokens.create(conn, people["bob"], "laptop", can_edit=True)[1])
    with pytest.raises(Exception, match="isn't turned on"):
        tools.list_rooms(conn, bob)
    with pytest.raises(Exception, match="isn't turned on"):
        tools.post_to_room(conn, bob, "#desk", "hello")

    with _web(settings, "boss", directory) as c:
        status = c.get("/api/admin/settings/rooms").json()
        assert status["enabled"] is False and [ch["name"] for ch in status["channels"]] == ["desk"]
        assert c.put("/api/admin/settings/rooms", json={"enabled": True}).json()["enabled"] is True
        assert 'href="/rooms"' in c.get("/").text
    with _web(settings, "alice", directory) as c:
        assert c.get(f"/api/rooms/{room_id}/messages").json()["messages"][0]["body"] == "Kept while it's off"
    actions = [r[0] for r in conn.execute("SELECT action FROM access_log WHERE action LIKE 'settings.rooms%'")]
    assert actions == ["settings.rooms"]
