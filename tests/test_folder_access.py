"""Folder-level access: inheritance, restricted folders, folder grants."""

import io

import pytest

from mnemerynne import api_tokens, fs, permissions, retrieval, users, worker
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from fakes import KeywordEmbedder
from fastapi.testclient import TestClient


@pytest.fixture
def embedder():
    return KeywordEmbedder()


@pytest.fixture
def world(conn, settings, embedder):
    """Front Office library:

        welcome.md                      (top level)
        Night Audit/audit.md
        Night Audit/Checklists/list.md
        Managers/secret.md              inheritance off; dave can edit
        Managers/Payroll/pay.md

    staff (alice, bob) read the library, bob can also edit it; carol can only
    read Night Audit; dave can only reach Managers.
    """
    names = ["alice", "bob", "carol", "dave"]
    ids = {n: users.create_user(conn, n, "password123") for n in names}
    lib = permissions.create_library(conn, "Front Office")
    staff = permissions.create_group(conn, "Staff", [ids["alice"], ids["bob"]])
    permissions.grant_group(conn, lib, staff)
    permissions.grant_user(conn, lib, ids["bob"], can_write=True)
    night = fs.ensure_path(conn, lib, None, ["Night Audit"], None)
    checklists = fs.ensure_path(conn, lib, night, ["Checklists"], None)
    managers = fs.ensure_path(conn, lib, None, ["Managers"], None)
    payroll = fs.ensure_path(conn, lib, managers, ["Payroll"], None)
    permissions.set_folder_inherit(conn, managers, False)
    permissions.grant_folder(conn, managers, "user", ids["dave"], can_write=True)
    permissions.grant_folder(conn, night, "user", ids["carol"])

    def add(name, text, folder):
        received = store.receive(io.BytesIO(f"# {name}\n\n{text} policy\n".encode()), name, settings)
        return store.add_file(conn, settings, received, lib, None, folder_id=folder)[0]

    files = {"welcome": add("welcome.md", "welcome", None), "audit": add("audit.md", "audit", night),
             "list": add("list.md", "checklist", checklists), "secret": add("secret.md", "secret", managers),
             "pay": add("pay.md", "payroll", payroll)}
    while worker.run_one(conn, settings, embedder):
        pass
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    return {**ids, "lib": lib, "staff": staff, "night": night, "checklists": checklists, "managers": managers,
            "payroll": payroll, "files": files}


def _found(conn, settings, embedder, user_id, scope=None):
    result = retrieval.search_for_user(conn, settings, embedder, user_id, "policy", scope=scope)
    return {h.file_name for h in result.candidates}


# ------------------------------------------------------------------ engine
def test_reach_follows_inheritance_and_restrictions(conn, world):
    lib = world["lib"]
    alice = permissions.reach(conn, world["alice"])[lib]
    assert alice.root_read and alice.read == {world["night"], world["checklists"]}
    assert not alice.write and not alice.root_write
    bob = permissions.reach(conn, world["bob"])[lib]
    assert bob.write == {world["night"], world["checklists"]} and bob.root_write
    carol = permissions.reach(conn, world["carol"])[lib]
    assert not carol.root_read and carol.read == {world["night"], world["checklists"]}
    dave = permissions.reach(conn, world["dave"])[lib]
    assert not dave.root_read and dave.read == dave.write == {world["managers"], world["payroll"]}
    assert dave.visible() == {world["managers"], world["payroll"]}
    # Everyone above reaches the library, so it shows up for them.
    for name in ("alice", "bob", "carol", "dave"):
        assert lib in permissions.readable_collection_ids(conn, world[name])


def test_inbox_folders_cannot_be_shared(conn, world):
    inbox = permissions.private_collection_id(conn, world["alice"])
    mine = fs.ensure_path(conn, inbox, None, ["Private"], None)
    with pytest.raises(ValueError):
        permissions.grant_folder(conn, mine, "user", world["bob"])
    with pytest.raises(ValueError):
        permissions.set_folder_inherit(conn, mine, False)


def test_turning_inheritance_off_can_keep_or_drop_access(conn, world):
    night, alice = world["night"], world["alice"]
    permissions.set_folder_inherit(conn, night, False, copy_inherited=True)
    assert night in permissions.reach(conn, alice)[world["lib"]].read     # Staff's grant was copied
    assert {(g["group_id"], g["can_write"]) for g in permissions.folder_grants(conn, night)} >= {(world["staff"], 0)}
    permissions.revoke_folder(conn, night, "group", world["staff"])
    assert night not in permissions.reach(conn, alice)[world["lib"]].read
    permissions.set_folder_inherit(conn, night, True)
    assert night in permissions.reach(conn, alice)[world["lib"]].read
    # Without copying, only the folder's own grants (carol, bob's copy) remain.
    permissions.set_folder_inherit(conn, night, False)
    assert night not in permissions.reach(conn, alice)[world["lib"]].read
    assert night in permissions.reach(conn, world["carol"])[world["lib"]].read


