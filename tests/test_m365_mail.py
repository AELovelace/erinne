"""Mail, calendar and drafts for the agent group, through linked Microsoft accounts, and the same for
Claude through the connector, only for the group an admin names. Nothing ever sends."""

import asyncio
import json
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from fakes import FakeDirectory, FakeEmbedder, KeywordEmbedder
from mnemerynne import accounts, api_tokens, m365, m365_mail, mcp_server, users
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.config import Settings
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from test_agent import AgentLLM, last_tool
from test_chat import FakeLLM, _ask, _final

HOME, GASCO = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
LONG_ID = "AAMkAGI2TG93AAA=" * 6


class FakeGraph:
    """Each person's mailbox and calendar. Any send, move or delete fails the test."""

    def __init__(self):
        self.calls = []
        self.drafts = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, method = str(request.url), request.method
        self.calls.append((method, url))
        path = urlsplit(url).path
        assert method in ("GET", "POST"), f"{method} {url}"
        assert not any(w in path.lower() for w in ("/send", "/move", "/delete", "/copy")), f"forbidden: {url}"
        who = request.headers.get("authorization", "").split()[-1]
        q = parse_qs(urlsplit(url).query)
        if path.endswith("/users/frontdesk@gasco.test/messages") and who != "user-alice-g":
            return httpx.Response(403, json={"error": {"message": "Access is denied."}})
        if "/mailFolders/inbox/messages" in path:
            return httpx.Response(200, json={"value": []})
        if path.endswith("/messages") and method == "GET":
            assert q["$search"][0].startswith('"boiler') or q["$search"][0] == '"boiler from:Sam"'
            return httpx.Response(200, json={"value": [
                {"id": LONG_ID, "subject": "Boiler repair", "receivedDateTime": "2026-10-20T15:04:00Z",
                 "from": {"emailAddress": {"name": "Sam GM", "address": "sam@eternal.test"}},
                 "bodyPreview": "The boiler is fixed", "webLink": "https://outlook.test/m/1"},
                {"id": "old", "subject": "Old boiler", "receivedDateTime": "2025-01-01T10:00:00Z",
                 "from": {"emailAddress": {"name": "Ann"}}, "webLink": "https://outlook.test/m/2"}]})
        if path.endswith(f"/messages/{LONG_ID}") and method == "GET":
            if "body" in q.get("$select", [""])[0]:
                assert request.headers["prefer"] == 'outlook.body-content-type="text"'
            return httpx.Response(200, json={
                "subject": "Boiler repair", "receivedDateTime": "2026-10-20T15:04:00Z", "webLink": "https://outlook.test/m/1",
                "from": {"emailAddress": {"name": "Sam GM", "address": "sam@eternal.test"}},
                "toRecipients": [{"emailAddress": {"name": "Alice"}}],
                "body": {"content": "Hi Alice,\nThe boiler is fixed; the bill is $420.\n\n-- \nSam\nGM\n\n"
                                    "From: Alice\nSent: Monday\n> old stuff"}})
        if path.endswith("/calendarView"):
            assert q["startDateTime"][0].endswith("Z") and q["endDateTime"][0] > q["startDateTime"][0]
            return httpx.Response(200, json={"value": [{
                "subject": "Owners meeting", "start": {"dateTime": "2026-10-23T16:00:00.0000000"},
                "end": {"dateTime": "2026-10-23T17:00:00.0000000"}, "location": {"displayName": "Board room"},
                "organizer": {"emailAddress": {"name": "Sam GM"}}, "attendees": [{"emailAddress": {"name": "Alice"}}],
                "webLink": "https://outlook.test/e/1"}]})
        if path.endswith("/search/query"):
            return httpx.Response(200, json={"value": [{"hitsContainers": [{"hits": [{
                "summary": "<c0>Boiler</c0> maintenance plan", "resource": {
                    "name": "Boiler plan.docx", "webUrl": "https://sp.test/boiler", "lastModifiedDateTime": "2026-09-01T00:00:00Z"}}]}]}]})
        if path.endswith(f"/messages/{LONG_ID}/createReply") or path.endswith("/me/messages") and method == "POST":
            body = json.loads(request.content)
            self.drafts.append((path, body))
            return httpx.Response(201, json={"id": "draft-1", "webLink": "https://outlook.test/drafts/1"})
        return httpx.Response(404, json={"error": {"message": "not faked: " + url}})


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(fake.handler))
    m365._user_tokens.clear()
    return fake


