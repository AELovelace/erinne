"""Linking Microsoft 365 accounts: tenants, the link round trip, checks, tokens."""

import base64
import json
import re
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


# ------------------------------------------------- the shared app, set live on the Access page
LIVE_APP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_the_shared_app_is_set_live_from_access_with_no_restart(client, ms, conn, settings, world):
    settings.m365_client_id = settings.m365_client_secret = ""              # nothing in the server's settings file
    _login(client, "alice")
    assert client.get("/api/m365").json()["available"] is False
    assert client.put("/api/admin/m365/shared-app", json={"client_id": LIVE_APP, "client_secret": "s3cret"}).status_code == 403   # admins only
    client.post("/logout")
    _login(client, "boss")
    before = client.get("/api/admin/m365").json()
    assert not before["configured"] and before["shared_app"] == {"source": None, "client_id": "", "unreadable": False}
    assert before["tenants"][0]["consent_url"] is None
    for bad, why in (({"client_id": "not-an-id", "client_secret": "s3cret"}, r"Application \(client\) ID"),
                     ({"client_id": LIVE_APP, "client_secret": "   "}, "client secret"),
                     ({"client_id": LIVE_APP, "client_secret": HOME}, "secret's ID")):        # the Secret ID pasted for its Value
        refused = client.put("/api/admin/m365/shared-app", json=bad)
        assert refused.status_code == 400 and re.search(why, refused.json()["detail"]), refused.text
    saved = client.put("/api/admin/m365/shared-app", json={"client_id": LIVE_APP.upper(), "client_secret": " s3cret "})
    assert saved.status_code == 200, saved.text
    now = saved.json()
    assert now["configured"] and now["shared_app"] == {"source": "access", "client_id": LIVE_APP, "unreadable": False}
    assert f"adminconsent?client_id={LIVE_APP}" in now["tenants"][0]["consent_url"]
    assert "s3cret" not in saved.text and "s3cret" not in client.get("/api/admin/m365").text         # the secret never comes back
    stored = conn.execute("SELECT value FROM meta WHERE key = ?", (m365.SHARED_KEY,)).fetchone()["value"]
    assert "s3cret" not in stored and LIVE_APP in stored                                            # and is kept encrypted
    assert (settings.m365_client_id, settings.m365_client_secret) == ("", "")                        # the server's own settings are untouched
    entry = client.get("/api/admin/log").json()["entries"][0]
    assert entry["action"] == "m365.shared_app" and "s3cret" not in json.dumps(entry)

    seen = []                                                                # what Microsoft is sent from now on
    real = ms.handler
    def spy(request):
        if str(request.url).endswith("/oauth2/v2.0/token"):
            form = parse_qs(request.content.decode())
            seen.append((form["client_id"][0], form["client_secret"][0]))
        return real(request)
    m365.TRANSPORT = httpx.MockTransport(spy)
    assert client.post(f"/api/admin/m365/tenants/{world['home']}/check").json()["ok"]                # the app's own sign-in uses it at once
    assert seen[-1] == (LIVE_APP, "s3cret")
    client.post("/logout")
    _login(client, "alice")                                                  # and so does linking, for everyone
    assert client.get("/api/m365").json()["available"] is True
    ms.aud = LIVE_APP
    assert _link(client, ms, HOME, "oid-alice", "alice@eternalhotels.com", "alice")["m365"] == ["linked"]
    assert seen[-1] == (LIVE_APP, "s3cret")


def test_access_wins_over_the_settings_file_and_removing_it_falls_back(client, ms, conn, settings, world):
    _login(client, "boss")                                                   # the fixture's settings file has app-id / app-secret
    assert client.get("/api/admin/m365").json()["shared_app"] == {"source": "server", "client_id": "app-id", "unreadable": False}
    assert m365.app_token(m365.live(conn, settings), m365.tenant(conn, world["home"])) == "app-" + HOME
    assert m365._app_tokens[HOME][2] == "app-id"
    client.put("/api/admin/m365/shared-app", json={"client_id": LIVE_APP, "client_secret": "s3cret"})
    assert m365.credentials(m365.live(conn, settings), None) == (LIVE_APP, "s3cret")
    assert HOME not in m365._app_tokens                                      # tokens given to the old app are dropped
    # Another process (the worker) still holds a token the old app was given: it is not used for the new app.
    m365._app_tokens[HOME] = ("stale", time.time() + 3000, "app-id")
    assert m365.app_token(m365.live(conn, settings), m365.tenant(conn, world["home"])) == "app-" + HOME
    assert m365._app_tokens[HOME][2] == LIVE_APP
    # A tenant's own app is never replaced by the shared one.
    client.patch(f"/api/admin/m365/tenants/{world['gasco']}", json={"client_id": "own-app", "client_secret": "s3"})
    assert m365.credentials(m365.live(conn, settings), m365.tenant(conn, world["gasco"])) == ("own-app", "s3")
    # A secret this server can no longer read (its secret key changed) falls back, and Access says so.
    conn.execute("UPDATE meta SET value = ? WHERE key = ?", (json.dumps({"client_id": LIVE_APP, "secret_enc": "garbage"}), m365.SHARED_KEY))
    assert client.get("/api/admin/m365").json()["shared_app"] == {"source": "server", "client_id": "app-id", "unreadable": True}
    assert m365.credentials(m365.live(conn, settings), None) == ("app-id", "app-secret")
    gone = client.delete("/api/admin/m365/shared-app")
    assert gone.status_code == 200 and gone.json()["shared_app"] == {"source": "server", "client_id": "app-id", "unreadable": False}
    assert conn.execute("SELECT 1 FROM meta WHERE key = ?", (m365.SHARED_KEY,)).fetchone() is None
    client.post("/logout")
    _login(client, "alice")
    assert client.delete("/api/admin/m365/shared-app").status_code == 403
