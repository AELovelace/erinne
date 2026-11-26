"""The ONLYOFFICE editor (mnemerynne/office.py, web/office.py): the signed config and its modes, the Document
Server's download and save callback (JWT checked both ways, versions saved as the person who edited, stale keys and
lost rights turned into copies, unsigned or foreign requests refused), and the lock that keeps Claude, the drive and
the text editor off a file while it's open in the editor."""

import io
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import db, editing, office, permissions, users
from mnemerynne.ingest import store
from mnemerynne.web.app import Services, create_app

SECRET = "test-onlyoffice-secret-0123456789"
DS = "http://127.0.0.1:8090/"                               # the Document Server as this server reaches it
OLD = b"PK\x03\x04 the original letter"                     # stands in for a .docx (never parsed here)
EDITED = b"PK\x03\x04 the letter after editing"


@pytest.fixture
def oo(settings, conn):
    """Settings with the editor set up and switched on."""
    settings.onlyoffice_url = "https://erynne.example.org/onlyoffice/"
    settings.onlyoffice_jwt_secret = SECRET
    settings.onlyoffice_internal_url = DS
    settings.public_url = "https://erynne.example.org"
    office.set_on(conn, True)
    return settings


@pytest.fixture
def world(conn, settings):
    alice = users.create_user(conn, "alice", "password123")       # may write in Brand
    bob = users.create_user(conn, "bob", "password123")           # may only read Brand
    carol = users.create_user(conn, "carol", "password123")       # no access to Brand at all
    brand = permissions.create_library(conn, "Brand")
    permissions.grant_user(conn, brand, alice, can_write=True)
    permissions.grant_user(conn, brand, bob)
    return {"alice": alice, "bob": bob, "carol": carol, "brand": brand}


@pytest.fixture
def fetched(monkeypatch, settings):
    """office.download replaced: 'fetches' whatever bytes the test puts in box['data']."""
    box = {"data": EDITED, "urls": []}

    def fake(_settings, url):
        box["urls"].append(url)
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        path = Path(settings.tmp_dir) / f"fake-{len(box['urls'])}.part"
        path.write_bytes(box["data"])
        return path, editing.sha256(box["data"])

    monkeypatch.setattr(office, "download", fake)
    return box


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


def _client(settings, ip="127.0.0.1"):
    # https: public_url is https, so the session cookie is https-only. The client address is where the call is from.
    return TestClient(_app(settings), base_url="https://testserver", client=(ip, 50123))


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def _add(conn, settings, collection, name, data, user=None):
    received = store.receive(io.BytesIO(data), name, settings)
    return store.add_file(conn, settings, received, collection, user)[0]


def _path_of(url):
    """A config URL (absolute, on the public host) as a path + query the TestClient can call."""
    parts = urlsplit(url)
    return parts.path + "?" + parts.query


def _callback(client, cfg, status, users_, *, url=DS + "cache/files/abc/output.docx", key=None, sign=True,
              secret=SECRET):
    """POST the Document Server's callback for the session in `cfg`, signed like the real one."""
    body = {"key": key or cfg["document"]["key"], "status": status, "users": [str(u) for u in users_], "url": url}
    if status == 6:
        body["forcesavetype"] = 0
    if sign:
        body["token"] = office.jwt_encode(dict(body), secret)
    return client.post(_path_of(cfg["editorConfig"]["callbackUrl"]), json=body)


def _versions(conn, fid):
    return conn.execute("SELECT * FROM file_versions WHERE file_id = ? ORDER BY id", (fid,)).fetchall()