@pytest.fixture
def world(settings, conn, graph):
    directory = FakeDirectory({"alice": {"erynne agents"}, "bob": {"staff"}}, groups=["erynne agents", "staff"])
    ids = {n: users.create_user(conn, n, "password123") for n in ("alice", "bob")}
    for n in ids:
        accounts.link(conn, ids[n], n, directory)
    home = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Eternal', 'ad')", (HOME,)).lastrowid
    gasco = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Gasco', 'cloud')",
                         (GASCO,)).lastrowid
    identities = {}
    for name, tenant, suffix in (("alice", home, ""), ("alice", gasco, "-g"), ("bob", home, "")):
        i = conn.execute("INSERT INTO m365_identities(user_id, tenant_row, oid, upn) VALUES (?, ?, ?, ?)",
                         (ids[name], tenant, f"oid-{name}{suffix}", f"{name}{suffix}@test")).lastrowid
        m365._user_tokens[i] = (f"user-{name}{suffix}", time.time() + 3600)
        identities[f"{name}{suffix}"] = i
    conn.execute("INSERT INTO m365_shared_mailboxes(tenant_row, address, label, group_ref) "
                 "VALUES (?, 'frontdesk@gasco.test', 'Front desk', '')", (gasco,))
    conn.execute("INSERT INTO m365_shared_mailboxes(tenant_row, address, label, group_ref) "
                 "VALUES (?, 'it@eternal.test', 'IT', 'staff')", (home,))
    accounts.set_agent_group(conn, "erynne agents")
    return {**ids, **{f"id_{k}": v for k, v in identities.items()}, "directory": directory}


def _ctx(conn, world, name):
    user = conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (world[name],)).fetchone()
    return m365_mail.context(conn, user, world["directory"])


def test_tools_offer_this_persons_accounts_and_mailboxes(conn, world):
    alice = _ctx(conn, world, "alice")
    assert [a.label for a in alice.accounts] == ["Eternal", "Gasco"]
    assert [m.label for m in alice.mailboxes] == ["Front desk"]           # IT is for the staff group only
    spec = {s["function"]["name"]: s["function"]["parameters"] for s in m365_mail.specs(alice)}
    assert set(spec) == m365_mail.TOOL_NAMES
    assert spec["search_mail"]["properties"]["account"]["enum"] == ["Eternal", "Gasco"]
    assert spec["search_mail"]["properties"]["mailbox"]["enum"] == ["Front desk"]
    bob = _ctx(conn, world, "bob")
    assert "account" not in {s["function"]["name"]: s for s in m365_mail.specs(bob)}["search_mail"]["function"][
        "parameters"]["properties"]                                        # one account: no choice to make
    assert [m.label for m in bob.mailboxes] == ["IT"]
    # Claude's connector lists the mail and calendar tools (gated at call time), never the live SharePoint search.
    names = {t.name for t in asyncio.run(mcp_server.build_server(Settings(_env_file=None), lambda: None).list_tools())}
    assert names & m365_mail.TOOL_NAMES == m365_mail.CLAUDE_TOOL_NAMES and "search_sharepoint" not in names


def test_search_read_calendar_and_sharepoint(conn, settings, world):
    ctx = _ctx(conn, world, "alice")
    cited = []
    cite = lambda *a: cited.append(a) or f"[{len(cited)}]"   # noqa: E731
    found = m365_mail.run(conn, settings, ctx, "search_mail", {"query": "boiler", "account": "Eternal",
                                                               "since": "2026-01-01"}, cite)
    assert [m["id"] for m in found["messages"]] == ["m1"] and found["messages"][0]["cite"] == "[1]"   # old one dropped
    assert cited[0][0] == "mail" and cited[0][4] == "https://outlook.test/m/1"
    read = m365_mail.run(conn, settings, ctx, "read_mail", {"id": "m1"}, cite)
    assert read["body"] == "Hi Alice,\nThe boiler is fixed; the bill is $420."        # no signature, no quote
    assert read["from"] == "Sam GM" and read["cite"] == "[1]" or read["cite"] == "[2]"
    with pytest.raises(m365_mail.MailError, match="search_mail or recent_mail first"):
        m365_mail.run(conn, settings, ctx, "read_mail", {"id": "m9"}, cite)
    with pytest.raises(m365_mail.MailError, match="Say which account"):
        m365_mail.run(conn, settings, ctx, "search_mail", {"query": "boiler"}, cite)
    events = m365_mail.run(conn, settings, ctx, "calendar", {"start": "2026-10-23", "end": "2026-10-23",
                                                             "account": "Eternal"}, cite)["events"]
    assert events[0]["subject"] == "Owners meeting" and events[0]["location"] == "Board room"
    hits = m365_mail.run(conn, settings, ctx, "search_sharepoint", {"query": "boiler", "account": "Gasco"}, cite)
    assert hits["results"][0]["summary"] == "Boiler maintenance plan"


