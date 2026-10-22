import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret", embed_dim=4,
                    ad_domain="ETERNALHOTELS")


@pytest.fixture
def directory():
    return FakeDirectory({"jsmith": {"Domain Users"}, "boss": {"Domain Users", "Domain Admins"}},
                         groups=["Domain Admins", "Domain Users", "Front Desk"])


@pytest.fixture
def client(settings, conn, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        yield c


def _login(client, name):
    r = client.post("/login", data={"username": name, "password": "password123"}, follow_redirects=False)
    assert r.status_code == 303


def test_link_checks_the_domain_and_keeps_links_unique(conn, directory):
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    with pytest.raises(accounts.LinkError, match="no domain account 'ghost'"):
        accounts.link(conn, alice, "ghost", directory)
    assert accounts.link(conn, alice, "ETERNALHOTELS\\JSmith", directory) == "JSmith"
    with pytest.raises(accounts.LinkError, match="already linked to alice"):
        accounts.link(conn, bob, "jsmith", directory)
    assert [r["username"] for r in accounts.list_users(conn)] == ["bob", "alice"]  # unlinked first
    assert accounts.unlink(conn, alice) and not accounts.unlink(conn, alice)


def test_admin_rights_follow_the_chosen_ad_group(client, conn, directory):
    accounts.link(conn, users.create_user(conn, "boss", "password123"), "boss", directory)
    accounts.link(conn, users.create_user(conn, "jane", "password123"), "jsmith", directory)

    _login(client, "jane")
    assert client.get("/admin/libraries").status_code == 403
    assert client.get("/api/admin/accounts").status_code == 403

    _login(client, "boss")
    assert client.get("/admin/libraries").status_code == 200
    got = client.get("/api/admin/settings/admin-group").json()
    assert got["group"] == "Domain Admins" and "Front Desk" in got["groups"]

    # A group the admin isn't in would lock them out; an unknown group is refused.
    assert client.put("/api/admin/settings/admin-group", json={"group": "Front Desk"}).status_code == 422
    assert client.put("/api/admin/settings/admin-group", json={"group": "Nope"}).status_code == 422

    directory.members["jsmith"].add("front desk")
    directory.members["boss"].add("front desk")
    assert client.put("/api/admin/settings/admin-group",
                      json={"group": "ETERNALHOTELS\\Front Desk"}).json() == {"group": "Front Desk"}
    _login(client, "jane")
    assert client.get("/admin/libraries").status_code == 200


def test_local_admin_role_still_works_without_ad(client, conn):
    users.create_user(conn, "root", "password123", role="admin")
    _login(client, "root")
    assert client.get("/admin/libraries").status_code == 200


def test_admin_links_and_unlinks_users(client, conn, directory):
    boss = users.create_user(conn, "boss", "password123")
    accounts.link(conn, boss, "boss", directory)
    jane = users.create_user(conn, "jane", "password123")
    _login(client, "boss")

    listed = client.get("/api/admin/accounts").json()
    assert listed["domain_accounts"] == ["boss", "jsmith"] and listed["domain"] == "ETERNALHOTELS"
    assert [(u["username"], u["ad_account"]) for u in listed["users"]] == [("jane", None), ("boss", "boss")]

    url = f"/api/admin/users/{jane}/domain-account"
    assert client.put(url, json={"ad_account": "ghost"}).status_code == 422
    assert client.put(url, json={"ad_account": "jsmith"}).json() == {"status": "linked", "ad_account": "jsmith"}
    assert client.get("/api/admin/log").json()["entries"][0]["action"] == "account.link"

    # Unlinking yourself would drop the admin rights that come from AD.
    assert client.delete(f"/api/admin/users/{boss}/domain-account").status_code == 422
    assert client.delete(url).json() == {"status": "unlinked"}
    assert users.get_user_by_id(conn, jane)["ad_account"] is None
