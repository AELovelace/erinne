"""Messages: notes between people (web, Claude, Erynne), what Mnemerynne tells people (system messages),
the unread badge, and announcements meant for them."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import messages, notices, retention, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def people(conn):
    return {"alice": users.create_user(conn, "alice", "password123"),
            "rob": users.create_user(conn, "rob", "password123"),
            "boss": users.create_user(conn, "boss", "password123", role="admin")}


@pytest.fixture
def directory(conn, people):
    conn.execute("UPDATE users SET ad_account = username")
    return FakeDirectory({"alice": {"Front Desk"}, "rob": {"IT Staff"}, "boss": {"Domain Admins"}},
                         groups=["Front Desk", "IT Staff", "Domain Admins"])


@contextmanager
def _web(settings, name, directory=None):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


def _user(conn, name):
    return users.get_user(conn, name)


# ----------------------------------------------------------------- notes
def test_a_note_between_people_and_the_badge(conn, settings, people):
    with _web(settings, "alice") as alice:
        assert alice.get("/api/people").json()["people"] == ["boss", "rob"]
        sent = alice.post("/api/messages", json={"to": "rob", "text": "Printer's fixed.", "link": "/files/1/view"})
        assert sent.status_code == 201 and sent.json()["message"]["to"] == "rob"
        assert alice.post("/api/messages", json={"to": "nobody", "text": "hi"}).status_code == 422
        assert alice.post("/api/messages", json={"to": "alice", "text": "hi"}).status_code == 422
        assert alice.post("/api/messages", json={"to": "rob", "text": "  "}).status_code == 422
        assert alice.post("/api/messages", json={"to": "rob", "text": "x", "link": "https://evil.example/"}).status_code == 422
        assert [m["to"] for m in alice.get("/api/messages?box=sent").json()["messages"]] == ["rob"]
    with _web(settings, "rob") as rob:
        assert 'id="nav-unread" >1<' in rob.get("/").text.replace("\n", " ") or ">1</span>" in rob.get("/").text
        assert rob.get("/api/notify").json()["unread"] == 1
        got = rob.get("/api/messages").json()
        m = got["messages"][0]
        assert got["unread"] == 1 and m["from"] == "alice" and m["text"] == "Printer's fixed." and m["link"] == "/files/1/view"
        assert m["read"] is False and m["kind"] == "note"
        assert rob.post(f"/api/messages/{m['message_id']}/read").status_code == 204
        assert rob.post("/api/messages/999/read").status_code == 404
        assert rob.get("/api/notify").json()["unread"] == 0
        page = rob.get("/messages")
        assert page.status_code == 200 and "messages.js" in page.text and "notify.js" in page.text
        assert 'id="messages-host"' in page.text
        # Without Rooms, the top-right button opens the fullscreen panel on every page.
        home = rob.get("/").text
        assert '<button type="button" class="notify-button" id="notify-button"' in home and 'id="msg-modal"' in home
        assert 'href="/messages"' not in home
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as anon:
        assert anon.get("/api/people").status_code == 401
        assert anon.get("/api/notify").status_code == 401
        assert "notify.js" not in anon.get("/login").text


def test_links_are_paths_in_mnemerynne_only(settings):
    assert messages.safe_link("") == "" and messages.safe_link(None) == ""
    assert messages.safe_link("/wiki/40#top") == "/wiki/40#top"
    assert messages.safe_link("https://mnemerynne.test/proposals#p7", "https://mnemerynne.test") == "/proposals#p7"
    for bad in ("//evil.example/x", "https://evil.example/", "/x\\y", "javascript:alert(1)", "files/1", "/a\nb"):
        with pytest.raises(messages.MessageError):
            messages.safe_link(bad, "https://mnemerynne.test")


def test_senders_are_rate_limited_and_messages_expire(conn, settings, people, monkeypatch):
    monkeypatch.setattr(messages, "MAX_PER_HOUR", 3)
    alice, rob = _user(conn, "alice"), _user(conn, "rob")
    for n in range(3):
        messages.send(conn, alice, "rob", f"note {n}", via="web")
    with pytest.raises(messages.MessageError, match="wait"):
        messages.send(conn, alice, "rob", "one more", via="web")
    assert messages.unread_count(conn, rob["id"]) == 3
    assert messages.mark_all_read(conn, rob["id"]) == 3
    conn.execute("UPDATE user_messages SET created_at = datetime('now', '-400 days')")
    settings.message_days = 365
    assert retention.purge(conn, settings).get("user_messages") == 3
    assert messages.inbox(conn, rob["id"]) == []


def test_system_messages_never_break_their_caller(conn, settings, people):
    rob = _user(conn, "rob")
    assert messages.system(conn, rob["id"], "An admin changed your role.", link="/preferences")
    assert messages.system(conn, rob["id"], "bad link", link="https://evil.example/")   # link dropped, message kept
    assert messages.system(conn, 999999, "nobody") is None
    assert messages.system(conn, rob["id"], "   ") is None
    got = messages.inbox(conn, rob["id"])
    assert [m["from"] for m in got] == ["Mnemerynne", "Mnemerynne"] and got[0]["link"] == "" and got[1]["link"] == "/preferences"
    assert all(m["kind"] == "system" for m in got)


# --------------------------------------------------------- announcements
def test_announcements_reach_their_audience_until_seen_or_acknowledged(conn, settings, people, directory):
    boss = _user(conn, "boss")
    everyone = notices.create(conn, boss["id"], "Welcome", "Hello **all**.", link="/help")
    it_only = notices.create(conn, boss["id"], "Patch night", "Servers reboot at 02:00.", group_ref="IT Staff",
                             must_ack=True)
    gone = notices.create(conn, boss["id"], "Old news", expires_at="2000-01-01")
    with pytest.raises(notices.NoticeError):
        notices.create(conn, boss["id"], "", "no title")
    with pytest.raises(notices.NoticeError):
        notices.create(conn, boss["id"], "Bad link", link="https://evil.example/")
    with pytest.raises(notices.NoticeError):
        notices.create(conn, boss["id"], "Bad date", expires_at="soon")

    with _web(settings, "alice", directory) as alice:
        pending = alice.get("/api/notify").json()["announcements"]
        assert [a["title"] for a in pending] == ["Welcome"]                       # not IT, and the old one expired
        assert pending[0]["html"].startswith("<p>Hello <strong>all</strong>.") and pending[0]["link"] == "/help"
        assert alice.post(f"/api/announcements/{everyone}/mark", json={"ack": False}).status_code == 204
        assert alice.get("/api/notify").json()["announcements"] == []
        assert alice.post(f"/api/announcements/{it_only}/mark", json={"ack": True}).status_code == 404   # not hers
        assert alice.post(f"/api/announcements/{gone}/mark", json={"ack": False}).status_code == 404
        mine = alice.get("/api/announcements").json()["announcements"]
        assert [(a["title"], a["seen"]) for a in mine] == [("Welcome", True)]
    with _web(settings, "rob", directory) as rob:
        pending = rob.get("/api/notify").json()["announcements"]
        assert [a["title"] for a in pending] == ["Patch night", "Welcome"]
        assert rob.post(f"/api/announcements/{it_only}/mark", json={"ack": False}).status_code == 422   # must acknowledge
        assert [a["title"] for a in rob.get("/api/notify").json()["announcements"]] == ["Patch night", "Welcome"]
        assert rob.post(f"/api/announcements/{it_only}/mark", json={"ack": True}).status_code == 204
        assert [a["title"] for a in rob.get("/api/notify").json()["announcements"]] == ["Welcome"]
    listed = {a["title"]: a for a in notices.listing(conn)}
    assert listed["Patch night"]["acked"] == 1 and listed["Patch night"]["group"] == "IT Staff"
    assert listed["Welcome"]["seen"] == 1 and listed["Old news"]["expired"]

    # Expired ones are removed after announcement_days; un-expiring ones stay.
    conn.execute("UPDATE announcements SET expires_at = datetime('now', '-100 days') WHERE id = ?", (gone,))
    assert retention.purge(conn, settings).get("announcements") == 1
    assert len(notices.listing(conn)) == 2


def test_help_pages_let_the_badge_poll(settings, conn, people):
    with _web(settings, "alice") as alice:
        page = alice.get("/help", follow_redirects=True)
        assert "connect-src 'self'" in page.headers["content-security-policy"] and "notify.js" in page.text


# ------------------------------------------------------- hooks, tools, Erynne
def test_mnemerynne_tells_people_what_happened(conn, settings, people, directory):
    import io

    from mnemerynne import api_tokens, comments, fs, permissions, proposals
    from mnemerynne.ingest import store
    from mnemerynne.mcp_server import Tools

    alice, rob, boss = _user(conn, "alice"), _user(conn, "rob"), _user(conn, "boss")
    lib = permissions.create_library(conn, "Brand")
    permissions.grant_user(conn, lib, alice["id"], can_write=True)
    permissions.grant_user(conn, lib, rob["id"], can_write=True)
    fid = store.add_file(conn, settings, store.receive(io.BytesIO(b"# Guide\n"), "guide.md", settings), lib, alice["id"])[0]

    # A comment on alice's file reaches alice (its uploader), later ones reach earlier commenters too, never the author.
    comments.add(conn, fs.Access.of(conn, rob["id"]), fid, "Looks dated.", via="web")
    assert [m["text"][:28] for m in messages.inbox(conn, alice["id"])] == ["rob commented on “guide.md”:"]
    assert messages.inbox(conn, alice["id"])[0]["link"] == f"/files/{fid}/view" and messages.inbox(conn, rob["id"]) == []
    comments.add(conn, fs.Access.of(conn, alice["id"]), fid, "Fixed.", via="web")
    assert [m["from"] for m in messages.inbox(conn, rob["id"])] == ["Mnemerynne"]
    assert len(messages.inbox(conn, alice["id"])) == 1                                    # not told about her own

    # An admin deciding a request tells the requester, with the note and a link to the card.
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    caller = api_tokens.verify(conn, api_tokens.create(conn, alice["id"], "t", can_edit=True)[1])
    out = tools.request_edit_access(conn, caller, library="Brand")[0]
    assert out["status"] == "awaiting_admin"
    proposals.reject(conn, boss, out["proposal"], "Brand stays locked.")
    told = messages.inbox(conn, alice["id"], unread_only=True)[0]
    assert told["text"].startswith("boss turned down your request: Let the AI edit") and "Brand stays locked." in told["text"]
    assert told["link"] == f"/proposals#p{out['proposal']}"

    # Account changes tell the person.
    with _web(settings, "boss", directory) as web:
        assert web.patch(f"/api/admin/users/{rob['id']}", json={"role": "admin"}).status_code == 200
    assert messages.inbox(conn, rob["id"], unread_only=True)[0]["text"] == "boss made you a local admin."


def test_claude_and_erynne_leave_notes_and_read_them(conn, settings, people, directory):
    from mnemerynne import api_tokens, notices
    from mnemerynne.mcp_server import Tools

    alice, rob, boss = _user(conn, "alice"), _user(conn, "rob"), _user(conn, "boss")
    settings.public_url = "https://mnemerynne.test"
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    writer = api_tokens.verify(conn, api_tokens.create(conn, alice["id"], "t", can_edit=True)[1])
    reader = api_tokens.verify(conn, api_tokens.create(conn, alice["id"], "r", can_edit=False)[1])
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="only search"):
        tools.send_message(conn, reader, "rob", "hi")
    with pytest.raises(ToolError, match="no one called"):
        tools.send_message(conn, writer, "nobody", "hi")
    with pytest.raises(ToolError, match="page in Mnemerynne"):
        tools.send_message(conn, writer, "rob", "hi", link="https://evil.example/")
    sent = tools.send_message(conn, writer, "rob", "Printer's fixed.", link="https://mnemerynne.test/files/1/view")[0]
    assert sent["to"] == "rob" and "isn't an email" in sent["note"]

    notices.create(conn, boss["id"], "Patch night", group_ref="IT Staff", must_ack=True)
    robs = api_tokens.verify(conn, api_tokens.create(conn, rob["id"], "t")[1])
    got = tools.list_messages(conn, robs)[0]
    assert got["unread"] == 1 and got["messages"][0]["from"] == "Claude for alice"
    assert got["messages"][0]["link"] == "https://mnemerynne.test/files/1/view"
    assert [a["title"] for a in got["announcements"]] == ["Patch night"] and got["announcements"][0]["must_ack"]
    assert tools.list_messages(conn, robs, mark_read=True)[0]["messages"][0]["read"] is True
    assert tools.list_messages(conn, robs)[0]["unread"] == 0

    # Erynne in chat sends as "Erynne for alice" and is told what's waiting.
    erynne = Tools(settings, lambda: FakeEmbedder(), lambda: directory, mode="erynne")
    erynne.send_message(conn, writer, "rob", "From chat.")
    assert messages.inbox(conn, rob["id"], unread_only=True)[0]["from"] == "Erynne for alice"
    from mnemerynne import agent as agent_mod
    agent = agent_mod.Agent(settings, lambda: FakeEmbedder(), lambda: directory)
    cfg, username = agent._gate(rob["id"])
    assert username == "rob" and cfg["notify"] == (1, ["Patch night"])
    system = agent._messages({"question": "hi"}, "rob", "", notify=cfg["notify"])[0]["content"]
    assert "Waiting for rob: 1 unread message (list_messages); announcements: Patch night." in system
    assert "send_message" in system
