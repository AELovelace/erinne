"""Entra groups next to AD groups: admin, agent, API and library access."""

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, apis, group_refs, m365, permissions, users
from mnemerynne.web.app import Services, create_app

GASCO = "22222222-2222-2222-2222-222222222222"
AGENTS, IT, DESK = "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002", \
    "aaaaaaaa-0000-0000-0000-000000000003"


class FakeEntra:
    def __init__(self):
        self.groups = {AGENTS: "Erynne Agents", IT: "IT Admins", DESK: "Front Desk"}
        self.members = {"oid-alice": [AGENTS, DESK]}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            if self.down:
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "AADSTS: no consent"})
            return httpx.Response(200, json={"access_token": "app", "expires_in": 3600})
        if "/groups" in url and "/users/" not in url:
            # Two pages, to follow @odata.nextLink.
            items = [{"id": k, "displayName": v} for k, v in self.groups.items()]
            if "page2" in url:
                return httpx.Response(200, json={"value": items[2:]})
            return httpx.Response(200, json={"value": items[:2],
                                             "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups?page2=1"})
        if "/transitiveMemberOf/" in url:
            oid = url.split("/users/")[1].split("/")[0]
            return httpx.Response(200, json={"value": [{"id": g} for g in self.members.get(oid, [])]})
        return httpx.Response(404, json={"error": {"message": "not faked"}})


@pytest.fixture
def entra(monkeypatch):
    fake = FakeEntra()
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(fake.handler))
    m365._app_tokens.clear()
    return fake


@pytest.fixture
def world(settings, conn, entra):
    settings.m365_client_id, settings.m365_client_secret = "app-id", "app-secret"
    directory = FakeDirectory({"boss": {"domain admins"}, "carol": {"staff"}}, groups=["Domain Admins", "staff"])
    ids = {n: users.create_user(conn, n, "password123") for n in ("alice", "bob", "boss", "carol")}
    accounts.link(conn, ids["boss"], "boss", directory)
    accounts.link(conn, ids["carol"], "carol", directory)
    tenant = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Gasco', 'cloud')",
                          (GASCO,)).lastrowid
    conn.execute("INSERT INTO m365_identities(user_id, tenant_row, oid, upn) VALUES (?, ?, 'oid-alice', "
                 "'alice@gascofuel.com')", (ids["alice"], tenant))
    conn.execute("INSERT INTO m365_identities(user_id, tenant_row, oid, upn) VALUES (?, ?, 'oid-bob', "
                 "'bob@gascofuel.com')", (ids["bob"], tenant))
    stats = m365.refresh_groups(conn, settings)
    assert stats == {"tenants": 1, "groups": 3, "accounts": 2, "errors": []}
    return {**ids, "directory": directory, "tenant": tenant}


@pytest.fixture
def client(settings, world):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(),
                                                  directory=world["directory"]))) as c:
        c.post("/login", data={"username": "boss", "password": "password123"})
        yield c


def _user(conn, uid):
    return users.get_user_by_id(conn, uid)


def test_refresh_names_groups_and_memberships(conn, world):
    names = dict(conn.execute("SELECT group_key, name FROM entra_groups").fetchall())
    assert names[group_refs.key(GASCO, DESK)] == "Front Desk" and len(names) == 3   # both pages
    alice = group_refs.member_of(conn, _user(conn, world["alice"]), world["directory"])
    assert alice == {group_refs.key(GASCO, AGENTS), group_refs.key(GASCO, DESK)}
    assert group_refs.member_of(conn, _user(conn, world["bob"]), world["directory"]) == set()


