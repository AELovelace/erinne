"""The agent scratchpad: boards of notes a person's own agents (their Claude Code sessions, Desktop,
claude.ai) leave for each other, with claims so two sessions don't take the same task, lapsing claims,
resolve and replace, the briefing line, Preferences' Clear, and retention."""

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import api_tokens, briefings, retention, scratchpad, users
from mnemerynne.mcp_server import Refused, Tools
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def people(conn):
    return {name: users.create_user(conn, name, "password123") for name in ("alice", "bob")}


@pytest.fixture
def tools(settings):
    return Tools(settings, lambda: FakeEmbedder(), lambda: FakeDirectory({}, groups=[]))


def _caller(conn, user_id, name="laptop", can_edit=True):
    """A connection of the person's: a token named after the machine, as Claude Code or Desktop would have."""
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, name, can_edit=can_edit)[1])


# ------------------------------------------------------------- write and read
def test_agents_jot_and_read_notes_on_boards(conn, people, tools):
    laptop = _caller(conn, people["alice"], "laptop")
    assert tools.read_scratchpad(conn, laptop)[0]["boards"] == []                      # nothing yet
    first = tools.write_scratchpad(conn, laptop, "NAS Migration", "Steps 1-3 done; step 4 (rsync) next.")[0]
    assert first["board"] == "nas-migration" and first["agent"] == "laptop #1"         # the token signs it
    desktop = _caller(conn, people["alice"], "desktop")
    tools.write_scratchpad(conn, desktop, "nas-migration", "Leave the backup job alone.", agent="cc-desktop")

    boards = tools.read_scratchpad(conn, laptop)[0]["boards"]
    assert [(b["board"], b["open"], b["claimed"]) for b in boards] == [("nas-migration", 2, 0)]
    read = tools.read_scratchpad(conn, desktop, "nas-migration")[0]
    assert [(n["agent"], n["text"]) for n in read["notes"]] == [
        ("laptop #1", "Steps 1-3 done; step 4 (rsync) next."), ("cc-desktop", "Leave the backup job alone.")]
    assert read["you"] == "desktop #2" and "Only the person's own connections" in read["note"]

    # Another person sees none of it, and can't reach the notes by id.
    bob = _caller(conn, people["bob"], "laptop")
    assert tools.read_scratchpad(conn, bob)[0]["boards"] == []
    assert tools.read_scratchpad(conn, bob, "nas-migration")[0]["notes"] == []
    with pytest.raises(Refused, match="no note") as exc:
        tools.resolve_note(conn, bob, first["note_id"])
    assert exc.value.code == "not_found"


def test_since_include_done_and_limits(conn, people, tools):
    me = _caller(conn, people["alice"])
    old = tools.write_scratchpad(conn, me, "b", "old")[0]["note_id"]
    conn.execute("UPDATE scratchpad_notes SET updated_at = datetime('now', '-2 days') WHERE id = ?", (old,))
    new = tools.write_scratchpad(conn, me, "b", "new")[0]["note_id"]
    assert [n["note_id"] for n in tools.read_scratchpad(conn, me, "b", since="2020-01-01")[0]["notes"]] == [old, new]
    yesterday = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 day')").fetchone()[0]
    assert [n["note_id"] for n in tools.read_scratchpad(conn, me, "b", since=yesterday)[0]["notes"]] == [new]
    tools.resolve_note(conn, me, old)
    assert [n["note_id"] for n in tools.read_scratchpad(conn, me, "b")[0]["notes"]] == [new]
    done = tools.read_scratchpad(conn, me, "b", include_done=True)[0]["notes"]
    assert [(n["note_id"], n["status"]) for n in done] == [(old, "done"), (new, "open")]
    assert len(tools.read_scratchpad(conn, me, "b", include_done=True, limit=1)[0]["notes"]) == 1
    with pytest.raises(Refused, match="since") as exc:
        tools.read_scratchpad(conn, me, "b", since="last tuesday")
    assert exc.value.code == "invalid"


def test_bad_boards_long_notes_and_read_only_tokens_refuse(conn, people, tools):
    me = _caller(conn, people["alice"])
    for board in ("", "a/b", "x" * 61):
        with pytest.raises(Refused, match="board name") as exc:
            tools.write_scratchpad(conn, me, board, "hi")
        assert exc.value.code == "invalid"
    with pytest.raises(Refused, match="under 4000"):
        tools.write_scratchpad(conn, me, "b", "x" * 4001)
    with pytest.raises(Refused, match="Write something"):
        tools.write_scratchpad(conn, me, "b", "   ")
    reader = _caller(conn, people["alice"], "ro", can_edit=False)
    note = tools.write_scratchpad(conn, me, "b", "hi")[0]["note_id"]
    assert tools.read_scratchpad(conn, reader, "b")[0]["notes"][0]["note_id"] == note   # reading still works
    for fn, args in ((tools.write_scratchpad, ("b", "no")), (tools.claim_note, (note,)), (tools.resolve_note, (note,))):
        with pytest.raises(Exception, match="only search"):
            fn(conn, reader, *args)


