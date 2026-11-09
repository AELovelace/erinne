"""Downloading folders and several files as one zip (downloads.py, POST /api/downloads, GET /files/zip/…)."""

import io
import zipfile

import pytest

from mnemerynne import downloads, fs, permissions, users

MD = b"# Notes\n\n" + b"Checkout is at 11am. " * 200 + b"\n"   # compresses well: should be deflated
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4           # stored as it is


@pytest.fixture
def people(conn):
    return {name: users.create_user(conn, name, "password123") for name in ("alice", "bob")}


def _login(client, name):
    client.post("/logout")
    client.post("/login", data={"username": name, "password": "password123"})


def _put(client, cid, path, data=MD):
    """Upload `data` as `path` ("Reports/2026/q1.md") into collection cid; returns the file's id."""
    r = client.post("/api/files", files={"file": (path.rsplit("/", 1)[-1], data, "application/octet-stream")},
                    data={"collection_id": str(cid), "relative_path": path})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _folders(client, cid):
    return {f["name"]: f["id"] for f in client.get(f"/api/collections/{cid}/folders").json()["folders"]}


def _zip(client, body):
    """Ask for a zip, download it; (the answer, the zip opened)."""
    asked = client.post("/api/downloads", json=body)
    assert asked.status_code == 200, asked.text
    got = client.get(asked.json()["url"])
    assert got.status_code == 200, got.text
    return asked.json(), got, zipfile.ZipFile(io.BytesIO(got.content))


@pytest.fixture
def inbox(client, people):
    _login(client, "alice")
    cid = client.get("/api/locations").json()["locations"][0]["id"]
    _put(client, cid, "Reports/2026/q1.md")
    _put(client, cid, "Reports/2026/chart.png", PNG)
    _put(client, cid, "Reports/summary.md", MD + b"x")
    _put(client, cid, "Loose/summary.md", MD + b"y")      # the same name as Reports/summary.md
    client.post("/api/folders", json={"collection_id": cid, "parent_id": _folders(client, cid)["Reports"],
                                      "name": "Empty"})
    return cid


def test_a_folder_comes_down_as_one_zip_with_everything_inside(client, inbox):
    reports = _folders(client, inbox)["Reports"]
    asked, got, zf = _zip(client, {"items": [{"type": "folder", "id": reports}]})
    assert asked["name"] == "Reports.zip" and asked["files"] == 3 and asked["folders"] == 3
    assert asked["bytes"] == len(MD) * 2 + 1 + len(PNG)
    assert got.headers["content-type"] == "application/zip"
    assert got.headers["content-disposition"].startswith('attachment; filename="Reports.zip"')
    assert got.headers["x-accel-buffering"] == "no"
    assert sorted(zf.namelist()) == ["Reports/", "Reports/2026/", "Reports/2026/chart.png", "Reports/2026/q1.md",
                                     "Reports/Empty/", "Reports/summary.md"]
    assert zf.testzip() is None                                       # every CRC checks out
    assert zf.read("Reports/2026/q1.md") == MD and zf.read("Reports/2026/chart.png") == PNG
    assert zf.getinfo("Reports/2026/q1.md").compress_type == zipfile.ZIP_DEFLATED
    assert zf.getinfo("Reports/2026/chart.png").compress_type == zipfile.ZIP_STORED
    assert zf.getinfo("Reports/Empty/").is_dir()


def test_several_items_share_the_top_and_same_names_are_kept_apart(client, inbox):
    tree = _folders(client, inbox)
    files = {f["name"]: f["id"] for f in client.get(
        f"/api/browse?collection_id={inbox}&folder_id={tree['Reports']}").json()["files"]}
    loose = client.get(f"/api/browse?collection_id={inbox}&folder_id={tree['Loose']}").json()["files"][0]["id"]
    asked, _, zf = _zip(client, {"items": [{"type": "file", "id": files["summary.md"]}, {"type": "file", "id": loose},
                                           {"type": "folder", "id": tree["2026"]}]})
    assert sorted(zf.namelist()) == ["2026/", "2026/chart.png", "2026/q1.md", "summary (2).md", "summary.md"]
    assert asked["name"] == "Download.zip"                           # from two different folders
    assert zf.read("summary.md") == MD + b"x" and zf.read("summary (2).md") == MD + b"y"
    # Two from the same folder: named after it.
    asked, _, _ = _zip(client, {"items": [{"type": "file", "id": files["summary.md"]},
                                          {"type": "folder", "id": tree["2026"]}]})
    assert asked["name"] == "Reports.zip"


