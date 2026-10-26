"""API actions: operations that change data are only proposed, and run when the person applies them."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from mnemerynne import api_tokens, apis
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.mcp_server import Tools
from mcp.server.mcpserver.exceptions import ToolError
from mnemerynne.web.app import Services, create_app
from test_agent import GROUP, AgentLLM, agent_llm, client, directory, world  # noqa: F401 (fixtures)
from test_chat import _ask, _final, _login

ATERA = "api-templates/atera-read-write.json"


@pytest.fixture(autouse=True)
def _fresh():
    apis.clear_cache()
    yield
    apis.TRANSPORT = None


def _upstream(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(204) if request.method == "PUT" else httpx.Response(200, json={"TicketID": 4812})
    apis.TRANSPORT = httpx.MockTransport(handler)


def _atera(conn, settings, group="Staff"):
    """The read-write Atera template as an API alice may use (she's in Staff)."""
    t = apis.parse_template(open(ATERA, encoding="utf-8").read())
    api_id = conn.execute("INSERT INTO apis(name, base_url, auth_type, ad_group, secret_enc) VALUES (?, ?, 'bearer', ?, ?)",
                          (t["name"], t["base_url"], group, apis.encrypt_secret(settings, "tok"))).lastrowid
    ids = {}
    for op in t["operations"]:
        ids[op["name"]] = conn.execute(
            "INSERT INTO api_operations(api_id, name, description, method, path, params, body, cache_seconds, action) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (api_id, op["name"], op["description"], op["method"], op["path"], json.dumps(op["params"]), op["body"],
             op["cache_seconds"], int(op["action"]))).lastrowid
    return api_id, ids


# ------------------------------------------------------------------- rules
def test_methods_that_change_data_must_be_actions():
    with pytest.raises(apis.ApiError, match="mark the operation as an action"):
        apis.check_specs("/tickets/{id}", [{"name": "id", "type": "int", "in": "path"}], "PUT", "")
    apis.check_specs("/alerts/{id}", [{"name": "id", "type": "int", "in": "path"}], "PUT", "", action=True)
    with pytest.raises(apis.ApiError, match="free text"):
        apis.check_specs("/x", [{"name": "t", "type": "text", "in": "body"}], "POST", '{"a": "{{t}}"}')
    with pytest.raises(apis.ApiError, match="need a body"):
        apis.check_specs("/x", [{"name": "t", "type": "text", "in": "body"}], "PUT", "", action=True)
    specs = [{"name": "t", "type": "text", "in": "body"}]
    assert apis.validate_params(specs, {"t": "Replaced the printer's toner.\nAll good!"})["t"].endswith("good!")
    with pytest.raises(apis.ApiError, match="plain text"):
        apis.validate_params(specs, {"t": "x" * 2001})


def test_actions_never_run_from_chat_or_claude_and_are_never_cached(conn, settings, world):
    seen = []
    _upstream(seen)
    _, ids = _atera(conn, settings)
    for via in ("chat", "claude"):
        with pytest.raises(apis.ApiError, match="runs only when the person applies it"):
            apis.call(conn, settings, ids["Resolve alert"], {"alertId": 5531}, user_id=None, via=via)
    assert seen == []
    for _ in range(2):
        assert apis.call(conn, settings, ids["Resolve alert"], {"alertId": 5531}, user_id=None, via="apply").ok
    assert len(seen) == 2 and seen[0].method == "PUT" and seen[0].url.path == "/api/v3/alerts/5531"
    assert seen[0].content == b""                                              # no body for this one


