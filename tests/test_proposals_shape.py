"""1.0.1, phases A and B: every proposing tool returns one shape (proposal_id, summary, expires_at, status,
approve), proposals expire instead of looking withdrawn, dismissing carries a note, Claude can withdraw its
own, and nothing Claude can call approves, applies or rejects a proposal."""

import json
import re

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import apis, api_tokens, proposals
from mnemerynne.mcp_server import Tools, build_server
from mnemerynne.web.app import Services, create_app
from test_api_actions import _atera, _upstream  # noqa: F401
from test_mcp_consistency import _add, _folder, _web, tools, world  # noqa: F401 (fixtures)


@pytest.fixture
def directory(conn, world):
    """alice is in the Staff group the Atera API is open to."""
    conn.execute("UPDATE users SET ad_account = 'alice' WHERE id = ?", (world["alice"],))
    return FakeDirectory({"alice": {"Staff"}})


@pytest.fixture
def api_tools(settings, directory):
    return Tools(settings, lambda: FakeEmbedder(), lambda: directory)


@contextmanager
def _web_as(settings, name, directory):
    # public_url is https in these tests, so the session cookie is HTTPS-only: talk https.
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory)),
                    base_url="https://mnemerynne.test") as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


@pytest.fixture(autouse=True)
def _fresh():
    apis.clear_cache()
    yield
    apis.TRANSPORT = None


def _caller(conn, user_id):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "t", can_edit=True)[1])


SHAPE = {"proposal", "proposal_id", "summary", "expires_at", "status", "approve", "approver", "requested_via",
         "created_at", "note"}


def test_all_proposing_tools_return_the_same_shape(conn, settings, world, api_tools, directory):
    tools = api_tools
    settings.public_url = "https://mnemerynne.test"
    seen = []
    _upstream(seen)
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    alice = _caller(conn, world["alice"])
    fid = _add(conn, settings, world["ops"], "old.md", user=world["alice"])

    api = tools.propose_api_action(conn, alice, "Atera (read-write)", "Change ticket status",
                                   {"ticketId": 4812, "status": "Resolved"})[0]
    delete = tools.delete_item(conn, alice, file_id=fid)[0]
    _folder(conn, world["alice"], world["inbox"], "Notes")
    access = tools.request_edit_access(conn, alice, library="File box", folder="Notes")[0]
    for out in (api, delete, access):
        assert SHAPE <= set(out), out
        assert out["proposal_id"] == out["proposal"] and out["status"] == "proposed"
        assert out["approve"] == f"https://mnemerynne.test/proposals#p{out['proposal']}"
        assert out["expires_at"].endswith(" UTC") and out["requested_via"] == "Claude for alice"
        assert "summary" in out["note"]
    assert api["summary"] == "Change ticket status in Atera (read-write): ticketId 4812, status Resolved"
    assert delete["summary"] == "Move the file “old.md” to the recycle bin"
    assert access["summary"].startswith("Let the AI edit")
    assert seen == []                                                  # nothing ran

    # The approve page shows the same sentence, who asked, and when it expires.
    with _web_as(settings, "alice", directory) as web:
        cards = {p["id"]: p for p in web.get("/api/agent/proposals").json()["proposals"]}
        assert cards[api["proposal"]]["summary"] == api["summary"]
        assert cards[api["proposal"]]["requested_via"] == "Claude for alice"
        assert cards[api["proposal"]]["expires_at"] == api["expires_at"][:-4]


