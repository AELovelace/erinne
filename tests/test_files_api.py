import pytest

from mnemerynne import permissions, users, worker
from fakes import FakeEmbedder
from pdfgen import make_pdf

PDF = make_pdf([["Night Audit", "Run the night audit after midnight."]], [("Night Audit", 0, 0)])
MD = b"# Checkout\n\nCheckout is at 11am.\n\n<script>alert('x')</script>\n"


@pytest.fixture
def accounts(conn):
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    return {"alice": alice, "bob": bob}


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def _upload(client, data, name, collection_id=None):
    form = {} if collection_id is None else {"collection_id": str(collection_id)}
    return client.post("/api/files", files={"file": (name, data, "application/octet-stream")}, data=form)


def test_upload_list_and_dedupe(client, accounts):
    _login(client, "alice")
    r = _upload(client, PDF, "audit.pdf")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued" and body["kind"] == "pdf" and body["can_delete"]
    assert body["view_url"] == f"/files/{body['id']}/pdf"

    again = _upload(client, PDF, "audit copy.pdf")
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    assert [f["name"] for f in client.get("/api/files").json()["files"]] == ["audit.pdf"]


@pytest.mark.parametrize("data, name, why", [
    (b"MZ fake exe", "invoice.pdf", None),             # not a PDF at all: just a file
    (b"PK\x03\x04 zip", "photos.zip", None),
    ("caf\xe9".encode("latin-1"), "notes.md", "UTF-8"),  # looks indexable but isn't
])
def test_unsearchable_files_are_stored(client, accounts, data, name, why):
    _login(client, "alice")
    r = _upload(client, data, name)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "stored" and body["kind"] == "other"
    if why:
        assert why in body["notes"]


def test_empty_upload_rejected(client, accounts):
    _login(client, "alice")
    assert _upload(client, b"", "empty.md").status_code == 400


def test_too_large(client, accounts, settings):
    settings.max_file_bytes = 16
    _login(client, "alice")
    assert _upload(client, b"# " + b"x" * 100, "big.md").status_code == 413


def test_library_upload_needs_write_grant(client, conn, accounts):
    lib = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, lib, accounts["alice"])
    _login(client, "alice")
    assert _upload(client, PDF, "a.pdf", lib).status_code == 403
    permissions.grant_user(conn, lib, accounts["alice"], can_write=True)
    assert _upload(client, PDF, "a.pdf", lib).status_code == 201


def test_other_users_cannot_see_or_touch_private_files(client, accounts):
    _login(client, "alice")
    file_id = _upload(client, MD, "notes.md").json()["id"]
    client.post("/logout")
    _login(client, "bob")
    assert client.get("/api/files").json()["files"] == []
    for method, url in [("GET", f"/api/files/{file_id}"), ("GET", f"/files/{file_id}/view"),
                        ("GET", f"/files/{file_id}/raw"), ("DELETE", f"/api/files/{file_id}")]:
        assert client.request(method, url).status_code == 404, url
    alice_inbox = client.get("/api/me").json()["collections"][0]["id"] - 1  # alice's inbox id
    assert _upload(client, PDF, "sneaky.pdf", alice_inbox).status_code == 403


def test_markdown_view_is_safe(client, accounts):
    _login(client, "alice")
    file_id = _upload(client, MD, "notes.md").json()["id"]
    r = client.get(f"/files/{file_id}/view")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert 'id="checkout"' in r.text
    assert "<script>alert" not in r.text and "&lt;script&gt;" in r.text


