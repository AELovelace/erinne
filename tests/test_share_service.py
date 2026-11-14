"""The share service (docs/plans/services-rewrite.md, Phase 5): one process walks the share's permissions for every
process (and watches it); the others read its walks from share-rights.db, ask it for new ones, and only walk
themselves if it doesn't answer, so nothing waits for good."""

import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from fakes import find_bash
from mnemerynne import events, permissions, share
from test_performance import CountingChecker, _write, checker, conn, jane, root, rt, settings  # noqa: F401


def _library(conn, settings, root):
    for place in ("Guides/One", "Plans/Two"):
        _write(root / "Docs" / place / "x.md")
    share.sync(conn, settings)
    return conn.execute("SELECT id FROM collections WHERE kind = 'library' AND share_path = 'Docs'").fetchone()[0]


def _fid(conn, name):
    return conn.execute("SELECT id FROM folders WHERE name = ?", (name,)).fetchone()[0]


def _key(rt, library):
    uid, gids = rt.directory.ids_of("jsmith")
    return (library, uid, gids)


@pytest.fixture
def heard(monkeypatch):
    """What was published on the event bus (share:walk, share:rights)."""
    said = []
    monkeypatch.setattr(events, "publish_sync", lambda channel, data: said.append((channel, data)) or 0)
    return said


def test_the_share_service_walks_from_the_database_alone_and_the_others_just_read_it(conn, settings, root, rt, jane,
                                                                                    heard):
    library = _library(conn, settings, root)
    key = _key(rt, library)
    assert share.serve_walks_once(rt, key)                         # the share service: no request, only the key
    assert ("share:rights", {"library": library}) in heard
    walked = share._load_rights(rt, key)
    assert walked is not None and walked.folders[_fid(conn, "Guides")] == (True, True)

    reader = share.configure(settings, rt.directory, CountingChecker(root), owner=False)   # the web app, say
    assert permissions.reach(conn, jane)[library].can_read(_fid(conn, "Guides"))
    assert reader.checker.asked == []                              # read from share-rights.db: no walk here


def test_a_key_nobody_walked_is_asked_for_and_walked_here_if_nobody_answers(conn, settings, root, rt, jane, heard,
                                                                           monkeypatch):
    library = _library(conn, settings, root)
    monkeypatch.setattr(share, "ASK_SECONDS", 0.3)
    monkeypatch.setattr(share, "POLL_SECONDS", 0.05)
    reader = share.configure(settings, rt.directory, CountingChecker(root), owner=False)
    started = time.monotonic()
    assert permissions.reach(conn, jane)[library].can_read(_fid(conn, "Guides"))
    assert time.monotonic() - started >= 0.3                        # it waited for the share service first
    assert heard[0][0] == "share:walk" and heard[0][1]["library"] == library
    assert reader.checker.asked                                    # then walked it itself: nobody waits for good
    assert share._load_rights(reader, _key(reader, library)) is not None   # and saved it for everyone


def test_a_walk_that_arrives_while_waiting_is_used(conn, settings, root, rt, jane, heard, monkeypatch):
    library = _library(conn, settings, root)
    key = _key(rt, library)
    monkeypatch.setattr(share, "POLL_SECONDS", 0.05)
    owner = rt
    reader = share.configure(settings, rt.directory, CountingChecker(root), owner=False)
    # The share service answers the request a moment later (on its own thread, as it would in its own process).
    import threading
    threading.Timer(0.2, lambda: share.serve_walks_once(owner, key)).start()
    assert permissions.reach(conn, jane)[library].can_read(_fid(conn, "Guides"))
    assert reader.checker.asked == []                              # the service's walk, not one of its own


