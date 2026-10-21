from mnemerynne import db, permissions, users


def _login(client, username="alice", password="password123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "schema": len(db._migration_files())}


def test_inbox_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/me").status_code == 401


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200 and "Erynne" in r.text


def test_login_and_inbox(client, conn):
    alice = users.create_user(conn, "alice", "password123")
    permissions.grant_user(conn, permissions.create_library(conn, "BrandResBW"), alice)

    assert _login(client, password="wrong").status_code == 401
    r = _login(client, username="ALICE")
    assert r.status_code == 303 and r.headers["location"] == "/"

    page = client.get("/files")
    assert page.status_code == 200 and "files.js" in page.text
    locations = client.get("/api/locations").json()["locations"]
    assert [(l["kind"], l["name"], l["writable"]) for l in locations] == [
        ("private", "My files", True), ("library", "BrandResBW", False)]
    assert [c["kind"] for c in client.get("/api/me").json()["collections"]] == ["private", "library"]


def test_lockout_after_repeated_failures(client, conn):
    users.create_user(conn, "alice", "password123")
    codes = [_login(client, password="wrong").status_code for _ in range(5)]
    assert codes == [401] * 5
    assert _login(client).status_code == 429  # even the right password, while locked


def test_disabled_or_deleted_user_loses_session(client, conn):
    users.create_user(conn, "alice", "password123")
    _login(client)
    assert client.get("/api/me").status_code == 200
    users.set_active(conn, "alice", False)
    assert client.get("/api/me").status_code == 401
    users.set_active(conn, "alice", True)
    _login(client)
    users.delete_user(conn, "alice")
    assert client.get("/api/me").status_code == 401


def test_logout(client, conn):
    users.create_user(conn, "alice", "password123")
    _login(client)
    client.post("/logout")
    assert client.get("/api/me").status_code == 401
