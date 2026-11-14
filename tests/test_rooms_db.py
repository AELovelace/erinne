"""rooms.db (docs/plans/services-rewrite.md, Phase 3): Rooms' tables move out of core, a message outlives its
author as before, and Rooms' live events (new messages, typing, presence, asks for Erynne) cross processes."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from fakes import FakeDirectory, find_bash
from mnemerynne import db, events, rooms, rooms_bus, users
from mnemerynne.services import nginx, registry


@pytest.fixture
def directory():
    return FakeDirectory({"alice": {"Front Desk"}, "bob": {"Front Desk"}}, groups=["Front Desk"])


def _core_with_rooms(settings, directory):
    """Today's box: only core's migrations, Rooms' tables in core with a channel and messages in it."""
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    rooms.set_enabled(core, True)
    alice_id = users.create_user(core, "alice", "password123")
    bob_id = users.create_user(core, "bob", "password123")
    alice, bob = users.get_user_by_id(core, alice_id), users.get_user_by_id(core, bob_id)
    with db.transaction(core):
        room_id = rooms.create_channel(core, alice, "front-office")
        rooms.join(core, bob, room_id, directory)
        rooms.post(core, alice, room_id, "Good morning", directory)
        rooms.post(core, bob, room_id, "Morning! @alice", directory)
    return core, alice, bob, room_id


def test_the_rooms_migration_has_cores_columns_exactly(settings):
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    feature = db.connect_feature(settings.db_path, "rooms")
    db.migrate_feature(feature, "rooms")
    for table in db.ROOMS_TABLES:
        mine = [tuple(r)[1:] for r in core.execute(f"PRAGMA main.table_info({table})")]
        theirs = [tuple(r)[1:] for r in feature.execute(f"PRAGMA main.table_info({table})")]
        assert mine == theirs, table
        assert not [r for r in feature.execute(f"PRAGMA main.foreign_key_list({table})") if r["table"] == "users"]


def test_migrate_all_moves_every_rooms_row_and_the_room_reads_the_same(settings, directory):
    core, alice, bob, room_id = _core_with_rooms(settings, directory)
    before = {t: core.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in db.ROOMS_TABLES}
    history = [m["body"] for m in rooms.history(core, alice, room_id, directory)]
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    assert not core.execute("SELECT 1 FROM sqlite_master WHERE name = 'chat_messages'").fetchone()
    with rooms.on(core) as there:
        assert there is not core
        assert {t: there.execute(f"SELECT count(*) FROM main.{t}").fetchone()[0] for t in db.ROOMS_TABLES} == before
        assert [m["body"] for m in rooms.history(there, alice, room_id, directory)] == history
        assert rooms.enabled(there)                                      # core's meta, read through the attach


def test_a_deleted_users_messages_stay_without_their_name(settings, directory):
    core, alice, bob, room_id = _core_with_rooms(settings, directory)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    assert users.delete_user(core, "bob")
    with rooms.on(core) as there:
        kept = there.execute("SELECT body, user_id FROM main.chat_messages ORDER BY id").fetchall()
        assert [(r["body"], r["user_id"]) for r in kept] == [("Good morning", alice["id"]), ("Morning! @alice", None)]
        assert there.execute("SELECT count(*) FROM main.chat_members WHERE user_id = ?", (bob["id"],)).fetchone()[0] == 0


def test_a_user_gone_some_other_way_is_swept_the_same(settings, directory):
    core, alice, bob, room_id = _core_with_rooms(settings, directory)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    core.execute("PRAGMA foreign_keys = OFF")
    core.execute("DELETE FROM users WHERE id = ?", (bob["id"],))
    swept = db.sweep_orphans(settings.db_path)
    assert swept["rooms.chat_members"] == 1 and swept["rooms.chat_messages.user_id"] == 1


def test_moving_back_puts_rooms_in_core_for_older_code(settings, directory):
    core, alice, bob, room_id = _core_with_rooms(settings, directory)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    assert db.move_back(settings.db_path, "rooms")["chat_messages"] == 2
    core = db.connect(settings.db_path)
    assert [m["body"] for m in rooms.history(core, alice, room_id, directory)] == ["Good morning", "Morning! @alice"]


# --- live events across processes ---------------------------------------------------------------------------------

def _shared_valkey(monkeypatch):
    import fakeredis
    server = fakeredis.FakeServer()
    bus = events.ValkeyBus("fake", clients=(fakeredis.FakeRedis(server=server, decode_responses=True),
                                            fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)))
    monkeypatch.setattr(events, "_bus", bus)                    # both "processes" talk to the one Valkey


def test_a_message_posted_in_one_process_wakes_the_streams_of_another(settings, monkeypatch):
    _shared_valkey(monkeypatch)
    web, service = rooms_bus.Bus(settings.db_path), rooms_bus.Bus(settings.db_path)

    async def run():
        listener = rooms_bus.Listener(7, "bob")
        service.listeners.add(listener)
        task = asyncio.create_task(service.listen())
        await asyncio.sleep(0.2)                                  # subscribed
        web.wake()                                                # e.g. Erynne's answer, posted in the web app
        await asyncio.wait_for(listener.wake.wait(), 3)
        listener.wake.clear()
        web.push({7}, "typing", {"room_id": 1, "user": "alice"})
        await asyncio.wait_for(listener.wake.wait(), 3)
        assert listener.queue == [("typing", {"room_id": 1, "user": "alice"})]
        service.push({7}, "typing", {"room_id": 1, "user": "carol"})   # its own: delivered once, not echoed
        await asyncio.sleep(0.3)
        assert len(listener.queue) == 2
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_who_is_online_counts_every_processs_streams(settings, monkeypatch):
    _shared_valkey(monkeypatch)
    web, service = rooms_bus.Bus(settings.db_path), rooms_bus.Bus(settings.db_path)
    service._still_here(9, "carol")                               # carol's tab streams from the rooms service
    assert 9 in web.online() and web.presence()["carol"] == "online"


