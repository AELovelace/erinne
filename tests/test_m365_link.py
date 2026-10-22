"""Linking Microsoft 365 accounts: tenants, the link round trip, checks, tokens."""

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, apis, m365, users
from mnemerynne.web.app import Services, create_app

HOME, GASCO, OTHER = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                      "33333333-3333-3333-3333-333333333333")


def _jwt(claims: dict) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


class FakeMicrosoft:
    """Microsoft's sign-in, token endpoint and Graph, for a few accounts."""

    def __init__(self):
        self.codes = {}              # code -> account dict
        self.tokens = {}             # access token -> account
        self.refresh = {}            # refresh token -> account
        self.consented = {HOME}
        self.domains = {"eternalhotels.com": HOME, "gascofuel.com": GASCO}
        self.aud = "app-id"

    def account(self, code, tid, oid, upn, sam=None, nonce=None):
        self.codes[code] = {"tid": tid, "oid": oid, "upn": upn, "sam": sam, "nonce": nonce}

    def _issue(self, account):
        n = len(self.tokens) + 1
        access, refresh = f"at-{n}", f"rt-{n}"
        self.tokens[access], self.refresh[refresh] = account, account
        return access, refresh

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            domain = url.split("/")[3]
            if domain not in self.domains:
                return httpx.Response(400, json={"error": "invalid_tenant"})
            return httpx.Response(200, json={"issuer": f"https://login.microsoftonline.com/{self.domains[domain]}/v2.0"})
        if url.endswith("/oauth2/v2.0/token"):
            form = parse_qs(request.content.decode())
            grant = form["grant_type"][0]
            if grant == "client_credentials":
                tid = url.split("/")[3]
                if tid not in self.consented:
                    return httpx.Response(401, json={"error": "invalid_client",
                                                     "error_description": "AADSTS7000229: no consent\nmore"})
                return httpx.Response(200, json={"access_token": "app-" + tid, "expires_in": 3600})
            if grant == "authorization_code":
                account = self.codes.pop(form["code"][0], None)
                if account is None or not form.get("code_verifier"):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                access, refresh = self._issue(account)
                claims = {"aud": self.aud, "tid": account["tid"], "oid": account["oid"], "nonce": account["nonce"],
                          "iss": f"https://login.microsoftonline.com/{account['tid']}/v2.0", "exp": time.time() + 3600,
                          "preferred_username": account["upn"]}
                return httpx.Response(200, json={"access_token": access, "refresh_token": refresh,
                                                 "id_token": _jwt(claims), "expires_in": 3600, "scope": m365.SCOPES})
            if grant == "refresh_token":
                account = self.refresh.pop(form["refresh_token"][0], None)
                if account is None:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                access, refresh = self._issue(account)
                return httpx.Response(200, json={"access_token": access, "refresh_token": refresh, "expires_in": 3600})
        if url.startswith("https://graph.microsoft.com/v1.0/me"):
            account = self.tokens.get(request.headers["authorization"].split()[-1])
            if account is None:
                return httpx.Response(401, json={"error": {"message": "bad token"}})
            return httpx.Response(200, json={"id": account["oid"], "userPrincipalName": account["upn"],
                                             "displayName": account["upn"].split("@")[0],
                                             "onPremisesSamAccountName": account["sam"]})
        return httpx.Response(404, json={"error": {"message": "not faked: " + url}})


@pytest.fixture
def ms(monkeypatch):
    fake = FakeMicrosoft()
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(fake.handler))
    m365._user_tokens.clear()
    m365._app_tokens.clear()
    return fake


@pytest.fixture
def world(settings, conn):
    settings.m365_client_id, settings.m365_client_secret = "app-id", "app-secret"
    directory = FakeDirectory({"alice": {"staff"}, "boss": {"domain admins"}})
    ids = {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "boss")}
    accounts.link(conn, ids["alice"], "alice", directory)
    accounts.link(conn, ids["boss"], "boss", directory)
    home = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Eternal Hotels', 'ad')",
                        (HOME,)).lastrowid
    gasco = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Gasco', 'cloud')",
                         (GASCO,)).lastrowid
    return {**ids, "directory": directory, "home": home, "gasco": gasco}


@pytest.fixture
def client(settings, world):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(),
                                                  directory=world["directory"]))) as c:
        yield c


def _login(client, name):
    assert client.post("/login", data={"username": name, "password": "password123"}).status_code in (200, 303)