def test_inherited_grants_name_their_source(conn, world):
    inherited = permissions.inherited_grants(conn, world["checklists"])
    sources = {(g["username"] or g["group_name"], g["source"]["kind"], g["source"]["name"]) for g in inherited}
    assert ("carol", "folder", "Night Audit") in sources
    assert ("Staff", "library", "Front Office") in sources
    assert permissions.inherited_grants(conn, world["managers"]) == []   # inheritance off


# ------------------------------------------------------------------ search
def test_search_covers_exactly_what_each_person_can_read(conn, settings, embedder, world):
    found = lambda name, scope=None: _found(conn, settings, embedder, world[name], scope)  # noqa: E731
    assert found("alice") == {"welcome.md", "audit.md", "list.md"}
    assert found("carol") == {"audit.md", "list.md"}
    assert found("dave") == {"secret.md", "pay.md"}
    # "Search in" a folder can only narrow.
    assert found("alice", {"collection_id": world["lib"], "folders": [world["managers"], world["payroll"]]}) == set()
    assert found("carol", {"collection_id": world["lib"], "folders": [world["checklists"]]}) == {"list.md"}
    # A binned file in a readable folder stays out.
    fs.trash(conn, fs.Access.of(conn, world["bob"]), [fs.Item("file", world["files"]["audit"])])
    assert found("carol") == {"list.md"}


def test_vector_branches_merge_by_distance(conn, embedder, world):
    lib = world["lib"]
    reaches = permissions.reach(conn, world["alice"])
    scope = retrieval.Scope.of_reach([reaches[lib]])
    # Top level and two folders: two branches, each needing only IN (sqlite-vec
    # applies OR after picking the k nearest, which would drop allowed results).
    assert scope.branches() == [({lib}, {world["night"], world["checklists"]}), ({lib}, {0})]
    hits = retrieval.vector_search(conn, embedder.embed_query("policy"), scope, k=10)
    files = {r[0] for r in conn.execute(
        f"SELECT DISTINCT file_id FROM chunks WHERE id IN ({','.join(str(c) for c, _ in hits)})")}
    assert files == {world["files"]["welcome"], world["files"]["audit"], world["files"]["list"]}
    assert [d for _, d in hits] == sorted(d for _, d in hits)
    assert retrieval.Scope().branches() == [] and not retrieval.Scope()


# -------------------------------------------------------------- file manager
def test_browsing_shows_only_what_each_person_may_see(conn, world):
    lib = world["lib"]
    carol = fs.Access.of(conn, world["carol"])
    top = fs.browse(conn, carol, lib, None)
    assert [f["name"] for f in top["folders"]] == ["Night Audit"] and top["files"] == []
    assert [f["display_name"] for f in fs.browse(conn, carol, lib, world["night"])["files"]] == ["audit.md"]
    alice = fs.Access.of(conn, world["alice"])
    top = fs.browse(conn, alice, lib, None)
    assert [f["name"] for f in top["folders"]] == ["Night Audit"]
    assert [f["display_name"] for f in top["files"]] == ["welcome.md"]
    with pytest.raises(fs.NotFound):
        fs.browse(conn, alice, lib, world["managers"])
    dave = fs.Access.of(conn, world["dave"])
    assert [f["name"] for f in fs.browse(conn, dave, lib, None)["folders"]] == ["Managers"]
    assert {f["name"] for f in fs.folder_tree(conn, dave, lib)} == {"Managers", "Payroll"}
    assert [f["display_name"] for f in fs.search_names(conn, alice, "secret")["files"]] == []


def test_editing_is_per_folder(conn, world):
    lib = world["lib"]
    dave, bob, carol = (fs.Access.of(conn, world[n]) for n in ("dave", "bob", "carol"))
    fs.create_folder(conn, dave, lib, world["managers"], "Reviews")
    with pytest.raises(fs.Forbidden):
        fs.create_folder(conn, dave, lib, None, "Top")          # dave can't edit the top level
    with pytest.raises(fs.NotFound):
        fs.create_folder(conn, bob, lib, world["managers"], "Peek")
    with pytest.raises(fs.Forbidden):
        fs.create_folder(conn, carol, lib, world["night"], "Mine")   # read only
    # Moving needs edit access on both ends.
    with pytest.raises(fs.NotFound):
        fs.move(conn, dave, [fs.Item("file", world["files"]["secret"])], lib, world["night"])
    # A folder with something inside the person can't change can't be binned or moved.
    hidden = fs.ensure_path(conn, lib, world["night"], ["Private"], None)
    permissions.set_folder_inherit(conn, hidden, False)
    result = fs.trash(conn, fs.Access.of(conn, world["bob"]), [fs.Item("folder", world["night"])])[0]
    assert not result.ok and result.status == 403


