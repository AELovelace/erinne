"""Links Claude can fetch: handed out with file results through /mcp, checked again on every fetch."""

import json

import pytest
from fastapi.testclient import TestClient

from mnemerynne import api_tokens, claude_links, permissions, users
from mnemerynne.api_tokens import Caller
from mnemerynne.web.app import Services, create_app
from test_mcp import _caller, _initialize, _rpc, embedder, world  # noqa: F401 (fixtures)

BASE = "https://mnemerynne.test"


@pytest.fixture
def client(settings, conn, world, embedder):  # noqa: F811
    settings.public_url = BASE
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as c:
        yield c


def _search(client, token, query="night audit"):
    _initialize(client, token)
    result = _rpc(client, token, "tools/call", {"name": "search_documents", "arguments": {"query": query}})
    result = result.json()["result"]
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def _id(fetch):
    assert fetch.startswith(BASE + "/c/")
    return fetch.removeprefix(BASE + "/c/")


def test_file_results_carry_a_link_claude_can_open_without_signing_in(client, conn, world):
    _, token = api_tokens.create(conn, world["alice"], "laptop")
    hit = _search(client, token)["results"][0]
    assert hit["file"] == "OPERA.pdf" and hit["link"].startswith(BASE + "/files/")   # the person's link stays
    page = client.get(hit["fetch"].removeprefix(BASE))                                 # no session, no token
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/markdown")
    assert page.headers["cache-control"] == "no-store" and "noindex" in page.headers["x-robots-tag"]
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "# OPERA.pdf" in page.text and "Run the night audit after midnight" in page.text
    assert "## Page 1" in page.text and "From Oracle in Mnemerynne" in page.text
    logged = conn.execute("SELECT tool, file_ids, token_id FROM mcp_calls WHERE tool = 'fetch_link'").fetchone()
    assert logged["file_ids"] == json.dumps([hit["file_id"]]) and logged["token_id"] is not None
    # One link per file, whichever pages are cited, and the same one again on the next search.
    results = _search(client, token)["results"]
    assert results[0]["fetch"] == hit["fetch"]
    files = {r["file_id"] for r in results}
    assert conn.execute("SELECT count(*) FROM claude_links").fetchone()[0] == len(files) < len(results)


def test_reading_starts_at_the_cited_page_and_goes_on_in_parts(client, conn, world, monkeypatch):
    _, token = api_tokens.create(conn, world["alice"], "laptop")
    hit = next(h for h in _search(client, token, "parking lot")["results"] if h.get("page") == 3)
    first = client.get(hit["fetch"].removeprefix(BASE)).text
    assert "## Page 2" in first and "## Page 1" not in first          # a page before the cited one, for context
    assert hit["fetch"].endswith("?page=3")
    assert f"Earlier parts: {hit['fetch'].split('?')[0]}?from=" in first
    monkeypatch.setattr(claude_links, "PART_CHARS", 10)
    start = client.get(hit["fetch"].removeprefix(BASE).split("?")[0] + "?from=0").text
    assert "## Page 1" in start and "## Page 2" not in start
    more = start.split("(More of this file: ")[1].split(")")[0]
    assert "## Page 2" in client.get(more.removeprefix(BASE)).text


def test_every_fetch_checks_again(client, conn, world, settings):
    token_id, token = api_tokens.create(conn, world["alice"], "laptop")
    path = _id(_search(client, token)["results"][0]["fetch"]).split("?")[0]
    url = "/c/" + path

    def status():
        return client.get(url).status_code

    assert status() == 200
    permissions.set_claude_enabled(conn, world["oracle"], False, world["alice"])   # no longer shared with Claude
    assert status() == 404
    permissions.set_claude_enabled(conn, world["oracle"], True, world["alice"])
    conn.execute("UPDATE files SET claude_hidden = 1 WHERE display_name = 'OPERA.pdf'")   # taken out of view
    assert status() == 404
    conn.execute("UPDATE files SET claude_hidden = 0")
    claude_links.save_config(conn, enabled=False)                                       # links off
    assert status() == 404
    claude_links.save_config(conn, enabled=True)
    users.set_active(conn, "alice", False)
    assert status() == 404
    users.set_active(conn, "alice", True)
    assert status() == 200
    api_tokens.revoke(conn, token_id, world["alice"])                                   # connection removed
    assert status() == 404 and "expired or doesn't exist" in client.get(url).text
    assert client.get("/c/not-a-real-link").status_code == 404


def test_links_expire_and_wear_out(client, conn, world):
    _, token = api_tokens.create(conn, world["alice"], "laptop")
    url = "/c/" + _id(_search(client, token)["results"][0]["fetch"]).split("?")[0]
    conn.execute("UPDATE claude_links SET fetches = ?", (claude_links.MAX_FETCHES,))
    assert client.get(url).status_code == 429
    fresh = _search(client, token)["results"][0]["fetch"]                     # a worn-out link isn't reused
    assert "/c/" + _id(fresh).split("?")[0] != url and client.get(fresh.removeprefix(BASE)).status_code == 200
    conn.execute("UPDATE claude_links SET expires_at = datetime('now', '-1 minute')")
    assert client.get(fresh.removeprefix(BASE)).status_code == 404


def test_only_claudes_own_connections_get_links(conn, world, settings):
    out = {"results": [{"file_id": 1, "link": "x"}]}
    assert "fetch" not in claude_links.attach(conn, BASE, Caller(world["alice"], "alice", None, True), out)["results"][0]
    assert "fetch" not in claude_links.attach(conn, "", _caller(conn, world["alice"]), out)["results"][0]   # no address
    assert "fetch" in claude_links.attach(conn, BASE, _caller(conn, world["alice"]), out)["results"][0]


def test_admin_sets_how_long_links_last(client, conn, world):
    users.create_user(conn, "boss", "password123", role="admin")
    client.post("/login", data={"username": "boss", "password": "password123"})
    got = client.get("/api/admin/settings/claude-links").json()
    assert got["enabled"] and got["minutes"] == 60 and got["public_url"] == BASE
    assert client.put("/api/admin/settings/claude-links", json={"minutes": 2}).status_code == 422
    assert client.put("/api/admin/settings/claude-links", json={"minutes": 15, "enabled": False}).json()["minutes"] == 15
    assert claude_links.config(conn) == {"enabled": False, "minutes": 15}
