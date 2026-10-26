"""The built-in help wiki: a user section for everyone, an admin section for admins, opened from the Help
button; its pages are the ones shipped in mnemerynne/help, and every link between them resolves."""

import re

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import users
from mnemerynne.web import help as help_pages
from mnemerynne.web.app import Services, create_app


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


def _login(client, name):
    return client.post("/login", data={"username": name, "password": "password123"}, follow_redirects=False)


@pytest.fixture
def people(conn):
    users.create_user(conn, "alice", "password123")
    users.create_user(conn, "root", "password123", role="admin")


def test_help_opens_on_the_first_user_page(settings, conn, people):
    first = help_pages.load_help()["user"][0]
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        r = c.get("/help", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == first.url
        page = c.get(first.url)
        assert page.status_code == 200 and first.title in page.text and 'class="tree help-toc"' in page.text
        assert "/help/admin/" not in page.text                              # no admin section for her
        assert page.headers["content-security-policy"].startswith("default-src 'none'")


def test_admin_pages_only_for_admins(settings, conn, people):
    admin_page = help_pages.load_help()["admin"][0]
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        assert c.get(admin_page.url).status_code == 404                     # as if it didn't exist
        assert c.get("/help/user/no-such-page").status_code == 404
        assert c.get("/help/nope/welcome").status_code == 404
    with TestClient(_app(settings)) as c:
        _login(c, "root")
        page = c.get(admin_page.url)
        assert page.status_code == 200 and admin_page.title in page.text
        assert "/help/user/" in page.text and "/help/admin/" in page.text   # both sections to switch between


def test_help_needs_a_login(settings, conn, people):
    with TestClient(_app(settings)) as c:
        r = c.get("/help", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_help_button_sits_left_of_sign_out_and_opens_a_new_tab(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "alice")
        nav = c.get("/").text
        assert 'href="/help" target="_blank"' in nav
        assert nav.index('href="/help"') < nav.index("Sign out")
        me = c.get("/api/me").json()
        assert me["is_admin"] is False
    with TestClient(_app(settings)) as c:
        _login(c, "root")
        assert c.get("/api/me").json()["is_admin"] is True


def test_every_page_has_a_title_and_its_links_resolve():
    pages = help_pages.load_help()
    assert pages["user"] and pages["admin"]
    for section, listed in pages.items():
        for page in listed:
            assert page.title and page.title != page.slug, page.path
            html = help_pages.render(page)
            for href in re.findall(r'href="(/help/[^"#]+)', html):
                _, _, sec, slug = href.split("/")
                assert help_pages.page_for(sec, slug) is not None, f"{page.path}: dead link {href}"
            # A relative .md link that didn't resolve is a typo: none may survive rendering.
            assert not re.findall(r'href="[^"]*\.md[#"]', html), f"{page.path}: unresolved link"


def test_links_between_pages_are_rewritten():
    first = help_pages.load_help()["user"][0]
    rewrite = help_pages.link_rewriter("admin")
    assert rewrite(f"../user/{first.slug}.md#top") == f"{first.url}#top"
    assert rewrite("missing-page.md") == "missing-page.md"
    assert rewrite("https://example.com/x.md") == "https://example.com/x.md"
    assert rewrite("#anchor") == "#anchor"