# ------------------------------------------------------------------- claims
def test_claims_conflict_release_and_lapse(conn, people, tools, settings):
    a = _caller(conn, people["alice"], "laptop")
    b = _caller(conn, people["alice"], "desktop")
    task = tools.write_scratchpad(conn, a, "b", "Step 4: rsync to the array")[0]["note_id"]
    claimed = tools.claim_note(conn, a, task, agent="cc-laptop")[0]
    assert claimed["claimed_by"] == "cc-laptop" and "Claimed for cc-laptop" in claimed["note"]
    with pytest.raises(Refused, match="claimed by cc-laptop") as exc:
        tools.claim_note(conn, b, task, agent="cc-desktop")
    assert exc.value.code == "conflict"
    assert tools.claim_note(conn, a, task, agent="cc-laptop")[0]["claimed_by"] == "cc-laptop"   # re-claim: fine
    assert tools.read_scratchpad(conn, b)[0]["boards"][0]["claimed"] == 1
    with pytest.raises(Refused, match="not by cc-desktop") as exc:
        tools.claim_note(conn, b, task, agent="cc-desktop", release=True)
    assert exc.value.code == "conflict"
    released = tools.claim_note(conn, a, task, agent="cc-laptop", release=True)[0]
    assert "claimed_by" not in released and released["note"] == "Released."

    # A claim older than the timeout lapses on the next read, so a session that died doesn't block others.
    tools.claim_note(conn, a, task, agent="cc-laptop")
    conn.execute("UPDATE scratchpad_notes SET claimed_at = datetime('now', ?) WHERE id = ?",
                 (f"-{settings.scratchpad_claim_minutes + 1} minutes", task))
    assert "claimed_by" not in tools.read_scratchpad(conn, b, "b")[0]["notes"][0]
    assert tools.claim_note(conn, b, task, agent="cc-desktop")[0]["claimed_by"] == "cc-desktop"

    # Resolving clears the claim; a done note can't be claimed until reopened.
    done = tools.resolve_note(conn, b, task)[0]
    assert done["status"] == "done" and "claimed_by" not in done and done["done_at"]
    with pytest.raises(Refused, match="only open notes"):
        tools.claim_note(conn, a, task, agent="cc-laptop")
    assert tools.resolve_note(conn, a, task, done=False)[0]["status"] == "open"
    with pytest.raises(Refused, match="no note") as exc:
        tools.claim_note(conn, a, 999, agent="cc-laptop")
    assert exc.value.code == "not_found"


def test_replaces_retires_the_older_note(conn, people, tools):
    me = _caller(conn, people["alice"])
    first = tools.write_scratchpad(conn, me, "b", "step 4 next")[0]["note_id"]
    tools.claim_note(conn, me, first)
    second = tools.write_scratchpad(conn, me, "b", "step 5 next", replaces=first)[0]["note_id"]
    assert [n["note_id"] for n in tools.read_scratchpad(conn, me, "b")[0]["notes"]] == [second]
    history = tools.read_scratchpad(conn, me, "b", include_done=True)[0]["notes"]
    assert [(n["note_id"], n["status"]) for n in history] == [(first, "superseded"), (second, "open")]
    assert "claimed_by" not in history[0]
    with pytest.raises(Refused, match="replaced by a newer note"):
        tools.resolve_note(conn, me, first)
    with pytest.raises(Refused, match="on board b, not c"):
        tools.write_scratchpad(conn, me, "c", "elsewhere", replaces=second)


def test_the_default_label_is_the_connections_name(conn, people, tools):
    me = people["alice"]
    token = _caller(conn, me, "Claude Desktop")
    assert tools.write_scratchpad(conn, token, "b", "hi")[0]["agent"] == f"Claude Desktop #{token.token_id}"
    # An OAuth connection (claude.ai, Claude Code) signs with the client's name instead.
    row = conn.execute("INSERT INTO oauth_clients(client_id, name, redirect_uris) VALUES ('c1', 'Claude Code', '[]')")
    grant = conn.execute("INSERT INTO oauth_grants(user_id, client_row, can_edit) VALUES (?, ?, 1)",
                         (me, row.lastrowid)).lastrowid
    via_oauth = api_tokens.Caller(me, "alice", None, True, grant_id=grant)
    assert tools.write_scratchpad(conn, via_oauth, "b", "hi")[0]["agent"] == f"Claude Code #{grant}"
    assert tools.write_scratchpad(conn, token, "b", "hi", agent="  my\tlabel  ")[0]["agent"] == "my label"


# --------------------------------------------------- briefing, web, retention
def test_the_briefing_and_preferences_carry_open_notes(settings, conn, people, tools):
    me = _caller(conn, people["alice"])
    user = users.get_user(conn, "alice")
    assert "scratchpad" not in briefings.briefing(conn, user, None)                   # quiet when empty
    task = tools.write_scratchpad(conn, me, "nas-migration", "step 4")[0]["note_id"]
    tools.claim_note(conn, me, task, agent="cc-laptop")
    pad = tools.my_briefing(conn, me)[0]["scratchpad"]
    assert pad["open"] == 1 and pad["boards"][0]["board"] == "nas-migration"
    assert [(c["note_id"], c["claimed_by"]) for c in pad["claims"]] == [(task, "cc-laptop")]

    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=None))) as web:
        web.post("/login", data={"username": "alice", "password": "password123"})
        page = web.get("/preferences").text
        assert "1 open note on 1 board" in page and "nas-migration" in page and "cc-laptop" in page
        assert web.get("/api/scratchpad").json()["open"] == 1
        assert web.delete("/api/scratchpad").json() == {"status": "deleted", "count": 1}
        assert "Nothing open." in web.get("/preferences").text
    assert tools.read_scratchpad(conn, me)[0]["boards"] == []


def test_retention_purges_notes_nobody_touched(conn, people, settings):
    me = people["alice"]
    kept = scratchpad.write(conn, me, "b", "fresh", "laptop")
    stale = scratchpad.write(conn, me, "b", "forgotten", "laptop")
    conn.execute("UPDATE scratchpad_notes SET updated_at = datetime('now', ?) WHERE id = ?",
                 (f"-{settings.scratchpad_days + 1} days", stale))
    assert retention.purge(conn, settings)["scratchpad_notes"] == 1
    assert [n["note_id"] for n in scratchpad.notes(conn, me, "b")] == [kept]
    assert scratchpad.clear(conn, me, "b") == 1 and scratchpad.boards(conn, me) == []