def test_upload_and_views_respect_folders(client, conn, world):
    client.post("/login", data={"username": "dave", "password": "password123"})
    sent = client.post("/api/files", files={"file": ("r.md", b"# R\n\nreview\n", "text/markdown")},
                       data={"collection_id": str(world["lib"]), "folder_id": str(world["managers"])})
    assert sent.status_code == 201, sent.text
    refused = client.post("/api/files", files={"file": ("t.md", b"# T\n\ntop\n", "text/markdown")},
                          data={"collection_id": str(world["lib"])})
    assert refused.status_code == 403
    assert client.get(f"/files/{world['files']['welcome']}/view").status_code == 404
    assert client.get(f"/files/{world['files']['secret']}/view").status_code == 200
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "password123"})
    assert client.get(f"/files/{world['files']['secret']}/view").status_code == 404
    listed = {f["name"] for f in client.get("/api/files").json()["files"]}
    assert listed >= {"welcome.md", "audit.md", "list.md"} and not listed & {"secret.md", "pay.md", "r.md"}


# ------------------------------------------------------------------ Claude
def test_claude_follows_folder_grants(conn, settings, embedder, world):
    tools = Tools(settings, lambda: embedder)
    carol = api_tokens.verify(conn, api_tokens.create(conn, world["carol"], "t")[1])
    out, _ = tools.search_documents(conn, carol, "policy")
    assert {r["file"] for r in out["results"]} == {"audit.md", "list.md"}
    permissions.grant_folder(conn, world["night"], "user", world["carol"], can_claude=False)
    out, _ = tools.search_documents(conn, carol, "policy")
    assert out["found"] is False
    dave = api_tokens.verify(conn, api_tokens.create(conn, world["dave"], "t")[1])
    with pytest.raises(Exception):
        tools.read_passage(conn, dave, conn.execute("SELECT id FROM chunks WHERE file_id = ?",
                                                    (world["files"]["welcome"],)).fetchone()[0])
    listing = tools.browse_folder(conn, dave, "Front Office")[0]
    assert [f["name"] for f in listing["folders"]] == ["Managers"] and listing["files"] == []


# --------------------------------------------------------------- admin API
@pytest.fixture
def admin_client(settings, conn, world, embedder):
    users.create_user(conn, "boss", "password123", role="admin")
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as client:
        client.post("/login", data={"username": "boss", "password": "password123"})
        yield client


def test_admin_folder_api(admin_client, conn, world):
    folders = admin_client.get(f"/api/admin/libraries/{world['lib']}/folders").json()["folders"]
    by_name = {f["name"]: f for f in folders}
    assert by_name["Managers"]["inherit"] is False and by_name["Managers"]["grants"] == 1

    detail = admin_client.get(f"/api/admin/folders/{world['checklists']}").json()
    assert [p["name"] for p in detail["path"]] == ["Night Audit", "Checklists"]
    assert {(g["name"], g["source"]["name"]) for g in detail["inherited"]} >= {("carol", "Night Audit"),
                                                                               ("Staff", "Front Office")}
    put = admin_client.put(f"/api/admin/folders/{world['checklists']}/grants",
                           json={"type": "user", "id": world["dave"], "access": "write"})
    assert put.status_code == 200 and put.json()["grants"][0]["name"] == "dave"
    assert world["checklists"] in permissions.reach(conn, world["dave"])[world["lib"]].write

    off = admin_client.put(f"/api/admin/folders/{world['checklists']}/inherit", json={"inherit": False, "keep_access": False})
    assert off.json()["inherit"] is False and off.json()["inherited"] == []
    assert world["checklists"] not in permissions.reach(conn, world["alice"])[world["lib"]].read

    inbox_folder = fs.ensure_path(conn, permissions.private_collection_id(conn, world["alice"]), None, ["Mine"], None)
    assert admin_client.get(f"/api/admin/folders/{inbox_folder}").status_code == 404
    log = admin_client.get("/api/admin/log").json()["entries"]
    assert log[0]["action"] == "folder.inherit.off" and log[0]["target"] == "Front Office/Night Audit/Checklists"

    overview = {u["name"]: u for u in admin_client.get("/api/admin/access").json()["users"]}
    carol_lib = next(c for c in overview["carol"]["libraries"] if c["name"] == "Front Office")
    assert carol_lib["read"] == "Night Audit" and carol_lib["edit"] == "nothing"
    alice_lib = next(c for c in overview["alice"]["libraries"] if c["name"] == "Front Office")
    assert alice_lib["read"] == "top level, Night Audit"


def test_admin_folder_api_is_admin_only(client, conn, world):
    client.post("/login", data={"username": "alice", "password": "password123"})
    assert client.get(f"/api/admin/folders/{world['night']}").status_code == 403
    assert client.put(f"/api/admin/folders/{world['night']}/inherit", json={"inherit": False}).status_code == 403