def test_everything_in_a_place_and_nothing_from_the_bin(client, inbox):
    tree = _folders(client, inbox)
    q1 = next(f["id"] for f in client.get(f"/api/browse?collection_id={inbox}&folder_id={tree['2026']}").json()["files"]
              if f["name"] == "q1.md")
    assert client.post("/api/items/trash", json={"items": [{"type": "file", "id": q1},
                                                           {"type": "folder", "id": tree["Empty"]}]}).json()["ok"]
    asked, _, zf = _zip(client, {"place": {"collection_id": inbox}})
    assert asked["name"] == f"{permissions.PRIVATE_LABEL}.zip"
    names = set(zf.namelist())
    assert {"Agents/", "Loose/summary.md", "Reports/2026/chart.png", "Reports/summary.md"} <= names
    assert not any("q1.md" in n or "Empty" in n for n in names)
    # A folder's contents (Select all inside it) are at the top of a zip named after it.
    asked, _, zf = _zip(client, {"place": {"collection_id": inbox, "folder_id": tree["Reports"]}})
    assert asked["name"] == "Reports.zip" and sorted(zf.namelist()) == ["2026/", "2026/chart.png", "summary.md"]


def test_other_people_get_nothing(client, inbox):
    reports = _folders(client, inbox)["Reports"]
    link = client.post("/api/downloads", json={"items": [{"type": "folder", "id": reports}]}).json()["url"]
    _login(client, "bob")
    assert client.post("/api/downloads", json={"items": [{"type": "folder", "id": reports}]}).status_code == 404
    assert client.post("/api/downloads", json={"place": {"collection_id": inbox}}).status_code == 404
    assert client.post("/api/downloads", json={"place": {"collection_id": inbox, "folder_id": reports}}).status_code == 404
    assert client.get(link).status_code == 404                       # alice's link is no use to bob
    _login(client, "alice")
    assert client.get(link[:-3] + "abc").status_code == 404          # tampered
    assert client.post("/api/downloads", json={}).status_code == 422
    client.post("/logout")
    assert client.post("/api/downloads", json={"place": {"collection_id": inbox}}).status_code == 401


def test_a_folder_seen_only_on_the_way_down_gives_no_files_of_its_own(client, conn, people):
    lib = permissions.create_library(conn, "Ops")
    permissions.grant_user(conn, lib, people["bob"], can_write=True)
    _login(client, "bob")
    _put(client, lib, "Team/budget.md")
    _put(client, lib, "Team/Shared/rota.md")
    shared = _folders(client, lib)["Shared"]
    permissions.grant_folder(conn, shared, "user", people["alice"])
    _login(client, "alice")
    team = _folders(client, lib)["Team"]
    _, _, zf = _zip(client, {"items": [{"type": "folder", "id": team}]})
    assert sorted(zf.namelist()) == ["Team/", "Team/Shared/", "Team/Shared/rota.md"]


def test_too_many_files_is_refused_with_a_message(client, inbox, monkeypatch):
    monkeypatch.setattr(downloads, "MAX_FILES", 2)
    r = client.post("/api/downloads", json={"place": {"collection_id": inbox}})
    assert r.status_code == 413 and "more than 2 files" in r.json()["detail"]


def test_a_file_that_cant_be_read_while_zipping_is_named_not_dropped_silently(tmp_path):
    there = tmp_path / "a.txt"
    there.write_bytes(b"hello")
    plan = downloads.Plan("x", [downloads.Entry("Dir/"), downloads.Entry("Dir/a.txt", 1, 5, True),
                                downloads.Entry("Dir/gone.txt", 2, 5)])
    data = b"".join(downloads.stream(plan, {1: there, 2: tmp_path / "missing.txt"}))
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.read("Dir/a.txt") == b"hello"
    assert "Dir/gone.txt" in zf.read(downloads.NOT_INCLUDED).decode()
    assert "Dir/gone.txt" not in zf.namelist()


@pytest.mark.parametrize("name, safe", [("a/b", "a_b"), ("..", "_"), ("", "_"), ("x\\y\x01", "x_y_"), ("ok.md", "ok.md")])
def test_zip_names_cant_climb_out(name, safe):
    assert downloads.safe_name(name) == safe


def test_fs_refusals_keep_their_status():
    assert issubclass(downloads.TooMany, fs.FsError) and downloads.TooMany.status == 413