def test_shared_mailboxes_follow_exchange(conn, settings, world):
    cite = lambda *a: "[1]"   # noqa: E731
    alice = m365_mail.run(conn, settings, _ctx(conn, world, "alice"), "search_mail",
                          {"query": "boiler", "account": "Gasco", "mailbox": "Front desk"}, cite)
    assert alice["mailbox"] == "Front desk" and alice["messages"]
    with pytest.raises(m365_mail.MailError, match="another organisation"):
        m365_mail.run(conn, settings, _ctx(conn, world, "alice"), "search_mail",
                      {"query": "boiler", "account": "Eternal", "mailbox": "Front desk"}, cite)
    # Listed for Gasco, but Exchange doesn't let this account in.
    conn.execute("UPDATE m365_shared_mailboxes SET tenant_row = (SELECT tenant_row FROM m365_identities WHERE id = ?) "
                 "WHERE label = 'Front desk'", (world["id_bob"],))
    with pytest.raises(m365_mail.MailError, match="didn't allow"):
        m365_mail.run(conn, settings, _ctx(conn, world, "bob"), "search_mail",
                      {"query": "boiler", "mailbox": "Front desk"}, cite)


def test_m365_tokens_needing_relinking_are_explained(conn, settings, world):
    m365._user_tokens.clear()
    conn.execute("UPDATE m365_identities SET status = 'relink'")
    ctx = m365_mail.Context([m365_mail.Account(world["id_bob"], "Eternal", 1, "bob@test")], [])
    with pytest.raises(m365_mail.MailError, match="needs linking again"):
        m365_mail.run(conn, settings, ctx, "calendar", {"start": "2026-10-23", "end": "2026-10-23"}, lambda *a: "")


# ------------------------------------------------------------------ in chat
@pytest.fixture
def client(settings, conn, world):
    agent_llm = AgentLLM()
    with TestClient(create_app(settings, Services(llm=FakeLLM(), embedder=KeywordEmbedder(),
                                                  directory=world["directory"], agent_llm=agent_llm))) as c:
        c.agent_llm = agent_llm
        c.post("/login", data={"username": "alice", "password": "password123"})
        yield c


def test_a_reply_draft_is_saved_to_drafts_only_on_apply(client, conn, graph):
    llm = client.agent_llm
    llm.turns(ToolTurn("", [ToolCall("c1", "search_mail", {"query": "boiler", "account": "Eternal"})]),
              lambda m: ToolTurn("", [ToolCall("c2", "draft_reply", {"id": last_tool(m)["messages"][0]["id"],
                                                                     "body": "Thanks Sam, I'll pay it Thursday."})]),
              ToolTurn("I drafted a reply to Sam [1]."))
    events = _ask(client, "reply to Sam about the boiler")
    final = _final(events)
    assert final["route"] == "agent" and final["citations"] == [1]
    assert final["sources"][0]["kind"] == "mail" and final["sources"][0]["url"] == "https://outlook.test/m/1"
    card = next(e for e in events if e["type"] == "proposal")
    assert card["kind"] == "mail" and card["body"] == "Thanks Sam, I'll pay it Thursday."
    assert card["where"] == "alice@test (Eternal)" and card["changes"][0] == "Reply to sam@eternal.test"
    assert graph.drafts == []                                              # nothing written yet
    logged = conn.execute("SELECT args FROM agent_calls WHERE tool = 'draft_reply'").fetchone()[0]
    assert "Thursday" not in logged and "characters" in logged             # no mail text in the log
    # The model saw a short id, never Microsoft's long one.
    assert LONG_ID not in json.dumps(llm.calls[1][0])

    saved = client.post(f"/api/agent/proposals/{card['id']}/apply").json()
    assert saved["status"] == "applied" and saved["url"] == "https://outlook.test/drafts/1"
    path, body = graph.drafts[0]
    assert path.endswith(f"/me/messages/{LONG_ID}/createReply") and body == {"comment": "Thanks Sam, I'll pay it Thursday."}
    # The conversation remembers the email source and the saved draft.
    cid = events[0]["conversation_id"]
    shown = client.get(f"/api/conversations/{cid}").json()["messages"][-1]
    assert shown["sources"][0]["file"] == "Boiler repair" and shown["sources"][0]["url"] == "https://outlook.test/m/1"
    assert shown["proposals"][0]["status"] == "applied"


