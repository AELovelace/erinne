"""The Scratchpad page and directions: people read their agents' boards and write on them; a person's note reaches
each session it's for once, with that session's next connector call; notes that fall off are archived."""

import json

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import api_tokens, mcp_server, retention, scratchpad, users
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def tools(settings):
    return Tools(settings, lambda: FakeEmbedder(), lambda: FakeDirectory({}, groups=[]))


@pytest.fixture
def web(settings, conn):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as client:
        yield client


@pytest.fixture
def alice(conn, web):
    uid = users.create_user(conn, "alice", "password123")
    users.create_user(conn, "bob", "password123")
    assert web.post("/login", data={"username": "alice", "password": "password123"},
                    follow_redirects=False).status_code == 303
    return uid


def _session(conn, user_id, name):
    token = api_tokens.create(conn, user_id, name, can_edit=True)[1]
    return api_tokens.verify(conn, token), token


def _call(conn, tools, caller, name, **args):
    """A tool call as the connector makes it: the tool, then what rides along (mcp_server._with_directions)."""
    out, _ = getattr(tools, name)(conn, caller, **args)
    return mcp_server._with_directions(conn, tools, caller, name, args, out)


def _direct(web, board, text, **extra):
    r = web.post("/api/scratchpad/notes", json={"board": board, "text": text, **extra})
    assert r.status_code == 201, r.text
    return r.json()["note_id"]


# -------------------------------------------------------------- directions
def test_a_direction_reaches_the_sessions_it_is_for_once(conn, tools, web, alice):
    a, _ = _session(conn, alice, "laptop")
    b, _ = _session(conn, alice, "desktop")
    _call(conn, tools, a, "read_scratchpad", board="nas", agent="cc-a")      # both work on board nas
    _call(conn, tools, b, "read_scratchpad", board="nas", agent="cc-b")
    to_a = _direct(web, "nas", "Push what you have.", for_agent="cc-a", urgent=True)
    to_all = _direct(web, "nas", "Freeze main for an hour.")

    out = _call(conn, tools, a, "list_libraries")                            # any tool: they ride along
    assert [d["note_id"] for d in out["directions"]] == [to_a, to_all]
    assert out["directions"][0]["author"] == "person" and out["directions"][0]["urgent"]
    assert "directions" not in _call(conn, tools, a, "list_libraries")      # once
    out = _call(conn, tools, b, "list_libraries")
    assert [d["note_id"] for d in out["directions"]] == [to_all]            # not the one for cc-a

    # Still open, read_scratchpad lists them first; answering, claiming and resolving work as for any note.
    read = _call(conn, tools, a, "read_scratchpad", board="nas", agent="cc-a")
    assert [d["note_id"] for d in read["open_directions"]] == [to_a, to_all]
    reply = _call(conn, tools, a, "write_scratchpad", board="nas", text="Pushed.", agent="cc-a", reply_to=to_a)
    _call(conn, tools, a, "resolve_note", note_id=to_a)
    read = _call(conn, tools, a, "read_scratchpad", board="nas", agent="cc-a")
    assert [d["note_id"] for d in read["open_directions"]] == [to_all]
    notes = {n["note_id"]: n for n in read["notes"]}
    assert notes[reply["note_id"]]["reply_to"] == to_a and notes[reply["note_id"]].get("author") is None

    # The page sees who was shown what.
    feed = web.get("/api/scratchpad/feed", params={"board": "nas"}).json()
    seen = {n["note_id"]: [s["agent"] for s in n["seen_by"]] for n in feed["notes"]}
    assert seen[to_all] == ["cc-a", "cc-b"] and seen[to_a] == ["cc-a"]
    assert {s["agent"]: s["state"] for s in feed["sessions"]} == {"cc-a": "active", "cc-b": "active"}


def test_sessions_on_other_boards_dont_get_everyone_notes(conn, tools, web, alice):
    a, _ = _session(conn, alice, "laptop")
    _call(conn, tools, a, "read_scratchpad", board="other", agent="cc-a")
    _direct(web, "nas", "For whoever is on nas.")
    assert "directions" not in _call(conn, tools, a, "list_libraries")
    named = _direct(web, "nas", "But this one is for you.", for_agent="CC-A")       # labels match any case
    assert [d["note_id"] for d in _call(conn, tools, a, "list_libraries")["directions"]] == [named]