def test_expiry_is_proposal_days_and_expired_is_its_own_status(conn, settings, world, tools):
    settings.proposal_days = 3
    alice = _caller(conn, world["alice"])
    fid = _add(conn, settings, world["ops"], "old.md", user=world["alice"])
    out = tools.delete_item(conn, alice, file_id=fid)[0]
    row = conn.execute("SELECT created_at, expires_at FROM agent_proposals WHERE id = ?", (out["proposal"],)).fetchone()
    assert conn.execute("SELECT datetime(?, '+3 days') = ?", (row["created_at"], row["expires_at"])).fetchone()[0]
    assert proposals.purge(conn, settings) == 0                        # not yet
    conn.execute("UPDATE agent_proposals SET expires_at = datetime('now', '-1 minute') WHERE id = ?", (out["proposal"],))
    assert proposals.purge(conn, settings) == 1
    seen = tools.get_proposal(conn, alice, out["proposal"])[0]
    assert seen["status"] == "expired" and "expired" in seen["note"] and "approve" not in seen and "expires_at" not in seen
    listed = tools.list_proposals(conn, alice, status="decided")[0]["proposals"]
    assert [p["status"] for p in listed] == ["expired"]
    # A proposal from before there were expiry dates still expires, after version_days.
    conn.execute("UPDATE agent_proposals SET status = 'pending', expires_at = NULL, decided_at = NULL, "
                 "created_at = datetime('now', '-40 days') WHERE id = ?", (out["proposal"],))
    assert proposals.purge(conn, settings) == 1