def test_new_mail_draft_and_dismiss(client, conn, graph):
    client.agent_llm.turns(ToolTurn("", [ToolCall("c1", "draft_mail", {"to": ["ops@eternal.test"], "subject": "Boiler",
                                                                       "body": "It's fixed.", "account": "Eternal"})]),
                           ToolTurn("Drafted."))
    card = next(e for e in _ask(client, "email ops that the boiler is fixed") if e["type"] == "proposal")
    assert client.post(f"/api/agent/proposals/{card['id']}/dismiss").json()["status"] == "dismissed"
    assert graph.drafts == []
    client.agent_llm.turns(ToolTurn("", [ToolCall("c1", "draft_mail", {"to": ["not-an-address"], "subject": "x",
                                                                       "body": "y", "account": "Eternal"})]),
                           lambda m: ToolTurn("Couldn't: " + last_tool(m)["error"]))
    assert "email addresses" in _final(_ask(client, "email someone"))["answer"]


def test_mail_tools_are_only_for_the_agent_group(client, conn, world):
    client.post("/logout")
    client.post("/login", data={"username": "bob", "password": "password123"})
    _ask(client, "what's in my inbox?")
    offered = {t["function"]["name"] for t in client.agent_llm.calls[-1][2]}   # bob gets Erynne's tools...
    assert "search_documents" in offered
    assert not offered & {"search_mail", "recent_mail", "read_mail", "calendar", "draft_reply", "draft_mail",
                          "search_sharepoint"}                              # ...but no mail: not in the agent group
    assert not [t for t in client.get("/api/me/tools").json()["tools"] if t["group"] == "Microsoft 365"]


# ------------------------------------------------------------ the connector
def _caller(conn, user_id):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "t", can_edit=True)[1])


@contextmanager
def _web(settings, world, name):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=world["directory"]))) as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


