import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, permissions, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only", embed_dim=4,
                    ad_domain="ETERNALHOTELS")


@pytest.fixture
def directory():
    return FakeDirectory({"jsmith": {"Domain Users"}, "boss": {"Domain Users", "Domain Admins"},
                          "kdoe": {"Domain Users"}})


@pytest.fixture
def people(conn):
    return {"root": users.create_user(conn, "root", "password123", role="admin"),
            "alice": users.create_user(conn, "alice", "password123")}


def _app(settings, directory):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))


@pytest.fixture
def client(settings, conn, directory, people):
    with TestClient(_app(settings, directory)) as c:
        _login(c, "root")
        yield c


def _login(client, name, password="password123"):
    client.post("/logout")
    return client.post("/login", data={"username": name, "password": password}, follow_redirects=False)


def test_create_users_from_the_admin_page(client, conn, directory):
    r = client.post("/api/admin/users", json={"username": "KDoe", "password": "a-long-password",
                                              "ad_account": "ETERNALHOTELS\\kdoe"})
    assert r.status_code == 201 and r.json()["username"] == "kdoe"
    kdoe = users.get_user(conn, "kdoe")
    assert kdoe["ad_account"] == "kdoe" and kdoe["role"] == "user"
    assert permissions.private_collection_id(conn, kdoe["id"]) is not None
    # Taken names, short passwords and unknown or taken domain accounts are refused, and nothing is made.
    assert client.post("/api/admin/users", json={"username": "kdoe", "password": "a-long-password"}).status_code == 409
    assert client.post("/api/admin/users", json={"username": "bob", "password": "short"}).status_code == 422
    assert client.post("/api/admin/users", json={"username": "bob", "password": "a-long-password",
                                                 "ad_account": "ghost"}).status_code == 422
    assert client.post("/api/admin/users", json={"username": "bob", "password": "a-long-password",
                                                 "ad_account": "kdoe"}).status_code == 422
    assert users.get_user(conn, "bob") is None
    log = client.get("/api/admin/log").json()["entries"]
    assert [e["action"] for e in log[:2]] == ["account.link", "account.create"]
    assert _login(client, "kdoe", "a-long-password").status_code == 303


def test_account_detail_and_list(client, conn, people):
    group = permissions.create_group(conn, "Front desk", [people["alice"]])
    listed = {u["username"]: u for u in client.get("/api/admin/accounts").json()["users"]}
    assert listed["alice"]["groups"] == ["Front desk"] and listed["root"]["admin_via"] == "role"
    d = client.get(f"/api/admin/users/{people['alice']}").json()
    assert d["all_groups"] == [{"id": group, "name": "Front desk", "member": True}] and d["is_you"] is False
    assert d["inbox"] == {"files": 0, "bytes": 0, "claude_enabled": True, "claude_edit": False}
    assert client.get("/api/admin/users/9999").status_code == 404
    _login(client, "alice")
    assert client.get(f"/api/admin/users/{people['alice']}").status_code == 403


def test_reset_password_signs_them_out(settings, conn, directory, people):
    with TestClient(_app(settings, directory)) as admin, TestClient(_app(settings, directory)) as alice:
        _login(admin, "root")
        _login(alice, "alice")
        assert alice.get("/api/admin/log").status_code == 403          # signed in, just not an admin
        assert admin.put(f"/api/admin/users/{people['alice']}/password", json={"password": "x"}).status_code == 422
        assert admin.put(f"/api/admin/users/{people['alice']}/password",
                         json={"password": "new-password-1"}).status_code == 200
        assert alice.get("/api/admin/log").status_code == 401          # her old session ended
        assert _login(alice, "alice").status_code == 401
        assert _login(alice, "alice", "new-password-1").status_code == 303
        # An admin resetting their own password stays signed in.
        assert admin.put(f"/api/admin/users/{people['root']}/password",
                         json={"password": "new-password-2"}).status_code == 200
        assert admin.get("/api/admin/log").status_code == 200


def test_role_active_and_groups(client, conn, people, directory):
    alice = people["alice"]
    assert client.patch(f"/api/admin/users/{alice}", json={"role": "admin"}).json()["admin_via"] == "role"
    assert client.patch(f"/api/admin/users/{alice}", json={"active": False}).json()["active"] is False
    assert users.authenticate(conn, "alice", "password123") is None
    client.patch(f"/api/admin/users/{alice}", json={"active": True, "role": "user"})
    assert users.get_user_by_id(conn, alice)["role"] == "user"
    # You can't lock yourself out.
    root = people["root"]
    assert client.patch(f"/api/admin/users/{root}", json={"active": False}).status_code == 422
    assert client.patch(f"/api/admin/users/{root}", json={"role": "user"}).status_code == 422
    # ...unless the domain admin group keeps you an admin.
    accounts.link(conn, root, "boss", directory)
    assert client.patch(f"/api/admin/users/{root}", json={"role": "user"}).json()["admin_via"] == "domain"
    a = permissions.create_group(conn, "A", [])
    b = permissions.create_group(conn, "B", [alice])
    assert client.put(f"/api/admin/users/{alice}/groups", json={"groups": [a]}).json() == {"groups": ["A"]}
    assert permissions.group_member_ids(conn, b) == [] and permissions.group_member_ids(conn, a) == [alice]
    assert client.put(f"/api/admin/users/{alice}/groups", json={"groups": [999]}).status_code == 404