def test_dismiss_with_a_note_and_withdraw_from_claude(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    a = _add(conn, settings, world["ops"], "a.md", b"# A\n\nfirst\n", user=world["alice"])
    b = _add(conn, settings, world["ops"], "b.md", b"# B\n\nsecond\n", user=world["alice"])
    first = tools.delete_item(conn, alice, file_id=a)[0]
    second = tools.delete_item(conn, alice, file_id=b)[0]

    with _web(settings, "alice") as web:
        gone = web.post(f"/api/agent/proposals/{first['proposal']}/dismiss", json={"note": "Keep it, we need it."}).json()
        assert gone["status"] == "dismissed" and gone["decision_note"] == "Keep it, we need it." and gone["decided_by"] == "alice"
    seen = tools.get_proposal(conn, alice, first["proposal"])[0]
    assert seen["status"] == "dismissed" and seen["decision_note"] == "Keep it, we need it." and "note says why" in seen["note"]

    taken = tools.withdraw_proposal(conn, alice, second["proposal"])[0]
    assert taken["status"] == "dismissed" and taken["decision_note"] == "withdrawn by Claude" and "approve" not in taken
    with pytest.raises(ToolError, match="already dismissed"):
        tools.withdraw_proposal(conn, alice, second["proposal"])
    rob = _caller(conn, world["rob"])
    with pytest.raises(ToolError, match="No proposal"):
        tools.withdraw_proposal(conn, rob, second["proposal"])
    assert [tuple(r) for r in conn.execute("SELECT deleted_at FROM files WHERE id IN (?, ?)", (a, b))] == [(None,), (None,)]

    # list_proposals since a time
    assert tools.list_proposals(conn, alice, status="decided", since="2000-01-01")[0]["proposals"]
    with pytest.raises(ToolError, match="UTC time"):
        tools.list_proposals(conn, alice, since="yesterday")


def test_applied_api_action_reports_its_result_and_who_applied(conn, settings, world, api_tools, directory):
    tools = api_tools
    settings.public_url = "https://mnemerynne.test"
    seen = []
    _upstream(seen)
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    alice = _caller(conn, world["alice"])
    out = tools.propose_api_action(conn, alice, "Atera (read-write)", "Resolve alert", {"alertId": 5531})[0]
    with _web_as(settings, "alice", directory) as web:
        done = web.post(f"/api/agent/proposals/{out['proposal']}/apply").json()
        assert done["status"] == "applied" and done["decided_by"] == "alice" and done["expires_at"] is None
    seen_out = tools.get_proposal(conn, alice, out["proposal"])[0]
    assert seen_out["status"] == "applied" and seen_out["decided_by"] == "alice"
    assert seen_out["link"].startswith("https://mnemerynne.test/apis/calls/") and seen_out["result"]


def test_no_tool_can_approve_apply_or_reject(settings, conn, world, tools):
    server = build_server(settings, lambda: None)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert not any(re.search(r"approve|apply|reject|decide", n) for n in names), names
    assert "withdraw_proposal" in names
    annotations = {t.name: t.annotations for t in server._tool_manager.list_tools()}
    for name in ("propose_api_action", "delete_item", "request_edit_access", "withdraw_proposal"):
        assert annotations[name].read_only_hint is False and annotations[name].destructive_hint is False
    assert annotations["request_edit_access"].idempotent_hint is True
    assert annotations["propose_api_action"].idempotent_hint is False
    read_only = {n for n, a in annotations.items() if a.read_only_hint}
    assert {"search_documents", "list_proposals", "get_proposal", "query_api", "open_file"} <= read_only

    # And proposing through the tools leaves the row pending until the person applies it on the web.
    alice = _caller(conn, world["alice"])
    fid = _add(conn, settings, world["ops"], "x.md", user=world["alice"])
    out = tools.delete_item(conn, alice, file_id=fid)[0]
    assert conn.execute("SELECT status FROM agent_proposals WHERE id = ?", (out["proposal"],)).fetchone()[0] == "pending"
    with _web(settings, "alice") as web:
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").json()["status"] == "applied"
    assert conn.execute("SELECT deleted_at IS NOT NULL FROM files WHERE id = ?", (fid,)).fetchone()[0]


# ------------------------------------------------------------------ phase D: batches and audit
def _ops(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM api_operations")}


def test_a_batch_is_one_proposal_with_one_link_and_the_person_ticks_what_runs(conn, settings, world, api_tools,
                                                                                directory):
    tools = api_tools
    settings.public_url = "https://mnemerynne.test"
    seen = []
    _upstream(seen)
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    alice = _caller(conn, world["alice"])
    items = [{"api": "Atera (read-write)", "operation": "Change ticket status",
              "params": {"ticketId": 4800 + n, "status": "Resolved"}} for n in range(10)]
    out = tools.propose_batch(conn, alice, items)[0]
    assert out["status"] == "proposed" and out["approve"] == f"https://mnemerynne.test/proposals#p{out['proposal']}"
    assert out["summary"].startswith("10 changes in Atera (read-write): Change ticket status")
    assert len(out["items"]) == 10 and all(i["status"] == "pending" for i in out["items"])
    assert seen == []

    with _web_as(settings, "alice", directory) as web:
        card = next(p for p in web.get("/api/agent/proposals").json()["proposals"] if p["id"] == out["proposal"])
        assert card["kind"] == "batch" and len(card["items"]) == 10 and card["counts"]["pending"] == 10
        done = web.post(f"/api/agent/proposals/{out['proposal']}/apply", json={"items": list(range(8))}).json()
        assert done["status"] == "applied" and done["counts"] == {"pending": 0, "applied": 8, "declined": 2, "failed": 0}
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 400     # once only
    assert len(seen) == 8 and {s.url.path for s in seen} == {f"/api/v3/tickets/{4800 + n}" for n in range(8)}

    seen_out = tools.get_proposal(conn, alice, out["proposal"])[0]
    assert seen_out["status"] == "applied" and "8 applied, 2 declined, 0 failed" in seen_out["note"]
    assert [i["status"] for i in seen_out["items"]] == ["applied"] * 8 + ["declined"] * 2
    assert all(i["result"] for i in seen_out["items"][:8])
    log = [tuple(r) for r in conn.execute("SELECT action, target FROM access_log WHERE action LIKE 'proposal.%'")]
    assert log == [("proposal.apply", seen_out["summary"][:200])]


def test_a_bad_item_refuses_the_whole_batch(conn, settings, world, api_tools):
    tools = api_tools
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    alice = _caller(conn, world["alice"])
    good = {"api": "Atera (read-write)", "operation": "Resolve alert", "params": {"alertId": 1}}
    with pytest.raises(ToolError, match="Item 2"):
        tools.propose_batch(conn, alice, [good, {"api": "Atera (read-write)", "operation": "Ticket", "params": {"ticketId": 1}}])
    with pytest.raises(ToolError, match="Item 2"):
        tools.propose_batch(conn, alice, [good, {"api": "Atera (read-write)", "operation": "Resolve alert", "params": {}}])
    with pytest.raises(ToolError, match="at least one"):
        tools.propose_batch(conn, alice, [])
    settings.proposal_batch_max = 1
    with pytest.raises(ToolError, match="at most 1"):
        tools.propose_batch(conn, alice, [good, good])
    assert conn.execute("SELECT count(*) FROM agent_proposals").fetchone()[0] == 0


def test_dismissing_is_logged_with_the_note(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    fid = _add(conn, settings, world["ops"], "note.md", user=world["alice"])
    out = tools.delete_item(conn, alice, file_id=fid)[0]
    with _web(settings, "alice") as web:
        web.post(f"/api/agent/proposals/{out['proposal']}/dismiss", json={"note": "Still needed."})
    row = conn.execute("SELECT action, detail FROM access_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action"] == "proposal.dismiss" and row["detail"].endswith("(Claude for alice): Still needed.")


# ------------------------------------------------------------------ phase C: approval in the chat
class _Session:
    """A client session as the tool wrapper sees it: capabilities, a back-channel, and what it answers."""

    def __init__(self, modes=("url",), back_channel=True, answer="accept", on_elicit=None):
        import mcp.types as types
        self.client_capabilities = types.ClientCapabilities.model_validate(
            {"elicitation": {m: {} for m in modes}} if modes else {})
        self.client_params = types.InitializeRequestParams.model_validate(
            {"protocolVersion": "2025-06-18", "capabilities": self.client_capabilities.model_dump(),
             "clientInfo": {"name": "fake", "version": "1"}})
        self.can_send_request = back_channel
        self.answer = answer
        self.on_elicit = on_elicit
        self.asked = []

    async def elicit_url(self, message, url, elicitation_id):
        import mcp.types as types
        self.asked.append((message, url, elicitation_id))
        if self.on_elicit:
            self.on_elicit()
        return types.ElicitResult(action=self.answer)


class _Ctx:
    def __init__(self, session):
        self.session = session


def test_the_approve_page_is_offered_in_the_chat_when_the_client_can_show_it(conn, settings, world, tools):
    import anyio
    from mnemerynne import mcp_server

    settings.public_url = "http://mnemerynne.test"      # http: the plain test client's cookie must work
    alice = _caller(conn, world["alice"])
    fid = _add(conn, settings, world["ops"], "gone.md", user=world["alice"])
    out = tools.delete_item(conn, alice, file_id=fid)[0]

    # The person approves on the page the client opened: the tool then reports it applied.
    def approve_on_the_page():
        with _web(settings, "alice") as web:
            assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").json()["status"] == "applied"
    session = _Session(on_elicit=approve_on_the_page)
    got = anyio.run(mcp_server.offer_approval, _Ctx(session), tools, alice, dict(out))
    assert session.asked == [(out["summary"], out["approve"], f"proposal-{out['proposal']}")]
    assert got["status"] == "applied" and got["elicited"] == "accept" and "approve" not in got and got["decided_by"] == "alice"

    # Closed without deciding: still pending, the link still works.
    fid2 = _add(conn, settings, world["ops"], "later.md", b"# Later\n", user=world["alice"])
    out2 = tools.delete_item(conn, alice, file_id=fid2)[0]
    session = _Session(answer="cancel")
    got = anyio.run(mcp_server.offer_approval, _Ctx(session), tools, alice, dict(out2))
    assert got["status"] == "proposed" and got["approve"] == out2["approve"] and "still works" in got["note"]

    # No URL mode, or no back-channel (the stateless endpoint): nothing is asked, the answer is as before.
    for session in (_Session(modes=("form",)), _Session(modes=()), _Session(back_channel=False)):
        got = anyio.run(mcp_server.offer_approval, _Ctx(session), tools, alice, dict(out2))
        assert session.asked == [] and got == out2