# -------------------------------------------------------------------- chat
def test_erynne_proposes_and_the_person_runs_it(client, agent_llm, conn, settings, world):
    seen = []
    _upstream(seen)
    _, ids = _atera(conn, settings)
    _login(client, "alice")
    listed = [t for t in client.get("/api/me/tools").json()["tools"] if t["group"] == "Internal systems"]
    assert {t["subgroup"] for t in listed} == {"Atera (read-write)"}           # one section per API
    assert {t["label"]: t["write"] for t in listed}["Change ticket status"] is True
    agent_llm.turns(ToolTurn("", [ToolCall("c1", "atera_read_write_change_ticket_status",
                                           {"ticketId": 4812, "status": "Resolved"})]),
                    ToolTurn("I've drafted closing ticket 4812; press Run it to do it."))
    events = _ask(client, "Close ticket 4812")
    offered = agent_llm.calls[0][1]
    assert "atera_read_write_change_ticket_status" in offered and "atera_read_write_ticket" in offered
    status_tool = next(t for t in agent_llm.calls[0][2] if t["function"]["name"] == "atera_read_write_change_ticket_status")
    assert status_tool["function"]["description"].startswith("Proposes a change in Atera (read-write)")
    proposal = next(e for e in events if e["type"] == "proposal")
    assert proposal["kind"] == "api" and proposal["changes"] == ["ticketId: 4812", "status: Resolved"]
    step = [e for e in events if e["type"] == "tool"]
    assert step[0]["label"] == "Drafting a change in Atera (read-write)" and "waiting for you" in step[1]["summary"]
    assert seen == [] and _final(events)["status"] == "answered"               # nothing ran yet

    ran = client.post(f"/api/agent/proposals/{proposal['id']}/apply")
    assert ran.status_code == 200 and ran.json()["status"] == "applied" and ran.json()["url"].startswith("/apis/calls/")
    assert seen[0].method == "PUT" and seen[0].url.path == "/api/v3/tickets/4812"
    assert json.loads(seen[0].content) == {"TicketStatus": "Resolved"}
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert conn.execute("SELECT via, ok FROM api_calls ORDER BY id DESC").fetchone()[:] == ("apply", 1)
    assert client.post(f"/api/agent/proposals/{proposal['id']}/apply").status_code == 400   # once only


def test_applying_checks_access_again(client, agent_llm, conn, settings, world):
    _upstream([])
    _, ids = _atera(conn, settings)
    _login(client, "alice")
    agent_llm.turns(ToolTurn("", [ToolCall("c1", "atera_read_write_resolve_alert", {"alertId": 5531})]),
                    ToolTurn("Drafted."))
    proposal = next(e for e in _ask(client, "Resolve alert 5531") if e["type"] == "proposal")
    conn.execute("UPDATE apis SET ad_group = 'Domain Admins'")                    # alice left the API's group
    refused = client.post(f"/api/agent/proposals/{proposal['id']}/apply")
    assert refused.status_code == 400 and "can't use that operation any more" in refused.json()["detail"]


def test_questions_are_never_routed_straight_to_an_action(client, conn, settings, world):
    seen = []
    _upstream(seen)
    _, ids = _atera(conn, settings)
    t = apis.parse_template(open(ATERA, encoding="utf-8").read())
    for op in t["operations"]:   # every operation's example questions, as an admin's save would index them
        conn.execute("UPDATE api_operations SET example_questions = ? WHERE id = ?",
                     (op["example_questions"], ids[op["name"]]))
        apis.index_examples(conn, world["embedder"], ids[op["name"]])
    _login(client, "bob")                                                       # Staff, but no agent
    final = _final(_ask(client, "Resolve alert 5531."))
    assert all(r.method == "GET" for r in seen)                                 # reads at most
    assert conn.execute("SELECT count(*) FROM agent_proposals").fetchone()[0] == 0
    assert not conn.execute("SELECT 1 FROM api_calls c JOIN api_operations o ON o.id = c.operation_id "
                            "WHERE o.action = 1").fetchone()
    assert "changes data" not in final["answer"]


# ------------------------------------------------------------- the connector
def test_claude_proposes_through_the_connector_and_the_person_approves(conn, settings, world, directory):
    seen = []
    _upstream(seen)
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    settings.public_url = "https://mnemerynne.test"
    tools = Tools(settings, lambda: None, lambda: directory)
    caller = api_tokens.verify(conn, api_tokens.create(conn, world["alice"], "laptop")[1])
    listed = tools.list_apis(conn, caller)[0]["apis"][0]["operations"]
    assert {o["operation"]: o.get("changes_data", False) for o in listed}["Resolve alert"] is True
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="use propose_api_action"):
        tools.query_api(conn, caller, "Atera (read-write)", "Resolve alert", {"alertId": 5531})
    with pytest.raises(ToolError, match="only reads"):
        tools.propose_api_action(conn, caller, "Atera (read-write)", "Ticket", {"ticketId": 1})
    out, _ = tools.propose_api_action(conn, caller, "Atera (read-write)", "Add internal note",
                                      {"ticketId": 4812, "text": "Printer's toner replaced; it's printing again."})
    assert out["approve"] == f"https://mnemerynne.test/proposals#p{out['proposal']}" and "Don't say" in out["note"]
    assert seen == []

    with TestClient(create_app(settings, Services(llm=None, embedder=world["embedder"], directory=directory)), base_url="https://mnemerynne.test") as c:
        _login(c, "bob")
        assert c.get("/api/agent/proposals").json()["proposals"] == []          # only your own
        c.post("/logout")
        _login(c, "alice")
        assert "Changes waiting for you" in c.get("/proposals").text
        pending = c.get("/api/agent/proposals").json()["proposals"]
        assert [p["id"] for p in pending] == [out["proposal"]] and pending[0]["status"] == "pending"
        assert c.post(f"/api/agent/proposals/{out['proposal']}/apply").json()["status"] == "applied"
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/v3/tickets/4812/comments"
    assert body["CommentText"] == "Printer's toner replaced; it's printing again."
    assert body["TechnicianCommentDetails"] == {"TechnicianEmail": "technician@example.com", "IsInternal": True}


