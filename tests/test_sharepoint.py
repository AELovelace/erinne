"""SharePoint libraries: delta sync into Mnemerynne, read-only, trimmed per person by SharePoint itself."""

import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, KeywordEmbedder
from mnemerynne import editing, fs, m365, permissions, retrieval, sharepoint, users, worker
from mnemerynne.api_tokens import Caller
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app

TENANT = "22222222-2222-2222-2222-222222222222"
DRIVE = "drive-1"


class FakeSharePoint:
    """One site with one document library, its delta feed, downloads and access."""

    def __init__(self):
        self.items = {}            # id -> item (without 'deleted')
        self.content = {}          # id -> bytes
        self.changes = []          # items reported by the next delta call
        self.allowed = {}          # user token -> item ids that user may open
        self.requests = []         # (method, url) of everything asked
        self.expired = False       # the next delta-link call answers 410
        self.n = 0
        self.put("root", {"name": "root", "root": {}, "folder": {}})

    def put(self, item_id, item, data=None):
        item = {"id": item_id, "parentReference": {"id": "root"}, **item}
        if data is not None:
            self.n += 1
            item.update({"file": {}, "size": len(data), "cTag": f"c{self.n}", "webUrl": f"https://sp.test/{item_id}"})
            self.content[item_id] = data
        self.items[item_id] = item
        self.changes.append(item)

    def delete(self, item_id):
        self.items.pop(item_id)
        self.changes.append({"id": item_id, "deleted": {}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        if "/sites/contoso.sharepoint.com:/sites/Front" in url:
            return httpx.Response(200, json={"id": "site-1", "displayName": "Front Office",
                                             "webUrl": "https://contoso.sharepoint.com/sites/Front"})
        if url.startswith("https://graph.microsoft.com/v1.0/sites/site-1/drives"):
            return httpx.Response(200, json={"value": [
                {"id": DRIVE, "name": "Documents", "driveType": "documentLibrary", "webUrl": "https://sp.test/docs"},
                {"id": "lists", "name": "Lists", "driveType": "business"}]})
        if "/root/delta" in url:
            if "token=" in url and self.expired:
                self.expired = False
                return httpx.Response(410, json={"error": {"message": "resync required"}})
            if "token=" not in url:              # a full sync: everything there is
                self.changes = list(self.items.values())
            batch, self.changes = self.changes, []
            half = len(batch) // 2
            if "page=2" in url:
                return httpx.Response(200, json={"value": batch, "@odata.deltaLink":
                                                 f"https://graph.microsoft.com/v1.0/drives/{DRIVE}/root/delta?token=t"})
            self.changes = batch[half:]         # first page: half now, the rest on the next page
            return httpx.Response(200, json={"value": batch[:half], "@odata.nextLink":
                                             f"https://graph.microsoft.com/v1.0/drives/{DRIVE}/root/delta?page=2"})
        if url.endswith("/content"):
            item_id = url.split("/items/")[1].split("/")[0]
            return httpx.Response(302, headers={"location": f"https://download.test/{item_id}"})
        if url.startswith("https://download.test/"):
            assert "authorization" not in request.headers          # pre-signed: no token sent
            return httpx.Response(200, content=self.content[url.rsplit("/", 1)[1]])
        if url.endswith("/$batch"):
            mine = self.allowed.get(request.headers["authorization"].split()[-1], set())
            responses = [{"id": q["id"], "status": 200 if q["url"].split("/items/")[1].split("?")[0] in mine else 403}
                         for q in json.loads(request.content)["requests"]]
            return httpx.Response(200, json={"responses": responses})
        return httpx.Response(404, json={"error": {"message": "not faked: " + url}})


@pytest.fixture
def sp(monkeypatch):
    fake = FakeSharePoint()
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(fake.handler))
    m365._app_tokens.clear()
    m365._user_tokens.clear()
    return fake


@pytest.fixture
def world(settings, conn, sp):
    settings.m365_client_id, settings.m365_client_secret = "app-id", "app-secret"
    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    sharepoint.configure(settings)
    ids = {n: users.create_user(conn, n, "password123") for n in ("alice", "bob", "carol")}
    tenant = conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Contoso', 'cloud')",
                          (TENANT,)).lastrowid
    for name in ("alice", "bob"):          # carol has no Microsoft account linked
        identity = conn.execute("INSERT INTO m365_identities(user_id, tenant_row, oid, upn) VALUES (?, ?, ?, ?)",
                                (ids[name], tenant, f"oid-{name}", f"{name}@contoso.com")).lastrowid
        m365._user_tokens[identity] = (f"user-{name}", time.time() + 3600)
    sp.put("folder-1", {"name": "Policies", "folder": {}})
    sp.put("a", {"name": "Pool.md"}, b"# Pool\n\nThe pool opens at 8am.\n")
    sp.put("b", {"name": "Audit.md", "parentReference": {"id": "folder-1"}}, b"# Audit\n\nRun the night audit at 1am.\n")
    sp.changes = []
    (cid,) = sharepoint.add_libraries(conn, settings, tenant, "https://contoso.sharepoint.com/sites/Front", [DRIVE])
    drive_row = conn.execute("SELECT id FROM sharepoint_drives").fetchone()[0]
    embedder = KeywordEmbedder()
    assert sharepoint.sync_drive(conn, settings, drive_row) == {"added": 2, "changed": 0, "removed": 0, "skipped": 0}
    while worker.run_one(conn, settings, embedder):
        pass
    sp.allowed = {"user-alice": {"a", "b"}, "user-bob": {"a"}}
    return {**ids, "cid": cid, "drive": drive_row, "embedder": embedder, "tenant": tenant}


