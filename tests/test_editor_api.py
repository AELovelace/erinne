"""Editing text and Markdown files in the browser: load with a version, save through the same path as
Claude's edits (versions kept, conflicts refused), new pages, the server-side preview, and the Edit
button's edit_url."""

import io

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import editing, permissions, users
from mnemerynne.ingest import store
from mnemerynne.web import editor as editor_pages, security
from mnemerynne.web.app import Services, create_app


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def _add(conn, settings, collection, name, data, folder=None, user=None):
    received = store.receive(io.BytesIO(data), name, settings)
    return store.add_file(conn, settings, received, collection, user, folder_id=folder)[0]


@pytest.fixture
def world(conn, settings):
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    brand = permissions.create_library(conn, "Brand")
    permissions.grant_user(conn, brand, alice, can_write=True)
    permissions.grant_user(conn, brand, bob)
    return {"alice": alice, "bob": bob, "brand": brand, "inbox": permissions.private_collection_id(conn, alice)}


def test_load_save_versions_and_conflicts(settings, conn, world):
    old, new = b"# Guide\n\nold\n", b"# Guide\n\nnew\n"
    fid = _add(conn, settings, world["brand"], "guide.md", old, user=world["alice"])
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        got = c.get(f"/api/files/{fid}/text").json()
        assert got["content"] == old.decode() and got["version"] == editing.sha256(old)
        assert got["can_write"] and got["edit_url"] == f"/edit/{fid}"

        saved = c.put(f"/api/files/{fid}/text", json={"content": new.decode(), "version": got["version"]})
        assert saved.status_code == 200 and saved.json()["version"] == editing.sha256(new)
        assert saved.json()["status"] == "queued"                       # read again for search
        assert store.content_path(conn, settings, fid).read_bytes() == new
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE file_id = ? AND status = 'queued'", (fid,)).fetchone()[0]
        assert conn.execute("SELECT edited_via FROM files WHERE id = ?", (fid,)).fetchone()[0] is None   # a person, not an AI

        versions = c.get(f"/api/files/{fid}/versions").json()["versions"]
        assert len(versions) == 1 and versions[0]["replaced_by"] == "alice" and versions[0]["change"] == "edited in the browser"
        assert conn.execute("SELECT via FROM file_versions").fetchone()[0] == "web"

        stale = c.put(f"/api/files/{fid}/text", json={"content": "x\n", "version": got["version"]})
        assert stale.status_code == 409 and "changed since" in stale.json()["detail"]
        same = c.put(f"/api/files/{fid}/text", json={"content": new.decode(), "version": editing.sha256(new)})
        assert same.status_code == 200 and len(c.get(f"/api/files/{fid}/versions").json()["versions"]) == 1


def test_only_text_files_and_only_where_you_may_write(settings, conn, world):
    md = _add(conn, settings, world["brand"], "notes.md", b"# Notes\n", user=world["alice"])
    zipped = _add(conn, settings, world["brand"], "photos.zip", b"PK\x03\x04 zip", user=world["alice"])
    doc = _add(conn, settings, world["brand"], "letter.docx", b"PK\x03\x04 not really", user=world["alice"])
    with TestClient(_app(settings)) as c:
        _login(c, "bob")                                               # reads Brand, can't write
        got = c.get(f"/api/files/{md}/text").json()
        assert got["content"] == "# Notes\n" and got["can_write"] is False and got["edit_url"] is None
        assert c.put(f"/api/files/{md}/text", json={"content": "no", "version": got["version"]}).status_code == 403
        assert c.get(f"/edit/{md}").status_code == 403
        assert c.get(f"/api/files/{zipped}/text").status_code == 404
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        assert c.get(f"/api/files/{zipped}/text").status_code == 404
        assert c.get(f"/api/files/{doc}/text").status_code == 404     # Word: Claude's tools, not this editor
        assert c.get(f"/api/files/{doc}").json()["edit_url"] is None
        assert c.get(f"/api/files/{md}").json()["edit_url"] == f"/edit/{md}"
        settings.edit_max_bytes = 4
        assert c.get(f"/api/files/{md}/text").status_code == 413
        settings.edit_max_bytes = 20 * 1024 * 1024
        assert c.get("/api/files/999999/text").status_code == 404