def test_a_broken_bus_leaves_presence_to_this_process(settings, monkeypatch):
    class Gone(events.ValkeyBus):
        def __init__(self):
            pass

        def expiring_keys(self, pattern):
            raise ConnectionError("no valkey")
    monkeypatch.setattr(events, "_bus", Gone())
    bus = rooms_bus.Bus(settings.db_path)
    bus.listeners.add(rooms_bus.Listener(3, "dana"))
    assert bus.online() == {3}


def test_an_ask_taken_by_the_rooms_service_is_answered_by_the_web_app(monkeypatch):
    from types import SimpleNamespace

    from mnemerynne.web import app as web_app
    asked = []
    fake = SimpleNamespace(state=SimpleNamespace(rooms_erynne=SimpleNamespace(
        ask=lambda user_id, message, bfgguf=False: asked.append((user_id, message["id"], bfgguf)))))
    monkeypatch.setattr(events, "_bus", events.LocalBus())

    async def run():
        task = asyncio.create_task(web_app._answer_rooms_asks(fake))
        await asyncio.sleep(0.05)
        events.publish_sync("rooms:ask", {"user_id": 4, "message": {"id": 99, "room_id": 1}, "bfgguf": True})
        for _ in range(50):
            if asked:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
    assert asked == [(4, 99, True)]


# --- the rooms service on the box -------------------------------------------------------------------------------

def test_the_rooms_service_waits_for_valkey_and_only_running_services_get_paths():
    assert registry.SERVICES["rooms"].needs_valkey and registry.SERVICES["rooms"].paths == ("/api/rooms",)
    conf = nginx.render("192.168.1.5:38200", running=["sync", "games"])
    assert "mnemerynne_rooms" not in conf                                           # the web app keeps them:
    assert "location /api/rooms {\n        proxy_pass http://mnemerynne_web;" in conf  # said outright
    assert "location /api/rooms {\n        proxy_pass http://mnemerynne_rooms;" in nginx.render("192.168.1.5:38200")


def test_front_door_services_leaves_rooms_off_without_valkey(tmp_path):
    bash = find_bash()
    if not bash:
        pytest.skip("needs bash")
    units = tmp_path / "units"
    (units / "mnemerynne-web.service.d").mkdir(parents=True)
    template = tmp_path / "mnemerynne@.service"
    template.write_text("[Unit]\n")
    env = tmp_path / "env"
    env.write_text("MNEMERYNNE_HOST=127.0.0.1\n")
    log = tmp_path / "log"
    script = r'''source <(sed '$d' "$1"); shift
LOG=$1; ENV=$2; UNIT_DIR=$3; TEMPLATE=$4; SAVED=$1.none; WAIT=2
sleep() { :; }
journalctl() { :; }
ENABLED="mnemerynne@rooms"
systemctl() {
    echo "systemctl $*" >> "$LOG"
    case "$1" in
        is-enabled) [[ " $ENABLED " == *" ${@: -1} "* ]] ;;
        is-active) [[ " $ENABLED " == *" ${@: -1} "* ]] ;;
        enable) ENABLED="$ENABLED ${@: -1}" ;;
        disable) ENABLED="${ENABLED//${@: -1}/}" ;;
    esac
}
app_py() { printf 'sync 38201 0\nrooms 38206 1\n'; }
curl() { echo '{"status":"ok","role":"sync"}'; }
services
'''
    result = subprocess.run([bash, "-c", script, "_", str(Path(__file__).resolve().parents[1] / "deploy" / "linux" / "front-door.sh"),
                             log.as_posix(), env.as_posix(), units.as_posix(), template.as_posix()],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    asked = log.read_text().splitlines()
    assert "systemctl enable --now mnemerynne@sync" in asked
    assert "systemctl disable --now mnemerynne@rooms" in asked and "systemctl enable --now mnemerynne@rooms" not in asked
    assert "off again, as Valkey is off" in result.stdout


def test_request_connections_read_every_feature_file_and_write_only_their_own(settings, directory):
    """Before Phase 4: what code reads (a TV's screen from the games router, Rooms' badge from any page) is found
    wherever its table lives; a write has to use the owner's connection, and a stray one fails loudly."""
    import sqlite3
    core, *_ = _core_with_rooms(settings, directory)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    pool = db.Pool(settings.db_path)
    pooled = pool.get()                                            # what a request's get_db lends
    assert pooled.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 2
    assert pooled.execute("SELECT count(*) FROM lumi_pets").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        pooled.execute("DELETE FROM chat_messages")
    games = db.connect_feature(settings.db_path, "games")          # a feature reads the others too
    assert games.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 2
    pool.put(pooled)
    pool.close()