def _link(client, ms, tid, oid, upn, sam=None, tenant=None, *, state=None, nonce=None):
    """Run the link round trip; returns the redirect back to /settings."""
    started = client.get("/m365/link" + (f"?tenant={tenant}" if tenant else ""), follow_redirects=False)
    assert started.status_code == 303, started.text
    q = parse_qs(urlsplit(started.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and "offline_access" in q["scope"][0]
    ms.account("code-1", tid, oid, upn, sam, nonce or q["nonce"][0])
    back = client.get(f"/auth/microsoft/callback?code=code-1&state={state or q['state'][0]}", follow_redirects=False)
    assert back.status_code == 303
    return parse_qs(urlsplit(back.headers["location"]).query)


# ------------------------------------------------------------------ linking
def test_linking_an_ad_account_and_a_cloud_account(client, ms, conn, settings, world):
    _login(client, "alice")
    assert client.get("/api/m365").json() == {"available": True, "accounts": [], "tenants": [
        {"id": world["home"], "label": "Eternal Hotels", "own_app": False},
        {"id": world["gasco"], "label": "Gasco", "own_app": False}]}
    back = _link(client, ms, HOME, "oid-alice", "alice@eternalhotels.com", "alice")
    assert back["m365"] == ["linked"] and "alice@eternalhotels.com" in back["note"][0]
    back = _link(client, ms, GASCO, "oid-alice-g", "alice@gascofuel.com")
    assert back["m365"] == ["linked"]
    linked = client.get("/api/m365").json()["accounts"]
    assert [(a["tenant"], a["upn"], a["status"]) for a in linked] == [
        ("Eternal Hotels", "alice@eternalhotels.com", "ok"), ("Gasco", "alice@gascofuel.com", "ok")]
    # The refresh token is stored encrypted, never as text.
    raw = conn.execute("SELECT refresh_enc FROM m365_identities WHERE oid = 'oid-alice'").fetchone()[0]
    assert b"rt-" not in bytes(raw) and apis.decrypt_secret(settings, raw).startswith("rt-")
    # Signing in to Mnemerynne is unchanged: password, as before.
    client.post("/logout")
    _login(client, "alice")
    assert client.get("/api/me").status_code == 200


def test_ad_tenants_only_link_your_own_domain_account(client, ms, world):
    _login(client, "alice")
    back = _link(client, ms, HOME, "oid-bob", "bob@eternalhotels.com", "bob")
    assert back["m365"] == ["error"] and "only link your own account (alice)" in back["note"][0]
    client.post("/logout")
    _login(client, "bob")                      # no domain account linked at all
    back = _link(client, ms, HOME, "oid-bob", "bob@eternalhotels.com", "bob")
    assert back["m365"] == ["error"] and "link your Mnemerynne user to your domain account" in back["note"][0]
    assert _link(client, ms, GASCO, "oid-bob-g", "bob@gascofuel.com")["m365"] == ["linked"]   # cloud: fine


@pytest.mark.parametrize("problem", ["unknown tenant", "state", "nonce", "audience"])
def test_links_that_dont_check_out_are_refused(client, ms, conn, problem):
    _login(client, "alice")
    if problem == "unknown tenant":
        back = _link(client, ms, OTHER, "oid-x", "x@elsewhere.com")
    elif problem == "state":
        back = _link(client, ms, GASCO, "oid-x", "x@gascofuel.com", state="forged")
    elif problem == "nonce":
        back = _link(client, ms, GASCO, "oid-x", "x@gascofuel.com", nonce="replayed")
    else:
        ms.aud = "someone-elses-app"
        back = _link(client, ms, GASCO, "oid-x", "x@gascofuel.com")
    assert back["m365"] == ["error"]
    assert conn.execute("SELECT count(*) FROM m365_identities").fetchone()[0] == 0


def test_an_account_links_to_one_user_only(client, ms, world):
    _login(client, "bob")
    assert _link(client, ms, GASCO, "oid-shared", "frontdesk@gascofuel.com")["m365"] == ["linked"]
    client.post("/logout")
    _login(client, "alice")
    back = _link(client, ms, GASCO, "oid-shared", "frontdesk@gascofuel.com")
    assert back["m365"] == ["error"] and "already linked to another" in back["note"][0]


def test_unlinking(client, ms, conn, world):
    _login(client, "bob")
    _link(client, ms, GASCO, "oid-bob-g", "bob@gascofuel.com")
    bobs = client.get("/api/m365").json()["accounts"][0]["id"]
    client.post("/logout")
    _login(client, "alice")
    assert client.delete(f"/api/m365/accounts/{bobs}").status_code == 404     # not hers
    client.post("/logout")
    _login(client, "bob")
    assert client.delete(f"/api/m365/accounts/{bobs}").json() == {"status": "unlinked"}
    assert client.get("/api/m365").json()["accounts"] == []


# ------------------------------------------------------------------- tokens
def test_tokens_refresh_rotate_and_need_relinking_when_refused(client, ms, conn, settings, world):
    _login(client, "alice")
    _link(client, ms, HOME, "oid-alice", "alice@eternalhotels.com", "alice")
    _link(client, ms, GASCO, "oid-alice-g", "alice@gascofuel.com")
    home_id, gasco_id = [a["id"] for a in client.get("/api/m365").json()["accounts"]]
    m365._user_tokens.clear()
    before = apis.decrypt_secret(settings, conn.execute("SELECT refresh_enc FROM m365_identities WHERE id = ?",
                                                        (home_id,)).fetchone()[0])
    token = m365.access_token(conn, settings, home_id)
    after = apis.decrypt_secret(settings, conn.execute("SELECT refresh_enc FROM m365_identities WHERE id = ?",
                                                       (home_id,)).fetchone()[0])
    assert token.startswith("at-") and after != before                        # rotated
    assert m365.access_token(conn, settings, home_id) == token                 # cached
    assert m365.graph_for(conn, settings, home_id).get("/me")["id"] == "oid-alice"

    ms.refresh.clear()                                                         # Microsoft revokes everything
    m365._user_tokens.clear()
    with pytest.raises(m365.NeedsRelink):
        m365.access_token(conn, settings, gasco_id)
    states = dict(conn.execute("SELECT id, status FROM m365_identities").fetchall())
    assert states == {home_id: "ok", gasco_id: "relink"}                       # only that account
    assert conn.execute("SELECT refresh_enc FROM m365_identities WHERE id = ?", (gasco_id,)).fetchone()[0] is None


# -------------------------------------------------------------------- admin
def test_admin_manages_tenants(client, ms, conn, world):
    _login(client, "alice")
    assert client.get("/api/admin/m365").status_code == 403
    client.post("/logout")
    _login(client, "boss")
    listed = client.get("/api/admin/m365").json()
    assert listed["configured"] and listed["redirect_uri"].endswith("/auth/microsoft/callback")
    assert "adminconsent?client_id=app-id" in listed["tenants"][0]["consent_url"]
    assert client.post("/api/admin/m365/tenants", json={"ref": "nowhere.example", "label": "X"}).status_code == 400
    conn.execute("DELETE FROM m365_tenants WHERE tenant_id = ?", (GASCO,))
    added = client.post("/api/admin/m365/tenants", json={"ref": "gascofuel.com", "label": "Gasco", "mode": "cloud"})
    assert added.status_code == 201
    gasco = next(t for t in added.json()["tenants"] if t["label"] == "Gasco")
    assert gasco["tenant_id"] == GASCO and gasco["domain"] == "gascofuel.com"
    assert client.post("/api/admin/m365/tenants", json={"ref": GASCO}).status_code == 400   # already there

    checked = client.post(f"/api/admin/m365/tenants/{gasco['id']}/check").json()
    assert not checked["ok"] and "AADSTS7000229" in checked["error"]
    ms.consented.add(GASCO)
    assert client.post(f"/api/admin/m365/tenants/{gasco['id']}/check").json()["ok"]

    client.patch(f"/api/admin/m365/tenants/{gasco['id']}", json={"client_id": "own-app", "client_secret": "s3"})
    own = next(t for t in client.get("/api/admin/m365").json()["tenants"] if t["label"] == "Gasco")
    assert own["own_app"] and own["secret_set"] and "client_id=own-app" in own["consent_url"]
    assert "s3" not in json.dumps(own)
    assert client.get("/api/admin/log").json()["entries"][0]["action"] == "m365.tenant"


def test_removing_a_tenant_unlinks_its_accounts(client, ms, conn, world):
    _login(client, "bob")
    _link(client, ms, GASCO, "oid-bob-g", "bob@gascofuel.com")
    client.post("/logout")
    _login(client, "boss")
    client.delete(f"/api/admin/m365/tenants/{world['gasco']}")
    assert conn.execute("SELECT count(*) FROM m365_identities").fetchone()[0] == 0


def test_nothing_shows_until_microsoft_365_is_set_up(client, conn, settings):
    settings.m365_client_id = settings.m365_client_secret = ""
    _login(client, "alice")
    assert client.get("/api/m365").json()["available"] is False
    back = client.get("/m365/link", follow_redirects=False)
    assert "isn't set up" in parse_qs(urlsplit(back.headers["location"]).query)["note"][0]
