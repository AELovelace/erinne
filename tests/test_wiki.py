"""The Wiki tab: the library called Wiki (or the one an admin picks), its page tree, pages rendered
with links between them resolved, Edit only where the person may write, and the admin setting."""

import io

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import fs, permissions, users, wiki
from mnemerynne.ingest import store
from mnemerynne.web.app import Services, create_app


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def _add(conn, settings, collection, name, data, folder=None, user=None):
    received = store.receive(io.BytesIO(data), name, settings)
    return store.add_file(conn, settings, received, collection, user, folder_id=folder)[0]


@pytest.fixture
def people(conn):
    return {"alice": users.create_user(conn, "alice", "password123"),
            "bob": users.create_user(conn, "bob", "password123"),
            "carol": users.create_user(conn, "carol", "password123"),
            "root": users.create_user(conn, "root", "password123", role="admin")}


@pytest.fixture
def world(conn, settings, people):
    lib = permissions.create_library(conn, "Wiki")
    permissions.grant_user(conn, lib, people["alice"], can_write=True)
    permissions.grant_user(conn, lib, people["bob"])
    guides = fs.create_folder(conn, fs.Access.of(conn, people["alice"]), lib, None, "Guides")
    index = _add(conn, settings, lib, "index.md",
                 b"# Home\n\nSee [intro](Guides/intro.md), [pic](Guides/diagram.png) and [manual](Guides/manual.pdf).\n",
                 user=people["alice"])
    intro = _add(conn, settings, lib, "intro.md",
                 b"# Intro\n\n[next](next.md) [up](../index.md#home) [self](#here) [out](https://x.y/z.md) "
                 b"[gone](nope.md) [escape](../../etc.md) [spaced](My%20Page.md)\n", folder=guides, user=people["alice"])
    nxt = _add(conn, settings, lib, "next.md", b"# Next\n", folder=guides, user=people["alice"])
    spaced = _add(conn, settings, lib, "My Page.md", b"# My Page\n", folder=guides, user=people["alice"])
    png = _add(conn, settings, lib, "diagram.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, folder=guides, user=people["alice"])
    pdf = _add(conn, settings, lib, "manual.pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nnot really\n", folder=guides,
               user=people["alice"])
    return {**people, "lib": lib, "guides": guides, "index": index, "intro": intro, "next": nxt, "spaced": spaced,
            "png": png, "pdf": pdf}


def test_no_wiki_library_yet(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        page = c.get("/wiki")
        assert page.status_code == 200 and "hasn't set up a wiki yet" in page.text
        assert c.get("/api/wiki").json() == {"library": None}
    with TestClient(_app(settings)) as c:
        _login(c, "root")
        assert "Access → Files" in c.get("/wiki").text                  # admins are told how to make one
        nav = c.get("/").text
        assert 'href="/wiki"' in nav and 'href="/memories"' not in nav


def test_tree_pages_and_links(settings, conn, world):
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        tree = c.get("/api/wiki").json()
        assert tree["library"]["name"] == "Wiki" and tree["can_write"]
        assert [p["name"] for p in tree["files"]] == ["index.md"]
        guides = tree["folders"][0]
        assert guides["name"] == "Guides" and guides["can_write"]
        assert [p["name"] for p in guides["files"]] == ["intro.md", "My Page.md", "next.md"]   # no png, no pdf

        page = c.get(f"/api/wiki/pages/{world['intro']}").json()
        html = page["html"]
        assert page["title"] == "intro" and page["path"] == ["Guides"] and page["can_write"]
        assert page["edit_url"] == f"/edit/{world['intro']}?back=/wiki/{world['intro']}"
        assert f'href="/wiki/{world["next"]}"' in html and f'href="/wiki/{world["index"]}#home"' in html
        assert f'href="/wiki/{world["spaced"]}"' in html
        assert 'href="#here"' in html and 'href="https://x.y/z.md"' in html
        assert 'href="nope.md"' in html and 'href="../../etc.md"' in html     # unknown and outside: as written

        home = c.get(f"/api/wiki/pages/{world['index']}").json()["html"]
        assert f'href="/files/{world["png"]}/inline"' in home and f'href="/files/{world["pdf"]}/raw"' in home
        assert c.get(f"/api/wiki/pages/{world['pdf']}").status_code == 404     # not a page
        assert c.get(f"/api/wiki/pages/{world['png']}").status_code == 404
        shown = c.get(f"/wiki/{world['intro']}").text
        assert f'data-file-id="{world["intro"]}"' in shown and 'data-can-write="yes"' in shown

    with TestClient(_app(settings)) as c:
        _login(c, "bob")                                                   # reads, can't write
        page = c.get(f"/api/wiki/pages/{world['intro']}").json()
        assert page["can_write"] is False and page["edit_url"] is None
        assert c.get("/api/wiki").json()["can_write"] is False
    with TestClient(_app(settings)) as c:
        _login(c, "carol")                                                 # no access at all
        assert c.get("/api/wiki").json() == {"library": None}
        assert c.get(f"/api/wiki/pages/{world['intro']}").status_code == 404
        assert "hasn't set up a wiki yet" in c.get("/wiki").text


def test_pages_in_the_bin_disappear(settings, conn, world):
    fs.trash(conn, fs.Access.of(conn, world["alice"]), [fs.Item("folder", world["guides"])])
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        tree = c.get("/api/wiki").json()
        assert tree["folders"] == [] and [p["name"] for p in tree["files"]] == ["index.md"]
        assert c.get(f"/api/wiki/pages/{world['intro']}").status_code == 404
        home = c.get(f"/api/wiki/pages/{world['index']}").json()["html"]
        assert 'href="Guides/intro.md"' in home                            # nowhere to point now


def test_admin_picks_another_library(settings, conn, world):
    docs = permissions.create_library(conn, "Docs")
    permissions.grant_user(conn, docs, world["alice"])
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        assert c.get("/api/admin/settings/wiki-library").status_code == 403
    with TestClient(_app(settings)) as c:
        _login(c, "root")
        got = c.get("/api/admin/settings/wiki-library").json()
        assert got["library_id"] == world["lib"] and got["source"] == "name"
        assert {l["name"] for l in got["libraries"]} == {"Wiki", "Docs"}
        picked = c.put("/api/admin/settings/wiki-library", json={"library_id": docs})
        assert picked.status_code == 200 and picked.json()["source"] == "setting" and picked.json()["name"] == "Docs"
        assert c.put("/api/admin/settings/wiki-library", json={"library_id": 999999}).status_code == 422
        inbox = permissions.private_collection_id(conn, world["alice"])
        assert c.put("/api/admin/settings/wiki-library", json={"library_id": inbox}).status_code == 422
        log = conn.execute("SELECT action, target, detail FROM access_log ORDER BY id DESC LIMIT 1").fetchone()
        assert tuple(log) == ("settings.wiki_library", "Docs", "was Wiki")
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        assert c.get("/api/wiki").json()["library"]["name"] == "Docs"
    with TestClient(_app(settings)) as c:
        _login(c, "root")
        assert c.put("/api/admin/settings/wiki-library", json={"library_id": None}).json()["source"] == "name"
    assert wiki.library_row(conn)["name"] == "Wiki"
