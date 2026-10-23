"""Mail, calendar and drafts for the agent group, through linked Microsoft accounts. Nothing ever sends."""

import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, KeywordEmbedder
from mnemerynne import accounts, m365, m365_mail, mcp_server, users
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.config import Settings
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
        if path.endswith("/messages") and method == "GET":
            assert q["$search"][0].startswith('"boiler') or q["$search"][0] == '"boiler from:Sam"'
            return httpx.Response(200, json={"value": [
                {"id": LONG_ID, "subject": "Boiler repair", "receivedDateTime": "2026-10-20T15:04:00Z",
                 "from": {"emailAddress": {"name": "Sam GM", "address": "sam@eternal.test"}},
                 "bodyPreview": "The boiler is fixed", "webLink": "https://outlook.test/m/1"},
                {"id": "old", "subject": "Old boiler", "receivedDateTime": "2025-01-01T10:00:00Z",
                 "from": {"emailAddress": {"name": "Ann"}}, "webLink": "https://outlook.test/m/2"}]})
        if "/mailFolders/inbox/messages" in path:
            return httpx.Response(200, json={"value": []})
        if path.endswith(f"/messages/{LONG_ID}") and method == "GET":
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
    # Claude never gets any of these.
    names = {t.name for t in asyncio.run(mcp_server.build_server(Settings(_env_file=None), lambda: None).list_tools())}
    assert not names & m365_mail.TOOL_NAMES


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