def test_pdf_view_and_raw_download(client, accounts):
    _login(client, "alice")
    file_id = _upload(client, PDF, "audit.pdf").json()["id"]
    r = client.get(f"/files/{file_id}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content == PDF
    assert client.get(f"/files/{file_id}/raw").headers["content-disposition"].startswith("attachment")
    assert client.get(f"/files/{file_id}/view").status_code == 404  # not markdown


def test_delete_and_retry(client, conn, settings, accounts):
    _login(client, "alice")
    file_id = _upload(client, PDF, "audit.pdf").json()["id"]
    assert client.post(f"/api/files/{file_id}/retry").status_code == 409  # not failed
    conn.execute("UPDATE files SET status = 'failed', error = 'boom' WHERE id = ?", (file_id,))
    assert client.post(f"/api/files/{file_id}/retry").json()["status"] == "queued"
    worker.run_one(conn, settings, FakeEmbedder())
    assert client.get(f"/api/files/{file_id}").json()["status"] == "ready"
    assert client.delete(f"/api/files/{file_id}").json()["status"] == "trashed"
    assert client.get(f"/api/files/{file_id}").json()["in_trash"] is True
    purged = client.post("/api/items/purge", json={"items": [{"type": "file", "id": file_id}]})
    assert purged.json()["ok"]
    assert client.get(f"/api/files/{file_id}").status_code == 404


# ------------------------------------------------------------ file manager
def test_folder_upload_browse_and_bulk_actions(client, conn, accounts):
    _login(client, "alice")
    inbox = client.get("/api/locations").json()["locations"][0]["id"]
    for path in ["Reports/2026/q1.md", "Reports/2026/q2.md", "Reports/../../escape.md"]:
        r = client.post("/api/files", files={"file": (path.rsplit("/", 1)[-1], MD, "text/markdown")},
                        data={"collection_id": str(inbox), "relative_path": path})
        assert r.status_code in (200, 201), r.text
    root = client.get(f"/api/browse?collection_id={inbox}").json()
    # Every inbox also has its Agents folder (Claude's workspace).
    assert [(f["name"], f["role"]) for f in root["folders"]] == [("Agents", "agents"), ("Reports", None)]
    assert root["can_write"]
    reports = root["folders"][1]["id"]
    tree = client.get(f"/api/collections/{inbox}/folders").json()["folders"]
    assert sorted(f["name"] for f in tree) == ["2026", "Agents", "Reports"]
    year = next(f["id"] for f in tree if f["name"] == "2026")
    listing = client.get(f"/api/browse?collection_id={inbox}&folder_id={year}").json()
    assert [p["name"] for p in listing["path"]] == ["Reports", "2026"]
    # Same content twice in one folder dedupes; ".." parts can't climb out.
    assert sorted(f["name"] for f in listing["files"]) == ["q1.md"]
    escape = client.get(f"/api/browse?collection_id={inbox}&folder_id={reports}").json()
    assert [f["name"] for f in escape["files"]] == ["escape.md"]

    q1 = listing["files"][0]["id"]
    assert client.patch(f"/api/files/{q1}", json={"name": "Q1 report.md"}).json()["name"] == "Q1 report.md"
    moved = client.post("/api/items/move", json={"items": [{"type": "file", "id": q1}],
                                                 "target": {"collection_id": inbox, "folder_id": None}}).json()
    assert moved["ok"]
    copied = client.post("/api/items/copy", json={"items": [{"type": "folder", "id": reports},
                                                            {"type": "file", "id": 999999}],
                                                  "target": {"collection_id": inbox}}).json()
    assert not copied["ok"]
    assert [r["ok"] for r in copied["results"]] == [True, False] and copied["results"][1]["status"] == 404
    names = [f["name"] for f in client.get(f"/api/browse?collection_id={inbox}").json()["folders"]]
    assert names == ["Agents", "Reports", "Reports (2)"]

    trashed = client.post("/api/items/trash", json={"items": [{"type": "folder", "id": reports}]}).json()
    assert trashed["ok"]
    bin_ = client.get(f"/api/trash?collection_id={inbox}").json()
    assert [f["name"] for f in bin_["folders"]] == ["Reports"] and bin_["keep_days"] == 30
    assert client.get("/api/locations").json()["locations"][0]["trash"] == 1
    assert client.post("/api/items/restore", json={"items": [{"type": "folder", "id": reports}]}).json()["ok"]
    hits = client.get("/api/search-names?q=report").json()
    assert {f["name"] for f in hits["files"]} == {"Q1 report.md"}


def test_bob_cannot_browse_or_change_alices_files(client, conn, accounts):
    _login(client, "alice")
    inbox = client.get("/api/locations").json()["locations"][0]["id"]
    folder = client.post("/api/folders", json={"collection_id": inbox, "name": "Private"}).json()["id"]
    file_id = _upload(client, MD, "notes.md").json()["id"]
    client.post("/logout")
    _login(client, "bob")
    assert client.get(f"/api/browse?collection_id={inbox}").status_code == 404
    assert client.get(f"/api/collections/{inbox}/folders").status_code == 404
    assert client.get(f"/api/trash?collection_id={inbox}").status_code == 404
    assert client.patch(f"/api/folders/{folder}", json={"name": "Mine"}).status_code == 404
    assert client.patch(f"/api/files/{file_id}", json={"name": "x.md"}).status_code == 404
    r = client.post("/api/items/trash", json={"items": [{"type": "file", "id": file_id}]}).json()
    assert r["results"][0]["status"] == 404
    assert client.post("/api/folders", json={"collection_id": inbox, "name": "Sneaky"}).status_code == 404
    assert client.get("/api/search-names?q=notes").json()["files"] == []
    bob_inbox = client.get("/api/locations").json()["locations"][0]["id"]
    r = client.post("/api/items/move", json={"items": [{"type": "file", "id": file_id}],
                                             "target": {"collection_id": bob_inbox}}).json()
    assert r["results"][0]["status"] == 404


def test_read_only_library_refuses_changes(client, conn, accounts):
    lib = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, lib, accounts["alice"])
    _login(client, "alice")
    assert client.post("/api/folders", json={"collection_id": lib, "name": "X"}).status_code == 403
    assert client.get(f"/api/browse?collection_id={lib}").json()["can_write"] is False
    assert client.get(f"/api/trash?collection_id={lib}").status_code == 403


def test_previews_are_safe(client, accounts):
    _login(client, "alice")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    image = _upload(client, png, "logo.png").json()
    vector = _upload(client, svg, "logo.svg").json()
    text = _upload(client, b"<script>alert(1)</script> plain words", "readme.txt").json()
    assert image["preview"] == "image" and vector["preview"] is None and text["preview"] == "text"
    r = client.get(f"/files/{image['id']}/inline")
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/files/{vector['id']}/inline").status_code == 404
    r = client.get(f"/files/{text['id']}/inline")
    assert r.headers["content-type"].startswith("text/plain") and "sandbox" in r.headers["content-security-policy"]
    raw = client.get(f"/files/{vector['id']}/raw")
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"].startswith("attachment")


def test_cross_site_requests_are_refused(client, accounts):
    _login(client, "alice")
    inbox = client.get("/api/locations").json()["locations"][0]["id"]
    evil = client.post("/api/folders", json={"collection_id": inbox, "name": "x"},
                       headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert evil.status_code == 403 and "Cross-site" in evil.json()["detail"]
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    same = client.post("/api/folders", json={"collection_id": inbox, "name": "ok"},
                       headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert same.status_code == 201
    assert client.get("/api/locations", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_ask_scope_must_be_readable(client, accounts):
    _login(client, "alice")
    alice_inbox = client.get("/api/locations").json()["locations"][0]["id"]
    client.post("/logout")
    _login(client, "bob")
    r = client.post("/api/ask", json={"question": "pool hours?", "scope": {"collection_id": alice_inbox}})
    assert r.status_code == 404
