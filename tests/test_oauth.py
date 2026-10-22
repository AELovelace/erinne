"""OAuth for /mcp: claude.ai connectors and Claude Code sign people in and ask consent."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import api_tokens, oauth, users
from mnemerynne.web.app import Services, create_app
from test_mcp import _initialize, _rpc

CLAUDE = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def client(settings, conn):
    settings.public_url = "https://mnemerynne.test"
    for name in ("alice", "bob"):
        users.create_user(conn, name, "password123")
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder())),
                    base_url="https://mnemerynne.test") as c:
        yield c


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(client, uri=CLAUDE, **extra):
    return client.post("/oauth/register", json={"redirect_uris": [uri], "client_name": "Claude", **extra})


def _connect(client, name="alice", can_edit=True, uri=CLAUDE):
    """The whole dance: register, sign in, approve, swap the code. Returns (tokens, client_id)."""
    client_id = _register(client, uri).json()["client_id"]
    verifier, challenge = _pkce()
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": uri, "state": "s1",
             "code_challenge": challenge, "code_challenge_method": "S256", "resource": "https://mnemerynne.test/mcp"}
    first = client.get("/oauth/authorize?" + urlencode(query), follow_redirects=False)
    if first.status_code == 303:                         # not signed in: sign in, come back
        assert first.headers["location"] == "/login"
        back = client.post("/login", data={"username": name, "password": "password123"}, follow_redirects=False)
        assert back.headers["location"].startswith("/oauth/authorize?")
        first = client.get(back.headers["location"])
    assert first.status_code == 200 and "Claude</strong> wants to use" in first.text
    nonce = first.text.split('name="nonce" value="')[1].split('"')[0]
    form = {"nonce": nonce, "decision": "allow", **({"can_edit": "1"} if can_edit else {})}
    done = client.post("/oauth/authorize", data=form, follow_redirects=False)
    q = parse_qs(urlsplit(done.headers["location"]).query)
    assert done.headers["location"].startswith(uri) and q["state"] == ["s1"] and q["iss"] == ["https://mnemerynne.test"]
    got = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                            "redirect_uri": uri, "client_id": client_id, "code_verifier": verifier})
    assert got.status_code == 200, got.text
    return got.json(), client_id


def test_discovery_points_claude_at_sign_in(client):
    unauthorized = _initialize(client, None)
    assert unauthorized.status_code == 401
    assert 'resource_metadata="https://mnemerynne.test/.well-known/oauth-protected-resource"' in \
        unauthorized.headers["www-authenticate"]
    resource = client.get("/.well-known/oauth-protected-resource").json()
    assert resource["resource"] == "https://mnemerynne.test/mcp"
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["registration_endpoint"] == "https://mnemerynne.test/oauth/register"


def test_only_claude_may_register(client):
    assert _register(client).status_code == 201
    assert _register(client, "http://127.0.0.1:33418/callback").status_code == 201          # Claude Code
    for bad in ("https://evil.example/cb", "https://claude.ai.evil.example/cb", "http://claude.ai/cb"):
        refused = _register(client, bad)
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_redirect_uri", bad
    # Claude's servers call this from claude.ai: not refused as a cross-site request.
    assert client.post("/oauth/register", json={"redirect_uris": [CLAUDE]},
                       headers={"Origin": "https://claude.ai"}).status_code == 201


def test_connecting_and_using_mcp(client, conn):
    tokens, client_id = _connect(client)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == oauth.ACCESS_SECONDS
    assert _initialize(client, tokens["access_token"]).status_code == 200
    listed = _rpc(client, tokens["access_token"], "tools/list").json()["result"]["tools"]
    assert "search_documents" in {t["name"] for t in listed}
    assert conn.execute("SELECT count(*) FROM oauth_tokens WHERE token_hash = ?",
                        (tokens["access_token"],)).fetchone()[0] == 0                    # only hashes stored
    # The person sees the connection, can stop editing, and disconnect.
    connections = client.get("/api/oauth/connections").json()
    assert connections["mcp_url"] == "https://mnemerynne.test/mcp"
    (c,) = connections["connections"]
    assert c["app"] == "Claude" and c["can_edit"] and c["last_used_at"]
    client.patch(f"/api/oauth/connections/{c['id']}", json={"can_edit": False})
    assert oauth.verify(conn, tokens["access_token"]).can_edit is False
    client.delete(f"/api/oauth/connections/{c['id']}")
    assert _initialize(client, tokens["access_token"]).status_code == 401


def test_refresh_rotates_and_a_copied_token_ends_the_connection(client, conn):
    tokens, client_id = _connect(client)
    renewed = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                "refresh_token": tokens["refresh_token"]}).json()
    assert renewed["access_token"] != tokens["access_token"]
    assert _initialize(client, renewed["access_token"]).status_code == 200
    again = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                              "refresh_token": tokens["refresh_token"]})
    assert again.status_code == 400 and "ended" in again.json()["error_description"]
    assert _initialize(client, renewed["access_token"]).status_code == 401        # the whole connection
    assert client.get("/api/oauth/connections").json()["connections"] == []


def test_codes_are_single_use_and_pkce_bound(client, conn):
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    client.post("/login", data={"username": "alice", "password": "password123"})
    page = client.get("/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE, "code_challenge": challenge,
        "code_challenge_method": "S256"}))
    nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
    code = parse_qs(urlsplit(client.post("/oauth/authorize", data={"nonce": nonce, "decision": "allow"},
                                         follow_redirects=False).headers["location"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE, "client_id": client_id}
    assert client.post("/oauth/token", data={**form, "code_verifier": "wrong"}).json()["error"] == "invalid_grant"
    first = client.post("/oauth/token", data={**form, "code_verifier": verifier}).json()
    assert first["access_token"]
    assert client.post("/oauth/token", data={**form, "code_verifier": verifier}).json()["error"] == "invalid_grant"
    assert _initialize(client, first["access_token"]).status_code == 401           # replayed code: ended


def test_bad_requests_and_denial(client):
    client_id = _register(client).json()["client_id"]
    _, challenge = _pkce()
    base = {"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE, "code_challenge": challenge,
            "code_challenge_method": "S256"}
    for change in ({"redirect_uri": "https://evil.example/cb"}, {"code_challenge_method": "plain"},
                   {"client_id": "nope"}, {"resource": "https://elsewhere.example/mcp"}):
        page = client.get("/oauth/authorize?" + urlencode({**base, **change}), follow_redirects=False)
        assert page.status_code == 400 and "location" not in page.headers, change   # shown, never redirected
    client.post("/login", data={"username": "alice", "password": "password123"})
    page = client.get("/oauth/authorize?" + urlencode({**base, "state": "x"}))
    nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
    denied = client.post("/oauth/authorize", data={"nonce": nonce, "decision": "deny"}, follow_redirects=False)
    assert parse_qs(urlsplit(denied.headers["location"]).query) == {"error": ["access_denied"], "state": ["x"]}
    # A forged approval (no matching nonce in this session) does nothing.
    assert client.post("/oauth/authorize", data={"nonce": "forged", "decision": "allow"}).status_code == 400


def test_search_only_connections_cant_edit(client, conn):
    tokens, _ = _connect(client, can_edit=False)
    caller = oauth.verify(conn, tokens["access_token"])
    assert caller.can_edit is False and caller.token_id is None and caller.grant_id


def test_admins_and_deactivation_end_connections(client, conn, settings):
    tokens, _ = _connect(client, "bob")
    bob = users.get_user(conn, "bob")["id"]
    assert api_tokens.revoke_all(conn, bob) == 1
    assert _initialize(client, tokens["access_token"]).status_code == 401
    tokens, _ = _connect(client, "bob")
    conn.execute("UPDATE users SET active = 0 WHERE id = ?", (bob,))
    assert _initialize(client, tokens["access_token"]).status_code == 401


def test_calls_are_logged_with_the_connection(client, conn):
    tokens, _ = _connect(client)
    _initialize(client, tokens["access_token"])
    _rpc(client, tokens["access_token"], "tools/call", {"name": "list_libraries", "arguments": {}}, id_=2)
    row = conn.execute("SELECT token_id, grant_id FROM mcp_calls ORDER BY id DESC").fetchone()
    assert row["token_id"] is None and row["grant_id"]
