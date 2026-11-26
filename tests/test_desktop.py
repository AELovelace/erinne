"""The desktop app's server side (web/desktop.py, desktop.py, drive.py): who may sign it in, the tickets that sign
its tabs in, the update feed, and the Explorer drive's API (listing, bytes, saving with versions, the change feed)."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from mnemerynne import accounts, api_tokens, db, desktop, drive, fs, permissions, share, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app

APP = "http://127.0.0.1:53682/callback"          # the app's loopback redirect (never actually listened on)
BASE = "https://mnemerynne.test"


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "storage" / "share"
    path.mkdir(parents=True)
    (path / share.MARKER).write_text("")
    return path


@pytest.fixture
def settings(tmp_path, root):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0,
                    public_url=BASE)


@pytest.fixture
def directory():
    return FakeDirectory({"jsmith": {"Domain Users", "Desktop Users"}, "rdoe": {"Domain Users"}})


@pytest.fixture
def conn(settings, directory, root):
    share.configure(settings, directory, FakeShareChecker(root))
    db.migrate_all(settings.db_path, settings.embed_dim, only=("search", "assistant", "logs", "automations", "sync"))
    connection = db.connect(settings.db_path)
    yield connection
    connection.close()


@pytest.fixture
def client(settings, conn, directory, root):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=FakeShareChecker(root))), base_url=BASE) as c:
        yield c


def _person(conn, directory, name="jane", account="jsmith"):
    uid = users.create_user(conn, name, "password123")
    accounts.link(conn, uid, account, directory)
    return uid


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _sign_in_app(client, name="jane"):
    """The app's sign-in: register, the person signs in and approves, the code is swapped. Returns the token answer,
    or the consent page's response when it refused."""
    client_id = client.post("/oauth/register", json={"redirect_uris": [APP], "client_name": "Desktop app"}).json()["client_id"]
    verifier, challenge = _pkce()
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": APP, "state": "s1",
             "code_challenge": challenge, "code_challenge_method": "S256", "scope": "desktop"}
    client.post("/login", data={"username": name, "password": "password123"}, follow_redirects=False)
    page = client.get("/oauth/authorize?" + urlencode(query))
    if page.status_code != 200 or "desktop app</strong> wants to sign in" not in page.text:
        return page
    nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
    done = client.post("/oauth/authorize", data={"nonce": nonce, "decision": "allow"}, follow_redirects=False)
    code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]
    got = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": APP,
                                            "client_id": client_id, "code_verifier": verifier})
    assert got.status_code == 200, got.text
    return got.json()


@pytest.fixture
def app_auth(client, conn, directory):
    """jane, in the Desktop app group, with the app signed in; then only the app's token (no cookie)."""
    desktop.set_group(conn, "Desktop Users")
    uid = _person(conn, directory)
    tokens = _sign_in_app(client)
    assert tokens["scope"] == "desktop"
    client.cookies.clear()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, uid


# ------------------------------------------------------------------- who may
def test_only_the_desktop_group_may_sign_the_app_in(client, conn, directory):
    _person(conn, directory, "rob", "rdoe")
    refused = _sign_in_app(client, "rob")                        # no group set: the app is off
    assert refused.status_code == 403 and "open for you" in refused.text
    desktop.set_group(conn, "Desktop Users")
    refused = _sign_in_app(client, "rob")                        # a group, but rob isn't in it
    assert refused.status_code == 403


def test_desktop_tokens_only_work_on_the_desktop_routes(client, conn, app_auth):
    auth, uid = app_auth
    assert client.get("/api/drive/libraries", headers=auth).status_code == 200
    assert client.get("/api/desktop/me", headers=auth).json()["username"] == "jane"
    token = auth["Authorization"][7:]
    assert api_tokens.verify(conn, token) is None                # not an MCP or a personal token
    _, mcp = api_tokens.create(conn, uid, "Claude Desktop")
    assert client.get("/api/drive/libraries", headers={"Authorization": f"Bearer {mcp}"}).status_code == 401
    assert client.get("/api/drive/libraries").status_code == 401  # nothing at all
    # Out of the group: the token already issued stops working at once.
    desktop.set_group(conn, "")
    assert client.get("/api/drive/libraries", headers=auth).status_code == 403