def _file(conn, name):
    return conn.execute("SELECT * FROM files WHERE display_name = ?", (name,)).fetchone()


def test_sync_mirrors_the_library_read_only(conn, settings, world, sp):
    lib = conn.execute("SELECT * FROM collections WHERE id = ?", (world["cid"],)).fetchone()
    assert (lib["name"], lib["source"]) == ("Front Office · Documents", "sharepoint")
    audit = _file(conn, "Audit.md")
    folder = conn.execute("SELECT name FROM folders WHERE id = ?", (audit["folder_id"],)).fetchone()["name"]
    assert folder == "Policies" and audit["status"] == "ready" and audit["web_url"] == "https://sp.test/b"
    # Only GETs go to SharePoint for the library itself (the $batch access checks are POSTs to /$batch).
    assert all(m == "GET" for m, u in sp.requests if "/drives/" in u or "download.test" in u)
    # Open to people with a linked account in the tenant; nobody can change it.
    assert world["cid"] in permissions.readable_collection_ids(conn, world["alice"])
    assert world["cid"] not in permissions.readable_collection_ids(conn, world["carol"])
    assert world["cid"] not in permissions.writable_collection_ids(conn, world["alice"])
    with pytest.raises(editing.EditError):
        editing.check_edit(conn, world["alice"], True, world["cid"], None, via="erynne")


def test_each_person_sees_only_what_sharepoint_allows(conn, settings, world, sp):
    for name, expect in (("alice", True), ("bob", False)):
        found = retrieval.search_for_user(conn, settings, world["embedder"], world[name], "night audit")
        assert any(h.file_name == "Audit.md" for h in found.hits) == expect, name
    assert retrieval.search_for_user(conn, settings, world["embedder"], world["bob"], "pool").hits
    batches = sum(1 for _, u in sp.requests if u.endswith("/$batch"))
    retrieval.search_for_user(conn, settings, world["embedder"], world["bob"], "night audit")
    assert sum(1 for _, u in sp.requests if u.endswith("/$batch")) == batches          # cached

    access = fs.Access.of(conn, world["bob"])
    listed = fs.browse(conn, access, world["cid"], _file(conn, "Audit.md")["folder_id"])
    assert listed["files"] == []
    assert [f["display_name"] for f in fs.search_names(conn, fs.Access.of(conn, world["alice"]), "md")["files"]] \
        == ["Audit.md", "Pool.md"]
    assert [f["display_name"] for f in fs.search_names(conn, access, "md")["files"]] == ["Pool.md"]

    # SharePoint changes its mind: after the cache expires, the new answer counts.
    sp.allowed["user-bob"].add("b")
    conn.execute("UPDATE sharepoint_access SET checked_at = datetime('now', '-16 minutes')")
    found = retrieval.search_for_user(conn, settings, world["embedder"], world["bob"], "night audit")
    assert any(h.file_name == "Audit.md" for h in found.hits)


def test_files_and_uploads_through_the_web(settings, conn, world, sp):
    with TestClient(create_app(settings, Services(llm=None, embedder=world["embedder"],
                                                  directory=FakeDirectory()))) as c:
        audit = _file(conn, "Audit.md")["id"]
        c.post("/login", data={"username": "bob", "password": "password123"})
        assert c.get(f"/files/{audit}/view").status_code == 404
        assert c.get(f"/api/files/{audit}").status_code == 404
        up = c.post("/api/files", data={"collection_id": world["cid"]},
                    files={"file": ("x.md", io.BytesIO(b"# x"), "text/markdown")})
        assert up.status_code in (403, 404)
        c.post("/logout")
        c.post("/login", data={"username": "alice", "password": "password123"})
        assert c.get(f"/files/{audit}/view").status_code == 200
        assert c.get(f"/api/files/{audit}").json()["web_url"] == "https://sp.test/b"