def test_agents_can_never_write_as_the_person(conn, tools, web, alice):
    a, _ = _session(conn, alice, "laptop")
    note = _call(conn, tools, a, "write_scratchpad", board="nas", text="I am the person now.", agent="sneaky")
    row = conn.execute("SELECT author FROM scratchpad_notes WHERE id = ?", (note["note_id"],)).fetchone()
    assert row["author"] == "agent"
    with pytest.raises(TypeError):
        tools.write_scratchpad(conn, a, "nas", "x", author="person")
    # Nor can they be edited from the page as if they were the person's.
    r = web.post("/api/scratchpad/notes", json={"board": "nas", "text": "edited", "replaces": note["note_id"]})
    assert r.status_code == 400


def test_one_person_never_reaches_anothers_scratchpad(conn, tools, web, alice):
    bob = conn.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()[0]
    b, _ = _session(conn, bob, "bobs-laptop")
    theirs = _call(conn, tools, b, "write_scratchpad", board="nas", text="bob's", agent="bob-cc")["note_id"]
    assert web.get("/api/scratchpad/feed", params={"board": "nas"}).json()["notes"] == []
    assert web.patch(f"/api/scratchpad/notes/{theirs}", json={"action": "resolve"}).status_code == 404
    assert web.delete(f"/api/scratchpad/notes/{theirs}").status_code == 404
    assert web.post("/api/scratchpad/notes", json={"board": "nas", "text": "x", "reply_to": theirs}).status_code == 404
    _direct(web, "nas", "alice's direction")
    _call(conn, tools, b, "read_scratchpad", board="nas", agent="bob-cc")
    assert "directions" not in _call(conn, tools, b, "list_libraries")


def test_a_failing_check_never_breaks_the_tool(conn, tools, alice, monkeypatch):
    a, _ = _session(conn, alice, "laptop")
    monkeypatch.setattr(scratchpad, "directions_for", lambda *a, **k: 1 / 0)
    out = _call(conn, tools, a, "list_libraries")
    assert "libraries" in out and "directions" not in out


# -------------------------------------------------------------------- page
def test_the_page_feed_and_changes(conn, tools, web, alice):
    assert web.get("/scratchpad").status_code == 200
    a, _ = _session(conn, alice, "laptop")
    agent_note = _call(conn, tools, a, "write_scratchpad", board="nas", text="Step 1 done.", agent="cc-a")["note_id"]
    _call(conn, tools, a, "claim_note", note_id=agent_note, agent="cc-a")
    first = web.get("/api/scratchpad/feed", params={"board": "nas"}).json()
    assert [b["board"] for b in first["boards"]] == ["nas"] and first["notes"][0]["claimed_by"] == "cc-a"
    assert first["sessions"][0]["holds"] == [agent_note]

    mine = _direct(web, "nas", "Status?", reply_to=agent_note)
    assert web.patch(f"/api/scratchpad/notes/{agent_note}", json={"action": "release"}).json().get("claimed_by") is None
    later = web.get("/api/scratchpad/feed", params={"board": "nas", "after": first["cursor"]}).json()
    assert {n["note_id"] for n in later["notes"]} == {agent_note, mine}
    assert later["boards"][0]["directions"] == 1
    edited = _direct(web, "nas", "Status, please.", replaces=mine)
    assert conn.execute("SELECT status FROM scratchpad_notes WHERE id = ?", (mine,)).fetchone()[0] == "superseded"
    assert web.patch(f"/api/scratchpad/notes/{edited}", json={"action": "resolve"}).json()["status"] == "done"
    assert web.patch(f"/api/scratchpad/notes/{edited}", json={"action": "explode"}).status_code == 422

    assert web.delete(f"/api/scratchpad/notes/{agent_note}").json() == {"status": "archived"}
    gone = web.get("/api/scratchpad/feed", params={"board": "nas", "after": later["cursor"]}).json()
    assert agent_note in gone["gone"]