def test_settings_take_ad_and_entra_groups(client, conn, world):
    assert "Gasco / Erynne Agents" in client.get("/api/admin/settings/agent").json()["groups"]
    saved = client.put("/api/admin/settings/agent-group", json={"group": "staff; Gasco / Erynne Agents"}).json()
    assert saved["group"] == "staff; Gasco / Erynne Agents" and saved["members"] == ["alice", "carol"]
    assert accounts.agent_group(conn) == f"staff; {group_refs.key(GASCO, AGENTS)}"         # stored by id
    assert accounts.is_agent_user(conn, _user(conn, world["alice"]), world["directory"])    # through Entra
    assert accounts.is_agent_user(conn, _user(conn, world["carol"]), world["directory"])    # through AD
    assert not accounts.is_agent_user(conn, _user(conn, world["bob"]), world["directory"])
    bad = client.put("/api/admin/settings/agent-group", json={"group": "Gasco / Nope"})
    assert bad.status_code == 422 and "no group called “Nope”" in bad.json()["detail"]

    # Admin rights from an Entra group, while the admin keeps theirs (no lockout).
    entra = world["directory"]
    out = client.put("/api/admin/settings/admin-group", json={"group": "Domain Admins; Gasco / Front Desk"}).json()
    assert out["group"] == "Domain Admins; Gasco / Front Desk"
    assert accounts.admin_via(conn, _user(conn, world["alice"]), entra) == "domain"
    assert client.put("/api/admin/settings/admin-group", json={"group": "Gasco / IT Admins"}).status_code == 422


def test_api_access_and_library_grants_follow_entra_groups(client, conn, world):
    api_id = conn.execute("INSERT INTO apis(name, base_url, ad_group) VALUES ('Tickets', 'https://t.test', '')").lastrowid
    conn.execute("INSERT INTO api_operations(api_id, name, path) VALUES (?, 'List', '/list')", (api_id,))
    assert client.patch(f"/api/admin/apis/{api_id}", json={"ad_group": "Gasco / Front Desk"}).json() == {
        "status": "saved"}
    assert client.get(f"/api/admin/apis/{api_id}").json()["ad_group"] == "Gasco / Front Desk"
    alice = conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (world["alice"],)).fetchone()
    bob = conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (world["bob"],)).fetchone()
    assert [o["name"] for o in apis.allowed_operations(conn, alice, world["directory"])] == ["List"]
    assert apis.allowed_operations(conn, bob, world["directory"]) == []

    lib = permissions.create_library(conn, "Gasco Docs")
    group = permissions.create_group(conn, "Gasco desk")
    permissions.grant_group(conn, lib, group)
    assert lib not in permissions.readable_collection_ids(conn, world["alice"])
    linked = client.put(f"/api/admin/groups/{group}/entra", json={"group": "Gasco / Front Desk"}).json()
    assert linked["entra"] == "Gasco / Front Desk"
    assert next(g for g in client.get("/api/admin/groups").json()["groups"] if g["id"] == group)["entra"] == \
        "Gasco / Front Desk"
    assert lib in permissions.readable_collection_ids(conn, world["alice"])
    assert lib not in permissions.readable_collection_ids(conn, world["bob"])
    assert client.put(f"/api/admin/groups/{group}/entra", json={"group": "staff"}).status_code == 422   # AD: no
    client.put(f"/api/admin/groups/{group}/entra", json={"group": ""})
    assert lib not in permissions.readable_collection_ids(conn, world["alice"])


def test_stale_memberships_dont_count(conn, settings, world, entra):
    alice = _user(conn, world["alice"])
    conn.execute("UPDATE entra_memberships SET refreshed_at = datetime('now', '-31 minutes')")
    assert group_refs.member_of(conn, alice, world["directory"]) == set()
    entra.down = True                                       # the tenant stops answering: stays stale
    m365._app_tokens.clear()
    assert m365.refresh_groups(conn, settings)["errors"]
    assert group_refs.member_of(conn, alice, world["directory"]) == set()
    assert "no consent" in conn.execute("SELECT consent_error FROM m365_tenants").fetchone()[0]
    entra.down = False
    m365.refresh_groups(conn, settings)
    assert group_refs.member_of(conn, alice, world["directory"])
    # A switched-off tenant's groups don't count either.
    conn.execute("UPDATE m365_tenants SET enabled = 0")
    assert group_refs.member_of(conn, alice, world["directory"]) == set()


def test_old_single_group_settings_still_work(conn, world):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('agent_group', 'staff')")
    assert accounts.is_agent_user(conn, _user(conn, world["carol"]), world["directory"])
    assert group_refs.label(conn, "staff") == "staff"