def test_claude_proposes_a_new_ticket_from_a_finding(conn, settings, world, directory):
    """A security check's finding becomes a ticket: Claude proposes it with the contact looked up
    first, the person runs it; apostrophes and newlines survive, defaults fill the rest."""
    seen = []
    _upstream(seen)
    _atera(conn, settings)
    conn.execute("UPDATE apis SET claude_enabled = 1")
    settings.public_url = "https://mnemerynne.test"
    tools = Tools(settings, lambda: None, lambda: directory)
    caller = api_tokens.verify(conn, api_tokens.create(conn, world["alice"], "laptop")[1])
    tools.query_api(conn, caller, "Atera (read-write)", "Contact by email", {"Email": "jane@example.com"})
    assert seen[-1].method == "GET" and seen[-1].url.path == "/api/v3/contacts" and seen[-1].url.params["Email"] == "jane@example.com"
    with pytest.raises(ToolError, match="endUserId"):                            # never without a contact
        tools.propose_api_action(conn, caller, "Atera (read-write)", "Create ticket",
                                 {"title": "x", "description": "y"})
    out, _ = tools.propose_api_action(conn, caller, "Atera (read-write)", "Create ticket",
                                      {"title": "PrintHost's stale credential", "endUserId": 77, "priority": "high",
                                       "description": "The print service on PrintHost still runs as svc_print,\n"
                                                      "whose password was last set in 2023."})
    assert len(seen) == 1                                                        # only the lookup ran
    with TestClient(create_app(settings, Services(llm=None, embedder=world["embedder"], directory=directory)), base_url="https://mnemerynne.test") as c:
        _login(c, "alice")
        assert c.post(f"/api/agent/proposals/{out['proposal']}/apply").json()["status"] == "applied"
    assert seen[-1].method == "POST" and seen[-1].url.path == "/api/v3/tickets"
    assert json.loads(seen[-1].content) == {
        "TicketTitle": "PrintHost's stale credential", "EndUserID": 77, "TicketPriority": "High",
        "TicketType": "Incident", "TicketImpact": "Minor",
        "Description": "The print service on PrintHost still runs as svc_print,\nwhose password was last set in 2023."}


# ------------------------------------------------------------------ admins
def test_the_atera_template_and_testing_an_action(settings, conn, world, directory):
    seen = []
    _upstream(seen)
    with TestClient(create_app(settings, Services(llm=None, embedder=world["embedder"], directory=directory)), base_url="https://mnemerynne.test") as c:
        _login(c, "boss")
        conn.execute("UPDATE users SET role = 'admin' WHERE username = 'boss'")
        up = c.post("/api/admin/apis/templates", json={"template": open(ATERA, encoding="utf-8").read()}).json()
        made = c.post("/api/admin/apis", json={"preset": up["key"]}).json()
        ops = {o["name"]: o for o in c.get(f"/api/admin/apis/{made['id']}").json()["operations"]}
        assert [n for n, o in ops.items() if o["action"]] == ["Create ticket", "Change ticket status",
                                                              "Change ticket priority", "Add internal note",
                                                              "Resolve alert"]
        assert all(o["cache_seconds"] == 0 for o in ops.values() if o["action"])
        c.put(f"/api/admin/apis/{made['id']}/credential", json={"secret": "tok"})
        url = f"/api/admin/apis/operations/{ops['Resolve alert']['id']}/test"
        refused = c.post(url, json={"params": {"alertId": 1}})
        assert refused.status_code == 422 and "testing it really does it" in refused.json()["detail"] and seen == []
        assert c.post(url, json={"params": {"alertId": 1}, "really_change": True}).json()["ok"] and len(seen) == 1
        bad = {"name": "Sneaky", "method": "PUT", "path": "/tickets/1", "body": '{"a": 1}'}
        assert c.post(f"/api/admin/apis/{made['id']}/operations", json=bad).status_code == 422   # not an action