# ----------------------------------------------------------------- archive
def test_what_falls_off_is_archived_and_searchable(conn, settings, tools, web, alice):
    a, _ = _session(conn, alice, "laptop")
    old = _call(conn, tools, a, "write_scratchpad", board="nas", text="rsync of the photos share done",
                agent="cc-a")["note_id"]
    keep = _call(conn, tools, a, "write_scratchpad", board="nas", text="still current", agent="cc-a")["note_id"]
    conn.execute("UPDATE scratchpad_notes SET updated_at = datetime('now', '-100 days') WHERE id = ?", (old,))
    assert retention.purge(conn, settings)["scratchpad_notes"] == 1
    _call(conn, tools, a, "write_scratchpad", board="tv", text="cleared later", agent="cc-a")
    assert web.delete("/api/scratchpad", params={"board": "tv"}).json()["count"] == 1

    found = web.get("/api/scratchpad/archive", params={"q": "photos rsync"}).json()["notes"]
    assert [(n["note_id"], n["reason"]) for n in found] == [(old, "expired")]
    every = web.get("/api/scratchpad/archive").json()["notes"]
    assert [n["reason"] for n in every] == ["cleared", "expired"]
    assert conn.execute("SELECT id FROM scratchpad_notes").fetchall()[0][0] == keep
    assert web.get("/api/scratchpad/archive", params={"q": "%"}).json()["notes"] == []      # LIKE is escaped


# -------------------------------------------------------- Claude Code hook
def test_the_hook_endpoint(conn, web, alice):
    _, token = _session(conn, alice, "laptop")
    web.cookies.clear()
    auth = {"Authorization": f"Bearer {token}"}
    assert web.get("/api/scratchpad/directions", params={"agent": "cc-a"}).status_code == 401
    r = web.get("/api/scratchpad/directions", params={"agent": "cc-a", "board": "nas", "event": "SessionStart"},
                headers=auth)
    assert r.json() == {"agent": "cc-a", "new": [], "open": []}
    web.post("/login", data={"username": "alice", "password": "password123"})
    stop = _direct(web, "nas", "Stop and hand over.", urgent=True)
    web.cookies.clear()
    r = web.get("/api/scratchpad/directions", params={"agent": "cc-a", "board": "nas"}, headers=auth).json()
    assert [d["note_id"] for d in r["new"]] == [stop] and [d["note_id"] for d in r["open"]] == [stop]
    again = web.get("/api/scratchpad/directions", params={"agent": "cc-a"}, headers=auth).json()
    assert again["new"] == [] and [d["note_id"] for d in again["open"]] == [stop]     # seen, but still open
    sessions = conn.execute("SELECT last_tool FROM scratchpad_sessions WHERE agent = 'cc-a'").fetchall()
    assert {s[0] for s in sessions} == {"hook:check"}
    # A sync token (the backup app's) doesn't work here.
    sync_token = api_tokens.create(conn, alice, "Backup", scope="sync")[1]
    assert web.get("/api/scratchpad/directions", params={"agent": "x"},
                   headers={"Authorization": f"Bearer {sync_token}"}).status_code == 401


def test_directions_ride_along_through_the_real_connector(conn, settings, alice):
    _, token = _session(conn, alice, "laptop")
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "Authorization": f"Bearer {token}"}
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as client:
        def rpc(method, params, id_=1):
            r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": id_, "method": method,
                                                           "params": params})
            assert r.status_code == 200, r.text
            body = r.text if r.headers.get("content-type", "").startswith("application/json") else \
                r.text.split("data: ", 1)[1]
            return json.loads(body)

        rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "pytest", "version": "1"}})
        rpc("tools/call", {"name": "read_scratchpad", "arguments": {"board": "nas", "agent": "cc-a"}}, 2)
        client.post("/login", data={"username": "alice", "password": "password123"})
        note = client.post("/api/scratchpad/notes", json={"board": "nas", "text": "Push now."}).json()["note_id"]
        client.cookies.clear()
        out = rpc("tools/call", {"name": "list_libraries", "arguments": {}}, 3)["result"]
        payload = out.get("structuredContent") or json.loads(out["content"][0]["text"])
        assert [d["note_id"] for d in payload["directions"]] == [note]