def test_claude_gets_mail_only_in_the_mail_group(conn, settings, world, graph):
    """The connector's mail tools refuse until an admin names a group, and then only for its members."""
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: world["directory"])
    alice, bob = _caller(conn, world["alice"]), _caller(conn, world["bob"])
    users.create_user(conn, "root", "password123", role="admin")
    with pytest.raises(ToolError, match="off for Claude"):
        tools.search_mail(conn, alice, "boiler")
    assert not graph.calls

    with _web(settings, world, "root") as web:
        assert web.get("/api/admin/settings/claude-mail").json()["enabled"] is False
        assert web.put("/api/admin/settings/claude-mail", json={"group": "Nobody"}).status_code == 422
        got = web.put("/api/admin/settings/claude-mail", json={"group": "staff"}).json()
        assert got["enabled"] and got["group"] == "staff" and got["members"] == ["bob"]
        assert 'id="claude-mail-settings"' in web.get("/admin/libraries").text
    assert m365_mail.claude_group(conn) == "staff"
    log = conn.execute("SELECT action, target, detail FROM access_log ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(log) == ("settings.claude_mail", "staff", "was (off)")

    with pytest.raises(ToolError, match="mail group"):
        tools.search_mail(conn, alice, "boiler")                            # alice isn't in staff
    assert tools.recent_mail(conn, bob)[0]["messages"] == []                 # bob is
    with _web(settings, world, "alice") as web:
        assert web.get("/api/admin/settings/claude-mail").status_code == 403
    with _web(settings, world, "root") as web:
        assert web.put("/api/admin/settings/claude-mail", json={"group": ""}).json()["enabled"] is False
    with pytest.raises(ToolError, match="off for Claude"):
        tools.recent_mail(conn, bob)
    # In the group but without a linked account: told to link one.
    m365_mail.set_claude_group(conn, "staff")
    conn.execute("UPDATE m365_identities SET status = 'relink' WHERE user_id = ?", (world["bob"],))
    with pytest.raises(ToolError, match="links one on Preferences"):
        tools.recent_mail(conn, bob)


def test_claude_reads_mail_with_opaque_ids_and_drafts_as_proposals(conn, settings, world, graph):
    m365_mail.set_claude_group(conn, "erynne agents")
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: world["directory"])
    alice = _caller(conn, world["alice"])

    found = tools.search_mail(conn, alice, "boiler", account="Eternal", since="2026-01-01")[0]
    (msg,) = found["messages"]
    assert msg["id"].startswith("mail_") and msg["citation"].startswith("Boiler repair · Sam GM · 2026-10-20")
    assert msg["link"] == "https://outlook.test/m/1" and "cite" not in msg and msg["preview"] == "The boiler is fixed"
    assert m365_mail.decode_ref(msg["id"]) == (world["id_alice"], None, LONG_ID)
    read = tools.read_mail(conn, alice, msg["id"])[0]
    assert read["body"] == "Hi Alice,\nThe boiler is fixed; the bill is $420." and read["link"] == "https://outlook.test/m/1"
    assert read["citation"].startswith("Boiler repair · Sam GM")
    # An id made up, or made for someone else's account or a mailbox they may not use, never reaches Graph.
    before = len(graph.calls)
    for bad in ("m1", "mail_nope", m365_mail.encode_ref(world["id_bob"], None, LONG_ID),
                m365_mail.encode_ref(world["id_alice"], "it@eternal.test", LONG_ID)):
        with pytest.raises(ToolError, match="There's no message"):
            tools.read_mail(conn, alice, bad)
    assert len(graph.calls) == before
    with pytest.raises(ToolError, match="Say which account"):
        tools.search_mail(conn, alice, "boiler")
    events = tools.calendar(conn, alice, "2026-10-23", "2026-10-23", account="Eternal")[0]["events"]
    assert events[0]["subject"] == "Owners meeting" and events[0]["link"] == "https://outlook.test/e/1"
    shared = tools.search_mail(conn, alice, "boiler", account="Gasco", mailbox="Front desk")[0]
    assert shared["mailbox"] == "Front desk" and m365_mail.decode_ref(shared["messages"][0]["id"])[1] == "frontdesk@gasco.test"

    # A reply is a proposal with an approve link; nothing is written until the person applies it.
    out = tools.draft_reply(conn, alice, msg["id"], "Thanks Sam, I'll pay it Thursday.")[0]
    assert out["status"] == "proposed" and out["kind"] == "mail" and out["approve"].endswith(f"#p{out['proposal_id']}")
    assert out["changes"][0] == "Reply to sam@eternal.test" and "Nothing is sent" in out["note"]
    assert graph.drafts == []
    with _web(settings, world, "alice") as web:
        saved = web.post(f"/api/agent/proposals/{out['proposal_id']}/apply").json()
    assert saved["status"] == "applied" and saved["url"] == "https://outlook.test/drafts/1"
    path, body = graph.drafts[0]
    assert path.endswith(f"/me/messages/{LONG_ID}/createReply") and body == {"comment": "Thanks Sam, I'll pay it Thursday."}
    assert tools.get_proposal(conn, alice, out["proposal_id"])[0]["status"] == "applied"

    new = tools.draft_mail(conn, alice, ["ops@eternal.test"], "Boiler", "It's fixed.", account="Eternal")[0]
    assert new["status"] == "proposed" and new["summary"] and len(graph.drafts) == 1
    with pytest.raises(ToolError, match="email addresses"):
        tools.draft_mail(conn, alice, ["not-an-address"], "x", "y", account="Eternal")
    # The call log keeps ids and lengths, never the text of a draft.
    logged = mcp_server.logged_args("draft_reply", {"id": msg["id"], "body": "Thanks Sam, I'll pay it Thursday."})
    assert logged == {"id": msg["id"], "body": "(33 characters)"}
    assert mcp_server.logged_args("search_documents", {"query": "x"}) == {"query": "x"}


def test_a_mail_tool_switched_off_is_refused_before_it_runs(client, conn, graph):
    """The model knows the tool names whether or not they were offered (a planted passage can name one); a
    mail tool the person switched off is refused like any other, and nothing reaches Microsoft."""
    client.agent_llm.turns(ToolTurn("", [ToolCall("c1", "search_mail", {"query": "boiler", "account": "Eternal"})]),
                           lambda m: ToolTurn("Couldn't: " + last_tool(m)["error"]))
    events = []
    with client.stream("POST", "/api/ask", json={"question": "find the boiler mail",
                                                 "disabled_tools": ["search_mail"]}) as r:
        assert r.status_code == 200, r.read()
        for block in r.iter_text():
            for line in block.split("\n"):
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
    assert "no tool called" in _final(events)["answer"]
    assert "search_mail" not in client.agent_llm.calls[0][1]                   # it wasn't offered...
    assert not [url for _, url in graph.calls if "/messages" in url]           # ...and it never ran