# --------------------------------------------------------------- the tickets
def test_a_ticket_signs_a_tab_in_once(client, conn, app_auth):
    auth, _ = app_auth
    got = client.post("/api/desktop/session-ticket", headers=auth).json()
    assert got["signin_url"].startswith(f"{BASE}/desktop/signin?t=mdt_")
    r = client.get("/desktop/signin", params={"t": got["ticket"], "next": "/files"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/files"
    assert client.get("/preferences").status_code == 200        # the tab is signed in now
    client.cookies.clear()
    again = client.get("/desktop/signin", params={"t": got["ticket"]}, follow_redirects=False)
    assert again.headers["location"] == "/login"                 # used: the normal sign-in instead


def test_a_ticket_never_sends_a_tab_to_another_site(client, app_auth):
    auth, _ = app_auth
    ticket = client.post("/api/desktop/session-ticket", headers=auth).json()["ticket"]
    r = client.get("/desktop/signin", params={"t": ticket, "next": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_a_ticket_dies_with_its_connection(client, conn, app_auth):
    auth, uid = app_auth
    ticket = client.post("/api/desktop/session-ticket", headers=auth).json()["ticket"]
    conn.execute("UPDATE oauth_grants SET revoked_at = datetime('now') WHERE user_id = ?", (uid,))
    assert desktop.use_ticket(conn, ticket) is None


# ------------------------------------------------------------ the app itself
def test_info_offers_the_newest_build_with_its_hash(client, conn, settings, directory):
    folder = settings.data_dir / desktop.BUILD_DIR
    folder.mkdir(parents=True)
    (folder / "Mnemerynne-Desktop-Setup-0.1.0.exe").write_bytes(b"old")
    (folder / "Mnemerynne-Desktop-Setup-0.10.0.exe").write_bytes(b"newest")   # numbers, not text, decide
    (folder / "notes.txt").write_text("ignored")
    got = client.get("/api/desktop/info").json()
    assert got["latest"] == "0.10.0" and got["download"]["sha256"] == hashlib.sha256(b"newest").hexdigest()
    assert got["product_name"] and got["icon"].startswith(BASE)
    url = got["download"]["url"].removeprefix(BASE)
    assert client.get(url).status_code == 401                    # nobody signed in
    desktop.set_group(conn, "Desktop Users")
    _person(conn, directory)
    client.post("/login", data={"username": "jane", "password": "password123"})
    r = client.get(url)
    assert r.status_code == 200 and r.content == b"newest"
    assert client.get("/downloads/desktop/..%2Fsecret").status_code == 404


def test_the_page_knows_it_is_in_the_app(client, conn, directory):
    _person(conn, directory)
    client.post("/login", data={"username": "jane", "password": "password123"})
    assert 'id="install-box"' in client.get("/files").text
    inside = client.get("/files", headers={"User-Agent": "Mozilla/5.0 Edg/131.0 MnemerynneDesktop/0.1.0"}).text
    assert 'id="install-box"' not in inside
    assert "data-desktop-app" in inside and "data-desktop-app" not in client.get("/files").text   # bar hidden (erynne.css)
    assert 'id="nav-more"' in inside                    # the menu stays in the page, for the app to read


# ---------------------------------------------------------------- the drive
def _box(conn, uid):
    return permissions.private_collection_id(conn, uid)


def _new(client, auth, cid, name, data, folder_id=None):
    params = {"collection_id": cid, "name": name, **({"folder_id": folder_id} if folder_id else {})}
    r = client.post("/api/drive/files", params=params, content=data, headers=auth)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_the_drive_lists_reads_and_saves_with_versions(client, conn, app_auth):
    auth, uid = app_auth
    cid = _box(conn, uid)
    libs = client.get("/api/drive/libraries", headers=auth).json()["libraries"]
    assert any(lib["id"] == cid and lib["name"] == "File box" for lib in libs)
    made = _new(client, auth, cid, "notes.txt", b"hello drive")
    assert made["sha256"] == hashlib.sha256(b"hello drive").hexdigest() and made["size"] == 11
    listed = client.get("/api/drive/list", params={"collection_id": cid}, headers=auth).json()
    assert [f["name"] for f in listed["files"]] == ["notes.txt"]

    got = client.get(f"/api/drive/files/{made['id']}/content", headers=auth)
    assert got.content == b"hello drive" and got.headers["etag"] == f'"{made["sha256"]}"'
    part = client.get(f"/api/drive/files/{made['id']}/content", headers={**auth, "Range": "bytes=6-10"})
    assert part.status_code == 206 and part.content == b"drive"

    saved = client.put(f"/api/drive/files/{made['id']}/content", content=b"hello again",
                       headers={**auth, "If-Match": f'"{made["sha256"]}"'})
    assert saved.status_code == 200, saved.text
    assert saved.json()["sha256"] == hashlib.sha256(b"hello again").hexdigest()
    kept = conn.execute("SELECT via, sha256 FROM file_versions WHERE file_id = ?", (made["id"],)).fetchall()
    assert [(k["via"], k["sha256"]) for k in kept] == [("drive", made["sha256"])]   # the old one, kept
    assert client.get(f"/api/drive/files/{made['id']}/content", headers=auth).content == b"hello again"


def test_a_save_over_someone_elses_change_is_refused(client, conn, app_auth):
    auth, uid = app_auth
    made = _new(client, auth, _box(conn, uid), "plan.txt", b"v1")
    first = client.put(f"/api/drive/files/{made['id']}/content", content=b"v2",
                       headers={**auth, "If-Match": made["sha256"]})
    assert first.status_code == 200
    stale = client.put(f"/api/drive/files/{made['id']}/content", content=b"v2 from another PC",
                       headers={**auth, "If-Match": made["sha256"]})         # still the old version
    assert stale.status_code == 409
    assert client.put(f"/api/drive/files/{made['id']}/content", content=b"x", headers=auth).status_code == 428


def test_rename_move_and_bin_from_the_drive(client, conn, app_auth):
    auth, uid = app_auth
    cid = _box(conn, uid)
    made = _new(client, auth, cid, "a.txt", b"a")
    folder = client.post("/api/drive/folders", json={"collection_id": cid, "name": "Reports"}, headers=auth).json()
    assert client.patch(f"/api/drive/files/{made['id']}", json={"name": "b.txt"}, headers=auth).json()["name"] == "b.txt"
    moved = client.post(f"/api/drive/files/{made['id']}/move", json={"collection_id": cid, "folder_id": folder["id"]},
                        headers=auth)
    assert moved.status_code == 200, moved.text
    inside = client.get("/api/drive/list", params={"collection_id": cid, "folder_id": folder["id"]}, headers=auth).json()
    assert [f["name"] for f in inside["files"]] == ["b.txt"]
    assert client.delete(f"/api/drive/files/{made['id']}", headers=auth).status_code == 200
    assert conn.execute("SELECT deleted_at IS NOT NULL FROM files WHERE id = ?", (made["id"],)).fetchone()[0]
    assert client.get(f"/api/drive/files/{made['id']}/content", headers=auth).status_code == 404   # binned
    assert client.delete("/api/drive/things/1", headers=auth).status_code == 404


def test_the_change_feed_reports_each_items_state_after_the_cursor(client, conn, app_auth):
    auth, uid = app_auth
    cid = _box(conn, uid)
    start = client.get("/api/drive/list", params={"collection_id": cid}, headers=auth).json()["cursor"]
    made = _new(client, auth, cid, "feed.txt", b"1")
    folder = client.post("/api/drive/folders", json={"collection_id": cid, "name": "Docs"}, headers=auth).json()
    feed = client.get("/api/drive/changes", params={"collection_id": cid, "cursor": start}, headers=auth).json()
    seen = {(c["type"], c["id"]): c for c in feed["changes"]}
    assert seen[("file", made["id"])]["name"] == "feed.txt" and not seen[("file", made["id"])]["deleted"]
    assert seen[("folder", folder["id"])]["name"] == "Docs" and feed["more"] is False
    # Nothing new: the same cursor comes back empty; a rename and a bin show as the item's state now.
    assert client.get("/api/drive/changes", params={"collection_id": cid, "cursor": feed["cursor"]},
                      headers=auth).json()["changes"] == []
    client.patch(f"/api/drive/folders/{folder['id']}", json={"name": "Documents"}, headers=auth)
    client.delete(f"/api/drive/files/{made['id']}", headers=auth)
    later = client.get("/api/drive/changes", params={"collection_id": cid, "cursor": feed["cursor"]},
                       headers=auth).json()["changes"]
    assert {(c["type"], c["name"], c["deleted"]) for c in later} == {("folder", "Documents", False),
                                                                     ("file", "feed.txt", True)}


def test_the_feed_pages_without_losing_anything(client, conn, app_auth):
    auth, uid = app_auth
    cid = _box(conn, uid)
    start = drive.cursor_now(conn)
    ids = {_new(client, auth, cid, f"f{i}.txt", str(i).encode())["id"] for i in range(5)}
    access = fs.Access.of(conn, uid)
    seen, cursor, more = set(), start, True
    while more:
        page = drive.changes(conn, access, cid, cursor, limit=2)   # small pages, to cross the cut
        seen |= {c["id"] for c in page["changes"] if c["type"] == "file"}
        cursor, more = page["cursor"], page["more"]
    assert ids <= seen


def test_someone_elses_library_is_not_on_the_drive(client, conn, directory, app_auth):
    auth, _ = app_auth
    other = _person(conn, directory, "rob", "rdoe")
    theirs = _box(conn, other)
    assert client.get("/api/drive/list", params={"collection_id": theirs}, headers=auth).status_code == 404
    assert client.get("/api/drive/changes", params={"collection_id": theirs}, headers=auth).status_code == 404


def test_preferences_lists_and_signs_out_computers(client, conn, app_auth):
    auth, _ = app_auth
    client.post("/login", data={"username": "jane", "password": "password123"})
    got = client.get("/api/desktop/connections").json()
    assert len(got["computers"]) == 1 and got["download"] is None
    assert client.delete(f"/api/desktop/connections/{got['computers'][0]['id']}").status_code == 200
    assert client.get("/api/drive/libraries", headers=auth).status_code == 401   # that computer is signed out
