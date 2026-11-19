"""Files' Share button (mnemerynne/sharing.py, web/shares.py): shares with people, groups and everyone, and
"anyone with the link" links that give nothing away."""

from __future__ import annotations

import re

import pytest

from mnemerynne import permissions, sharing, users

MD = b"# Rota\n\nNight shift: Bob.\n"


@pytest.fixture
def people(conn):
    return {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "carol")}


def _as(client, name):
    client.post("/logout")
    client.post("/login", data={"username": name, "password": "password123"})


def _upload(client, name="rota.md", data=MD, collection_id=None, folder_id=None):
    form = {k: str(v) for k, v in (("collection_id", collection_id), ("folder_id", folder_id)) if v is not None}
    r = client.post("/api/files", files={"file": (name, data, "application/octet-stream")}, data=form)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _folder(client, collection_id, name, parent_id=None):
    r = client.post("/api/folders", json={"collection_id": collection_id, "parent_id": parent_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _share(client, type_, id_, kind, **extra):
    return client.post("/api/shares", json={"type": type_, "id": id_, "kind": kind, **extra})


def _library(conn, people):
    """Alice may change the Ops library; nobody else has it."""
    lib = permissions.create_library(conn, "Ops")
    permissions.grant_user(conn, lib, people["alice"], can_write=True)
    return lib


# ------------------------------------------------------------------ people and groups
def test_a_file_shared_with_one_person_opens_for_them_only(client, people):
    _as(client, "alice")
    file_id = _upload(client)
    assert _share(client, "file", file_id, "user", target=people["bob"]).status_code == 201

    _as(client, "bob")
    shared = client.get("/api/shares/with-me").json()["items"]
    assert [(i["type"], i["id"], i["shared_by"]) for i in shared] == [("file", file_id, "alice")]
    assert shared[0]["download_url"] == f"/files/{file_id}/raw" and shared[0]["edit_url"] is None   # view only
    assert client.get(f"/files/{file_id}/raw").status_code == 200
    props = client.get("/api/properties", params={"type": "file", "id": file_id}).json()
    assert props["where"] == "Shared with me"                     # not where it lives in Alice's File box
    assert client.put(f"/api/files/{file_id}/text", json={"content": "x"}).status_code == 403   # view only

    _as(client, "carol")
    assert client.get(f"/files/{file_id}/raw").status_code == 404
    assert client.get("/api/shares/with-me").json()["items"] == []


def test_can_edit_lets_them_save_and_removing_the_share_takes_it_back(client, people):
    _as(client, "alice")
    file_id = _upload(client)
    share_id = _share(client, "file", file_id, "user", target=people["bob"], can_write=True).json()["shares"][0]["id"]

    _as(client, "bob")
    loaded = client.get(f"/api/files/{file_id}/text").json()
    assert loaded["can_write"]
    saved = client.put(f"/api/files/{file_id}/text", json={"content": "# Rota\n\nNight shift: Carol.\n",
                                                           "version": loaded["version"]})
    assert saved.status_code == 200, saved.text
    assert client.delete(f"/api/shares/{share_id}").status_code == 404   # not his to take back

    _as(client, "alice")
    assert client.delete(f"/api/shares/{share_id}").status_code == 200
    _as(client, "bob")
    assert client.get(f"/files/{file_id}/raw").status_code == 404


def test_groups_and_everyone(client, conn, people):
    night = permissions.create_group(conn, "Night audit", [people["bob"]])
    _as(client, "alice")
    a, b = _upload(client, "a.md"), _upload(client, "b.md", b"# B\n")
    assert _share(client, "file", a, "group", target=night).status_code == 201
    assert _share(client, "file", b, "everyone").status_code == 201
    _as(client, "bob")
    assert {i["id"] for i in client.get("/api/shares/with-me").json()["items"]} == {a, b}
    _as(client, "carol")
    assert {i["id"] for i in client.get("/api/shares/with-me").json()["items"]} == {b}


def test_sharing_again_changes_the_share_instead_of_adding_one(client, people):
    _as(client, "alice")
    file_id = _upload(client)
    _share(client, "file", file_id, "user", target=people["bob"])
    shares = _share(client, "file", file_id, "user", target=people["bob"], can_write=True).json()["shares"]
    assert len(shares) == 1 and shares[0]["can_write"]


def test_you_can_only_give_what_you_have(client, conn, people):
    lib = permissions.create_library(conn, "Policies")
    permissions.grant_user(conn, lib, people["alice"])                       # read only
    permissions.grant_user(conn, lib, people["carol"], can_write=True)
    _as(client, "carol")
    file_id = _upload(client, collection_id=lib)
    _as(client, "alice")
    assert _share(client, "file", file_id, "user", target=people["bob"], can_write=True).status_code == 403
    assert _share(client, "file", file_id, "user", target=people["bob"]).status_code == 201
    _as(client, "bob")
    other = _upload(client, "mine.md")
    _as(client, "alice")
    assert _share(client, "file", other, "user", target=people["carol"]).status_code == 404   # not hers at all
    assert _share(client, "file", file_id, "user", target=people["alice"]).status_code == 400  # herself


def test_a_share_stands_still_while_its_maker_has_lost_access(client, conn, people):
    lib = _library(conn, people)
    _as(client, "alice")
    file_id = _upload(client, collection_id=lib)
    _share(client, "file", file_id, "user", target=people["bob"])
    permissions.revoke_user(conn, lib, people["alice"])
    _as(client, "bob")
    assert client.get(f"/files/{file_id}/raw").status_code == 404
    permissions.grant_user(conn, lib, people["alice"])
    assert client.get(f"/files/{file_id}/raw").status_code == 200


def test_shares_are_never_passed_on(client, people):
    _as(client, "alice")
    file_id = _upload(client)
    _share(client, "file", file_id, "user", target=people["bob"])
    _as(client, "bob")
    assert _share(client, "file", file_id, "user", target=people["carol"]).status_code == 404
    assert _share(client, "file", file_id, "link").status_code == 404


# ------------------------------------------------------------------ folders
def test_a_shared_folder_opens_from_itself_down_and_nothing_above(client, conn, people):
    lib = _library(conn, people)
    _as(client, "alice")
    top = _folder(client, lib, "Finance")
    secret = _upload(client, "salaries.md", b"# Pay\n", lib, top)
    shared = _folder(client, lib, "Rotas", top)
    inner = _folder(client, lib, "2026", shared)
    rota = _upload(client, "jan.md", MD, lib, inner)
    assert _share(client, "folder", shared, "user", target=people["bob"]).status_code == 201

    _as(client, "bob")
    listing = client.get("/api/browse", params={"collection_id": lib, "folder_id": shared}).json()
    assert [p["name"] for p in listing["path"]] == ["Rotas"]          # "Finance" above it stays hidden
    assert [f["name"] for f in listing["folders"]] == ["2026"] and not listing["can_write"]
    deeper = client.get("/api/browse", params={"collection_id": lib, "folder_id": inner}).json()
    assert [p["name"] for p in deeper["path"]] == ["Rotas", "2026"] and [f["id"] for f in deeper["files"]] == [rota]
    assert client.get(f"/files/{rota}/raw").status_code == 200
    for params in ({"collection_id": lib, "folder_id": top}, {"collection_id": lib}):
        assert client.get("/api/browse", params=params).status_code == 404
    assert client.get(f"/files/{secret}/raw").status_code == 404
    assert lib not in [loc["id"] for loc in client.get("/api/locations").json()["locations"]]
    props = client.get("/api/properties", params={"type": "folder", "id": inner}).json()
    assert props["where"] == "Shared with me / Rotas"


def test_a_folder_shared_can_edit_takes_new_files(client, conn, people):
    lib = _library(conn, people)
    _as(client, "alice")
    shared = _folder(client, lib, "Drop box")
    _share(client, "folder", shared, "user", target=people["bob"], can_write=True)
    _as(client, "bob")
    _upload(client, "report.md", MD, lib, shared)
    _as(client, "alice")
    names = [f["name"] for f in client.get("/api/browse", params={"collection_id": lib, "folder_id": shared}).json()["files"]]
    assert names == ["report.md"]


# ------------------------------------------------------------------ anyone with the link
def _link(client, type_, id_, **extra):
    r = _share(client, type_, id_, "link", **extra)
    assert r.status_code == 201, r.text
    return r.json()["link"]


def test_a_link_names_nothing_and_only_its_hash_is_kept(client, conn, people):
    _as(client, "alice")
    file_id = _upload(client, "night-audit.md")
    url = _link(client, "file", file_id)
    token = url.rsplit("/s/", 1)[1]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)                  # 256 bits, URL-safe, no padding
    assert "night" not in url and "audit" not in url                   # the name is nowhere in it
    row = conn.execute("SELECT * FROM item_shares WHERE kind = 'link'").fetchone()
    assert token not in " ".join(str(v) for v in tuple(row))          # never stored...
    assert row["link_hash"] == sharing.token_hash(token)               # ...only its SHA-256
    assert row["expires_at"] is not None                               # 7 days unless chosen
    again = client.get("/api/shares", params={"type": "file", "id": file_id}).json()["shares"]
    assert again[0]["url"] == url                                      # rebuilt to copy again


def test_a_link_downloads_for_anyone_and_shows_nothing_as_a_page(client, people):
    _as(client, "alice")
    file_id = _upload(client, "x.html", b"<script>alert(1)</script>")
    token = _link(client, "file", file_id).rsplit("/s/", 1)[1]
    client.post("/logout")
    page = client.get(f"/s/{token}")
    assert page.status_code == 200 and "x.html" in page.text and "<script>alert" not in page.text
    assert page.headers["referrer-policy"] == "no-referrer" and "noindex" in page.headers["x-robots-tag"]
    assert "default-src 'none'" in page.headers["content-security-policy"]
    got = client.get(f"/s/{token}/download")
    assert got.status_code == 200 and got.content == b"<script>alert(1)</script>"
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment")


def test_every_dead_link_is_the_same_bare_404(client, conn, people):
    _as(client, "alice")
    keep, gone, old = _upload(client, "a.md"), _upload(client, "b.md", b"# b\n"), _upload(client, "c.md", b"# c\n")
    live = _link(client, "file", keep).rsplit("/s/", 1)[1]
    removed = _link(client, "file", gone)
    removed_id = client.get("/api/shares", params={"type": "file", "id": gone}).json()["shares"][0]["id"]
    client.delete(f"/api/shares/{removed_id}")
    expired = _link(client, "file", old, expires_days=1)
    conn.execute("UPDATE item_shares SET expires_at = datetime('now', '-1 minute') WHERE file_id = ?", (old,))
    client.post("/logout")
    answers = []
    for token in ("x" * 43, live[:-1] + ("A" if live[-1] != "A" else "B"), removed.rsplit("/s/", 1)[1],
                  expired.rsplit("/s/", 1)[1], "short", "y" * 200):
        for suffix in ("", "/download"):
            r = client.get(f"/s/{token}{suffix}")
            answers.append((r.status_code, r.text, r.headers.get("referrer-policy")))
    assert set(answers) == {(404, "Not found", "no-referrer")}
    assert client.get(f"/s/{live}").status_code == 200


def test_a_folder_link_opens_only_inside_its_folder(client, conn, people):
    lib = _library(conn, people)
    _as(client, "alice")
    outside = _upload(client, "outside.md", b"# no\n", lib)
    shared = _folder(client, lib, "Menus")
    sub = _folder(client, lib, "Summer", shared)
    menu = _upload(client, "menu.md", MD, lib, sub)
    other = _folder(client, lib, "Private")
    token = _link(client, "folder", shared).rsplit("/s/", 1)[1]
    client.post("/logout")
    top = client.get(f"/s/{token}")
    assert top.status_code == 200 and "Summer" in top.text and "outside.md" not in top.text
    assert client.get(f"/s/{token}/folder/{sub}").status_code == 200
    assert client.get(f"/s/{token}/file/{menu}").content == MD
    assert client.get(f"/s/{token}/file/{outside}").status_code == 404
    assert client.get(f"/s/{token}/folder/{other}").status_code == 404


def test_links_are_view_only_and_need_a_secret_key(client, people, settings):
    _as(client, "alice")
    file_id = _upload(client)
    r = _share(client, "file", file_id, "link", can_write=True)
    assert r.status_code == 201 and not r.json()["shares"][0]["can_write"]
    settings.secret_key = ""
    with pytest.raises(sharing.ShareRefused):
        sharing.link_token("", "nonce")