def test_new_pages(settings, conn, world):
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        made = c.post("/api/files/text", json={"collection_id": world["brand"], "name": "Runbook", "content": "# Runbook\n\nSteps.\n"})
        assert made.status_code == 201
        body = made.json()
        assert body["name"] == "Runbook.md" and body["edit_url"] == f"/edit/{body['id']}"
        assert body["version"] == editing.sha256(b"# Runbook\n\nSteps.\n")
        assert store.content_path(conn, settings, body["id"]).read_bytes() == b"# Runbook\n\nSteps.\n"
        again = c.post("/api/files/text", json={"collection_id": world["brand"], "name": "Runbook", "content": "# Runbook\n\nSteps.\n"})
        assert again.status_code == 200 and again.json()["id"] == body["id"]          # same content: the same file

        empty = c.post("/api/files/text", json={"collection_id": world["brand"], "name": "Blank page.txt"}).json()
        assert store.content_path(conn, settings, empty["id"]).read_bytes() == b"# Blank page\n"
        mine = c.post("/api/files/text", json={"name": "Private"})                   # no place: the File box
        assert mine.status_code == 201 and mine.json()["collection_id"] == world["inbox"]
        assert c.post("/api/files/text", json={"collection_id": world["brand"], "folder_id": 999, "name": "x"}).status_code == 404
        assert c.get(f"/edit/new?collection_id={world['brand']}").status_code == 200
    with TestClient(_app(settings)) as c:
        _login(c, "bob")
        assert c.post("/api/files/text", json={"collection_id": world["brand"], "name": "Nope"}).status_code == 403
        assert c.get(f"/edit/new?collection_id={world['brand']}").status_code == 403


def test_preview_is_the_safe_renderer_and_rate_limited(settings, conn, world, monkeypatch):
    monkeypatch.setattr(editor_pages, "_preview_limiter", security.RateLimiter())
    monkeypatch.setattr(editor_pages, "PREVIEW_PER_MINUTE", 3)
    with TestClient(_app(settings)) as c:
        assert c.post("/api/markdown/preview", json={"text": "# Hi"}).status_code == 401
        _login(c, "alice")
        html = c.post("/api/markdown/preview", json={"text": "# Hi\n\n<script>alert(1)</script>\n"}).json()["html"]
        assert '<h1 id="hi">' in html and "&lt;script&gt;" in html and "<script>" not in html
        assert c.post("/api/markdown/preview", json={"text": ""}).status_code == 200
        assert c.post("/api/markdown/preview", json={"text": ""}).status_code == 200
        assert c.post("/api/markdown/preview", json={"text": ""}).status_code == 429


def test_editor_page_and_vendored_codemirror(settings, conn, world):
    md = _add(conn, settings, world["brand"], "notes.md", b"# Notes\n", user=world["alice"])
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        page = c.get(f"/edit/{md}?back=/wiki/{md}").text
        assert 'data-mode="edit"' in page and f'data-file-id="{md}"' in page and 'data-back="/wiki/' in page
        assert "vendor/codemirror/codemirror.min.js" in page and "mdeditor.js" in page and "notes.md" in page
        assert 'data-back="/files"' in c.get(f"/edit/{md}?back=https://evil.example/").text
        fresh = c.get(f"/edit/new?collection_id={world['brand']}&name=Plan").text
        assert 'data-mode="new"' in fresh and 'value="Plan"' in fresh
        assert c.get("/static/vendor/codemirror/codemirror.min.js").status_code == 200
        assert c.get("/static/vendor/codemirror/codemirror.min.css").status_code == 200
        assert c.get("/static/mdeditor.js").status_code == 200
        files_page = c.get("/files").text
        assert 'data-act="edit"' in files_page
