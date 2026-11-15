"""Files' big uploads in parts (mnemerynne/ingest/parts.py): resumable, safe to retry, and checked like any upload."""

import os
import time

import pytest

from mnemerynne import users
from mnemerynne.ingest import parts

DATA = b"0123456789abcdefghij-the end"       # 28 bytes: 7 parts of 4 (the last one 4 too)


@pytest.fixture(autouse=True)
def small_parts(monkeypatch):
    monkeypatch.setattr(parts, "PART_BYTES", 4)   # tiny parts keep the tests fast


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _login(client, name="alice"):
    client.post("/login", data={"username": name, "password": "password123"})


def _start(client, name="big.txt", size=len(DATA), modified=1000, **extra):
    return client.post("/api/files/uploads", json={"name": name, "size": size, "modified": modified, **extra})


def _put(client, uid, n, data=DATA):
    return client.put(f"/api/files/uploads/{uid}/parts/{n}", content=data[n * 4:(n + 1) * 4])


def test_a_file_in_parts_arrives_whole_in_any_order(client, alice, settings):
    _login(client)
    up = _start(client).json()
    assert up["parts"] == [] and up["part_size"] == 4 and up["file_id"] is None
    for n in (3, 0, 6, 1, 2, 5, 4):                      # out of order, as retries can make it
        assert _put(client, up["id"], n).status_code == 200
    done = client.post(f"/api/files/uploads/{up['id']}/finish")
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["name"] == "big.txt" and body["size"] == len(DATA)
    assert client.get(body["download_url"]).content == DATA
    again = client.post(f"/api/files/uploads/{up['id']}/finish")   # the answer was lost: ask again, same file
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_choosing_the_same_file_again_carries_on(client, alice):
    _login(client)
    first = _start(client).json()
    _put(client, first["id"], 0)
    _put(client, first["id"], 1)
    _put(client, first["id"], 1)                         # a retried part just overwrites itself
    again = _start(client).json()
    assert again["id"] == first["id"] and again["parts"] == [0, 1]
    other = _start(client, modified=2000).json()          # changed since: a different upload
    assert other["id"] != first["id"] and other["parts"] == []


def test_a_wrong_part_is_refused(client, alice):
    _login(client)
    uid = _start(client).json()["id"]
    assert client.put(f"/api/files/uploads/{uid}/parts/0", content=b"12").status_code == 400    # too short
    assert client.put(f"/api/files/uploads/{uid}/parts/9", content=b"1234").status_code == 400  # no such part
    assert client.put(f"/api/files/uploads/{uid}/parts/0", content=b"12345").status_code == 413  # over a part
    assert client.put("/api/files/uploads/../../etc/parts/0", content=b"1234").status_code == 404
    assert client.post(f"/api/files/uploads/{uid}/finish").status_code == 400                  # parts missing


def test_only_its_owner_sees_an_upload(client, alice, conn):
    users.create_user(conn, "bob", "password123")
    _login(client)
    uid = _start(client).json()["id"]
    client.post("/logout")
    _login(client, "bob")
    assert _put(client, uid, 0).status_code == 404
    assert client.post(f"/api/files/uploads/{uid}/finish").status_code == 404
    assert client.delete(f"/api/files/uploads/{uid}").status_code == 404


def test_over_the_limit_is_refused_before_a_byte_is_sent(client, alice, settings, monkeypatch):
    _login(client)
    monkeypatch.setattr(settings, "max_file_bytes", 10)
    assert _start(client).status_code == 413


def test_a_cancelled_upload_leaves_nothing(client, alice, settings):
    _login(client)
    uid = _start(client).json()["id"]
    _put(client, uid, 0)
    assert client.delete(f"/api/files/uploads/{uid}").status_code == 200
    assert not (settings.tmp_dir / "file-uploads" / uid).exists()


def test_uploads_nobody_finished_are_swept_after_a_day(client, alice, settings, conn):
    _login(client)
    old = _start(client).json()["id"]
    fresh = _start(client, name="new.txt").json()["id"]
    folder = settings.tmp_dir / "file-uploads" / old
    day_ago = time.time() - 2 * 86400
    for p in [folder, *folder.rglob("*")]:
        os.utime(p, (day_ago, day_ago))
    assert parts.sweep(conn, settings) == 1
    assert not folder.exists() and (settings.tmp_dir / "file-uploads" / fresh).exists()