# ------------------------------------------------------------------ JWT
def test_jwt_round_trip_and_tampering():
    tok = office.jwt_encode({"a": 1}, SECRET)
    assert office.jwt_decode(tok, SECRET)["a"] == 1
    head, body, sig = tok.split(".")
    with pytest.raises(office.OfficeForbidden):
        office.jwt_decode(tok, "another secret")                                 # wrong secret
    with pytest.raises(office.OfficeForbidden):
        office.jwt_decode(f"{head}.{office._b64(b'{\"a\":2}')}.{sig}", SECRET)    # changed payload
    none_head = office._b64(b'{"alg":"none","typ":"JWT"}')
    with pytest.raises(office.OfficeForbidden):
        office.jwt_decode(f"{none_head}.{body}.", SECRET)                         # alg none
    with pytest.raises(office.OfficeForbidden):
        office.jwt_decode(office.jwt_encode({"exp": 1}, SECRET, ttl=None), SECRET)   # expired


# ------------------------------------------------------------------ the config and the page
def test_config_modes_and_refusals(settings, conn, world, oo):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    md = _add(conn, settings, world["brand"], "notes.md", b"# hi\n", world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        assert cfg["editorConfig"]["mode"] == "edit" and cfg["documentType"] == "word"
        assert cfg["editorConfig"]["user"] == {"id": str(world["alice"]), "name": "alice"}
        assert "callbackUrl" in cfg["editorConfig"] and cfg["document"]["permissions"]["edit"] is True
        signed = office.jwt_decode(cfg["token"], SECRET)                         # the Document Server trusts this
        assert signed["document"]["key"] == cfg["document"]["key"]
        again = c.get(f"/api/office/{fid}/config").json()
        assert again["document"]["key"] == cfg["document"]["key"]                # same session: co-editing
        assert c.get(f"/office/{fid}").status_code == 200                       # the page
        assert c.get(f"/api/office/{md}/config").status_code == 415              # not an Office file
        listing = c.get(f"/api/files/{fid}").json()
        assert listing["office"] == "edit"

        c.post("/logout")
        _login(c, "bob")                                                         # reader: view only, no callback
        view = c.get(f"/api/office/{fid}/config").json()
        assert view["editorConfig"]["mode"] == "view" and "callbackUrl" not in view["editorConfig"]
        assert view["document"]["permissions"]["edit"] is False

        c.post("/logout")
        _login(c, "carol")                                                       # stranger: same 404 as missing
        assert c.get(f"/api/office/{fid}/config").status_code == 404

        c.post("/logout")
        _login(c, "alice")
        settings.onlyoffice_max_bytes = 5                                        # too big
        assert c.get(f"/api/office/{fid}/config").status_code == 413
        settings.onlyoffice_max_bytes = 50 * 1024 * 1024
        office.set_on(conn, False)                                               # switched off
        assert c.get(f"/api/office/{fid}/config").status_code == 404
        office.set_on(conn, True)
        settings.onlyoffice_jwt_secret = ""                                      # not configured
        assert c.get(f"/api/office/{fid}/config").status_code == 404


def test_ds_auth_needs_a_signed_in_person(settings, conn, world, oo):
    with _client(settings) as c:
        assert c.get("/api/office/ds-auth").status_code == 401
        _login(c, "bob")
        assert c.get("/api/office/ds-auth").status_code == 204


# ------------------------------------------------------------------ the Document Server's download
def test_download_needs_our_token_their_jwt_and_their_network(settings, conn, world, oo):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        url = _path_of(cfg["document"]["url"])
        signed = {"Authorization": "Bearer " + office.jwt_encode({"payload": {"url": "x"}}, SECRET)}
        assert c.get(url).status_code == 403                                     # no JWT
        bad = {"Authorization": "Bearer " + office.jwt_encode({"payload": {}}, "wrong")}
        assert c.get(url, headers=bad).status_code == 403                        # wrong secret
        assert c.get(url.replace("t=", "t=x"), headers=signed).status_code == 403   # not our token
        got = c.get(url, headers=signed)
        assert got.status_code == 200 and got.content == OLD
    with _client(settings, ip="203.0.113.9") as outside:
        assert outside.get(url, headers=signed).status_code == 403               # not from the container's network


# ------------------------------------------------------------------ the save callback
def test_unsigned_and_foreign_callbacks_are_refused(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        assert _callback(c, cfg, 2, [world["alice"]], sign=False).status_code == 403
        assert _callback(c, cfg, 2, [world["alice"]], secret="not the secret").status_code == 403
        other = _callback(c, cfg, 2, [world["alice"]], key=f"{fid}-someone-elses-key")
        assert other.status_code == 403                                         # key isn't the one its link was for
        evil = _callback(c, cfg, 2, [world["alice"]], url="http://169.254.169.254/latest/meta-data")
        assert evil.status_code == 403                                          # never fetch other addresses
    with _client(settings, ip="203.0.113.9") as outside:
        assert _callback(outside, cfg, 2, [world["alice"]]).status_code == 403
    assert _versions(conn, fid) == [] and fetched["urls"] == []
    assert store.content_path(conn, settings, fid).read_bytes() == OLD


def test_forcesave_then_close_saves_versions_as_the_editor(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        assert _callback(c, cfg, 1, [world["alice"]]).json() == {"error": 0}     # alice has it open

        r = _callback(c, cfg, 6, [world["alice"]], url="https://erynne.example.org/onlyoffice/cache/files/a/out.docx")
        assert r.json() == {"error": 0}
        assert fetched["urls"][-1] == DS + "cache/files/a/out.docx"              # public path → loopback port
        assert store.content_path(conn, settings, fid).read_bytes() == EDITED
        (v1,) = _versions(conn, fid)
        assert v1["via"] == "onlyoffice" and v1["created_by"] == world["alice"] and v1["sha256"] == editing.sha256(OLD)
        assert conn.execute("SELECT edited_via FROM files WHERE id = ?", (fid,)).fetchone()[0] == "onlyoffice:alice"
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE file_id = ? AND status = 'queued'",
                            (fid,)).fetchone()[0]                                # indexed again
        sess = office.session(conn, fid)
        assert sess and sess.base_sha256 == editing.sha256(EDITED)              # still open, moved on

        fetched["data"] = EDITED + b" and closed"
        assert _callback(c, cfg, 2, [world["alice"]]).json() == {"error": 0}     # everyone closed: saved, ended
        assert len(_versions(conn, fid)) == 2
        assert office.session(conn, fid) is None
        history = c.get(f"/api/files/{fid}/versions").json()["versions"]
        assert history[0]["replaced_by"] == "the Office editor for alice"


def test_status_4_ends_the_session_without_a_version(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        _callback(c, cfg, 1, [world["alice"]])
        assert _callback(c, cfg, 4, []).json() == {"error": 0}
    assert office.session(conn, fid) is None and _versions(conn, fid) == []


def test_stale_key_becomes_a_copy_not_an_overwrite(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        _callback(c, cfg, 1, [world["alice"]])
        _callback(c, cfg, 4, [])                                                 # that session is over
        assert _callback(c, cfg, 2, [world["alice"]]).json() == {"error": 0}     # a late save with its old key
    assert _versions(conn, fid) == []
    assert store.content_path(conn, settings, fid).read_bytes() == OLD           # untouched
    copy = conn.execute("SELECT * FROM files WHERE display_name LIKE 'letter (editor copy, alice %).docx'").fetchone()
    assert copy is not None and copy["collection_id"] == world["brand"]          # beside the original
    assert store.content_path(conn, settings, copy["id"]).read_bytes() == EDITED
    note = conn.execute("SELECT body FROM user_messages WHERE to_user_id = ?", (world["alice"],)).fetchone()
    assert note and "editor copy" in note["body"]


def test_changed_underneath_becomes_a_copy(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()          # opened, but the Document Server hasn't reported
        editing.replace_content(conn, settings, fid, b"PK\x03\x04 Claude's change", expected=None,
                                actor_id=world["alice"], via="claude", summary="claude edit")   # nobody locked it yet
        assert _callback(c, cfg, 2, [world["alice"]]).json() == {"error": 0}
    assert store.content_path(conn, settings, fid).read_bytes() == b"PK\x03\x04 Claude's change"   # the check won
    assert [v["via"] for v in _versions(conn, fid)] == ["claude"]
    assert conn.execute("SELECT COUNT(*) FROM files WHERE display_name LIKE '%(editor copy, alice %'").fetchone()[0] == 1
    assert office.session(conn, fid) is None


def test_editor_without_write_rights_gets_a_copy_in_their_inbox(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        _callback(c, cfg, 1, [world["alice"]])
        permissions.revoke_user(conn, world["brand"], world["alice"])            # rights gone mid-edit
        permissions.grant_user(conn, world["brand"], world["alice"])             # read only now
        assert _callback(c, cfg, 2, [world["alice"]]).json() == {"error": 0}
    assert _versions(conn, fid) == []
    assert store.content_path(conn, settings, fid).read_bytes() == OLD
    inbox = permissions.private_collection_id(conn, world["alice"])
    copy = conn.execute("SELECT collection_id FROM files WHERE display_name LIKE '%(editor copy, alice %'").fetchone()
    assert copy and copy["collection_id"] == inbox


# ------------------------------------------------------------------ the lock against other writers
def test_open_session_locks_out_claude_the_drive_and_the_text_editor(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        _callback(c, cfg, 1, [world["alice"]])                                   # the editor reports alice in it
        for via in ("claude", "erynne", "drive", "web"):
            with pytest.raises(editing.EditConflict, match="open in the Office editor by alice"):
                editing.replace_content(conn, settings, fid, b"PK\x03\x04 x", expected=None,
                                        actor_id=world["alice"], via=via, summary="x")
        src = Path(settings.tmp_dir) / "drive.part"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"PK\x03\x04 drive")
        with pytest.raises(editing.EditConflict):
            editing.replace_from_path(conn, settings, fid, src, editing.sha256(b"PK\x03\x04 drive"), expected=None,
                                      actor_id=world["alice"], via="drive", summary="drive")
        _callback(c, cfg, 4, [])                                                 # closed: unlocked
    editing.replace_content(conn, settings, fid, b"PK\x03\x04 now fine", expected=None,
                            actor_id=world["alice"], via="claude", summary="after")
    assert store.content_path(conn, settings, fid).read_bytes() == b"PK\x03\x04 now fine"


def test_lock_lapses_and_restore_ends_the_session(settings, conn, world, oo, fetched):
    fid = _add(conn, settings, world["brand"], "letter.docx", OLD, world["alice"])
    with _client(settings) as c:
        _login(c, "alice")
        cfg = c.get(f"/api/office/{fid}/config").json()
        _callback(c, cfg, 1, [world["alice"]])
        conn.execute("UPDATE office_sessions SET last_status_at = datetime('now', '-13 hours')")   # silent too long
        editing.replace_content(conn, settings, fid, b"PK\x03\x04 v2", expected=None, actor_id=world["alice"],
                                via="claude", summary="lapsed lock")             # allowed: the session is dead

        cfg = c.get(f"/api/office/{fid}/config").json()                         # a fresh session
        _callback(c, cfg, 1, [world["alice"]])
        version = _versions(conn, fid)[0]["id"]
        editing.restore(conn, settings, fid, version, world["alice"])            # restore wins, session ends
        assert office.session(conn, fid) is None
        assert _callback(c, cfg, 2, [world["alice"]]).json() == {"error": 0}     # their save → a copy
    assert store.content_path(conn, settings, fid).read_bytes() == OLD
    assert conn.execute("SELECT COUNT(*) FROM files WHERE display_name LIKE '%(editor copy, alice %'").fetchone()[0] == 1


def test_admin_settings(settings, conn, world, oo):
    users.set_role(conn, "alice", "admin")                                       # admins only
    with _client(settings) as c:
        _login(c, "alice")
        got = c.get("/api/admin/settings/office").json()
        assert got["configured"] and got["on"]
        off = c.put("/api/admin/settings/office", json={"on": False, "group": ""}).json()
        assert off["on"] is False and not office.is_on(conn)
        settings.onlyoffice_url = ""
        assert c.put("/api/admin/settings/office", json={"on": True, "group": ""}).status_code == 409