def test_changes_renames_and_deletions_follow(conn, settings, world, sp):
    pool, audit = _file(conn, "Pool.md")["id"], _file(conn, "Audit.md")["id"]
    renamed = {**sp.items["a"], "name": "Pool hours.md"}                  # renamed only: same cTag
    sp.items["a"] = renamed
    sp.changes.append(renamed)
    sp.put("b", {"name": "Audit.md", "parentReference": {"id": "folder-1"}}, b"# Audit\n\nRun the audit at 2am.\n")
    stats = sharepoint.sync_drive(conn, settings, world["drive"])
    assert stats["changed"] == 2 and stats["added"] == 0
    assert _file(conn, "Pool hours.md")["id"] == pool                      # same file, so citations keep working
    assert _file(conn, "Audit.md")["id"] == audit and _file(conn, "Audit.md")["status"] == "queued"
    assert b"2am" in store.content_path(conn, settings, audit).read_bytes()

    sp.delete("b")
    sp.delete("folder-1")
    assert sharepoint.sync_drive(conn, settings, world["drive"])["removed"] >= 1
    assert _file(conn, "Audit.md") is None

    sp.put("c", {"name": "New.md"}, b"# New\n")
    sp.changes = []                                    # lost in an expired delta link: a full sync finds it
    sp.expired = True
    stats = sharepoint.sync_drive(conn, settings, world["drive"])
    assert stats["added"] == 1 and _file(conn, "New.md") is not None


def test_claude_sees_it_only_when_switched_on(conn, settings, world, sp):
    tools = Tools(settings, lambda: world["embedder"])
    alice = Caller(world["alice"], "alice", 1)
    assert not tools.search_documents(conn, alice, "night audit")[0]["found"]
    conn.execute("UPDATE collections SET claude_enabled = 1 WHERE id = ?", (world["cid"],))
    assert tools.search_documents(conn, alice, "night audit")[0]["results"][0]["file"] == "Audit.md"
    bob = Caller(world["bob"], "bob", 2)
    assert not any(r["file"] == "Audit.md" for r in tools.search_documents(conn, bob, "night audit")[0]["results"])


def test_library_counts_never_ask_sharepoint(conn, settings, world, sp, monkeypatch):
    """list_libraries on a big SharePoint library used to take minutes: a Graph call per 20 files. The count
    is what is synced; the per-file check stays on browsing, searching and opening."""
    conn.execute("UPDATE collections SET claude_enabled = 1 WHERE id = ?", (world["cid"],))
    tools = Tools(settings, lambda: world["embedder"])
    bob = Caller(world["bob"], "bob", 2)                       # may open only Pool.md in SharePoint

    def boom(*a, **k):
        raise AssertionError("list_libraries asked SharePoint")
    monkeypatch.setattr(sharepoint, "visible", boom)
    lib = next(l for l in tools.list_libraries(conn, bob)[0]["libraries"] if l["id"] == world["cid"])
    assert (lib["files"], lib["searchable_files"], lib["source"]) == (2, 2, "sharepoint") and "SharePoint" in lib["note"]


def test_admin_adds_syncs_and_removes_libraries(settings, conn, world, sp):
    with TestClient(create_app(settings, Services(llm=None, embedder=world["embedder"],
                                                  directory=FakeDirectory({"boss": {"domain admins"}})))) as c:
        boss = users.create_user(conn, "boss", "password123", role="admin")
        assert boss
        c.post("/login", data={"username": "boss", "password": "password123"})
        found = c.post("/api/admin/sharepoint/resolve", json={
            "tenant": world["tenant"], "url": "https://contoso.sharepoint.com/sites/Front/Shared%20Documents"}).json()
        assert found["name"] == "Front Office" and found["libraries"] == [
            {"id": DRIVE, "name": "Documents", "web_url": "https://sp.test/docs", "added": True}]
        assert c.post("/api/admin/sharepoint/resolve", json={"tenant": world["tenant"],
                                                            "url": "https://evil.example/x"}).status_code == 400
        listed = c.get("/api/admin/sharepoint").json()["libraries"]
        assert listed[0]["files"] == 2 and listed[0]["status"] == "ok"
        assert c.delete(f"/api/admin/sharepoint/{world['drive']}").json() == {"status": "removed"}
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM collections WHERE source = 'sharepoint'").fetchone()[0] == 0


def test_a_sync_already_running_isnt_started_twice(conn, settings, world):
    conn.execute("UPDATE sharepoint_drives SET status = 'syncing', sync_started_at = datetime('now')")
    assert sharepoint.sync_drive(conn, settings, world["drive"]).get("busy")
    conn.execute("UPDATE sharepoint_drives SET sync_started_at = datetime('now', '-2 hours')")
    assert not sharepoint.sync_drive(conn, settings, world["drive"]).get("busy")
