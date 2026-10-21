import pytest

from erinne import permissions, users, worker
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


@pytest.mark.parametrize("data, name, code", [
    (b"MZ fake exe", "invoice.pdf", 400),
    (b"plain", "notes.txt", 400),
    ("caf\xe9".encode("latin-1"), "notes.md", 400),
])
def test_rejected_uploads(client, accounts, data, name, code):
    _login(client, "alice")
    r = _upload(client, data, name)
    assert r.status_code == code and r.json()["detail"]


def test_too_large(client, accounts, settings):
    settings.max_md_bytes = 16
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
    assert client.delete(f"/api/files/{file_id}").json()["status"] == "deleted"
    assert client.get(f"/api/files/{file_id}").status_code == 404


def test_inbox_page_shows_upload_only_where_writable(client, conn, accounts):
    lib = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, lib, accounts["alice"])
    _login(client, "alice")
    page = client.get("/files").text
    assert page.count("Upload files") == 1  # inbox yes, read-only library no
    assert "Oracle" in page