def test_a_change_the_share_service_announces_is_read_at_the_next_look(conn, settings, root, rt, jane, heard):
    library = _library(conn, settings, root)
    key = _key(rt, library)
    guides = _fid(conn, "Guides")
    share.serve_walks_once(rt, key)
    owner = rt
    reader = share.configure(settings, rt.directory, CountingChecker(root), owner=False)
    assert permissions.reach(conn, jane)[library].can_read(guides)

    owner.checker.rules["Docs/Guides"] = (False, False)            # the share changes; the service re-reads it
    share.apply_changes(owner, conn, [root / "Docs" / "Guides"])
    share._runtime = reader                                        # back in the reader's process
    assert permissions.reach(conn, jane)[library].can_read(guides)   # within RELOAD_SECONDS: the last read stands
    share.heard(library)                                           # "share:rights" arrives
    assert not permissions.reach(conn, jane)[library].can_read(guides)
    assert reader.checker.asked == []


def test_only_the_walker_watches_the_share(settings, rt, monkeypatch):
    monkeypatch.setattr(share.shutil, "which", lambda name: "/usr/bin/inotifywait")
    started = []
    monkeypatch.setattr(share.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: started.append(k)})())
    reader = share.configure(settings, rt.directory, rt.checker, owner=False)
    share.start_watching()
    assert started == [] and reader.watcher is None
    owner = share.configure(settings, rt.directory, rt.checker, owner=True)
    share.start_watching()
    assert started and started[0]["name"] == "share-watch" and owner.watcher is not None


def test_due_walks_are_found_for_the_share_service(conn, settings, root, rt, jane, heard):
    library = _library(conn, settings, root)
    key = _key(rt, library)
    share.serve_walks_once(rt, key)
    assert share.due_keys(rt) == []
    share._mark_due(rt, library)
    assert share.due_keys(rt) == [key]


def test_the_share_service_answers_a_walk_request_on_the_bus(conn, settings, root, rt, jane, monkeypatch):
    library = _library(conn, settings, root)
    key = _key(rt, library)
    monkeypatch.setattr(events, "_bus", events.LocalBus())

    async def run():
        walker = asyncio.create_task(share.serve_walks())
        await asyncio.sleep(0.05)
        share._ask_for_walk(key)
        for _ in range(100):
            if share._load_rights(rt, key) is not None:
                break
            await asyncio.sleep(0.05)
        walker.cancel()
        await asyncio.gather(walker, return_exceptions=True)
    asyncio.run(run())
    assert share._load_rights(rt, key) is not None


def test_front_door_tells_the_app_who_walks(tmp_path):
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
    script = r'''
source <(sed '$d' "$1"); shift
LOG=$1; ENV=$2; UNIT_DIR=$3; TEMPLATE=$4; SAVED=$1.none; WAIT=2
sleep() { :; }
journalctl() { :; }
ENABLED=""
systemctl() {
    echo "systemctl $*" >> "$LOG"
    case "$1" in
        is-enabled|is-active) [[ " $ENABLED " == *" ${@: -1} "* ]] ;;
        enable) ENABLED="$ENABLED ${@: -1}" ;;
        disable) ENABLED="${ENABLED//${@: -1}/}" ;;
        list-units) echo "mnemerynne-web.service loaded active running x" ;;
    esac
}
app_py() { printf 'share 38211 0\n'; }
curl() { echo '{"status":"ok","role":"share"}'; }
services
'''
    deploy = str(Path(__file__).resolve().parents[1] / "deploy" / "linux" / "front-door.sh")
    args = [log.as_posix(), env.as_posix(), units.as_posix(), template.as_posix()]
    result = subprocess.run([bash, "-c", script, "_", deploy, *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "MNEMERYNNE_SHARE_WALKS=service" in env.read_text()
    assert "systemctl restart mnemerynne-web" in log.read_text()     # the app hears it
    env.write_text("MNEMERYNNE_HOST=127.0.0.1\nMNEMERYNNE_SHARE_WALKS=service\nMNEMERYNNE_SERVICES_OFF=share\n")
    result = subprocess.run([bash, "-c", script, "_", deploy, *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "MNEMERYNNE_SHARE_WALKS" not in env.read_text()           # no share service: everyone walks again
