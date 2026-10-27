"""The phone pass: the pages carry the controls a phone needs (the ☰ menu, the chat drawers, Select and
Actions in Files, folding side lists), the stylesheet sizes full-height pages from the measured top bar
(--chrome, --page-h) rather than a fixed 3.6rem, and every script parses (node, where it is installed)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemerynne import users

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "mnemerynne" / "web" / "static"
NODE = shutil.which("node")


def _login(client, username="alice", password="password123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


@pytest.mark.skipif(not NODE, reason="needs node")
@pytest.mark.parametrize("script", sorted(p.name for p in STATIC.glob("*.js")))
def test_every_script_parses(script):
    subprocess.run([NODE, "--check", str(STATIC / script)], check=True)


def test_the_stylesheets_size_pages_from_the_measured_bar():
    css = (STATIC / "erynne.css").read_text(encoding="utf-8")
    assert "--chrome: 3.6rem" in css and "--page-h: calc(100dvh - var(--chrome))" in css
    # No page subtracts a fixed bar height from 100vh any more (the one left is a dialog's max-height).
    assert re.findall(r"calc\(100vh - \d[^)]*\)", css) == ["calc(100vh - 2rem)"]
    assert "height: var(--page-h)" in css and "viewport-fit" not in css
    for selector in (".drawer", ".drawer-backdrop", ".topbar.main-bar", ".nav-more.open", "@media (pointer: coarse)",
                     ".fm-list.selecting", ".ax-side > summary", ".ed-switch", "env(safe-area-inset-bottom"):
        assert selector in css, selector
    media = (STATIC / "media.css").read_text(encoding="utf-8")
    assert "calc(100vh - 5.4rem)" not in media and "var(--page-h" in media and ".mc-dock" in media


def test_the_pages_carry_the_phone_controls(client, conn):
    users.create_user(conn, "alice", "password123")
    users.create_user(conn, "boss", "password123", role="admin")
    _login(client)
    base = client.get("/").text
    assert 'content="width=device-width, initial-scale=1, viewport-fit=cover"' in base and "nav.js" in base
    assert 'class="topbar main-bar"' in base and 'id="nav-menu"' in base and 'id="nav-more"' in base
    assert 'id="chat-history-toggle"' in base and 'id="chat-new"' in base and 'id="chat-history"' in base
    # The ☰ menu holds what the desktop bar shows inline, in the same order (Help before Sign out).
    assert base.index('id="nav-more"') < base.index('href="/help"') < base.index("Sign out")
    files = client.get("/files").text
    for control in ('id="btn-select"', 'id="btn-actions"', 'id="btn-tree"', 'id="fm-side"', 'class="crumbs-row"'):
        assert control in files, control
    assert files.count("fm-desk") >= 12                                 # the desktop-only toolbar buttons
    help_page = client.get("/help/user/phone").text
    assert "<details class=\"ax-side ax-tree glass\" open>" in help_page and "Contents ▾" in help_page
    assert 'class="topbar main-bar"' in help_page and 'id="nav-menu"' in help_page
    assert "Press and hold" in help_page and 'href="/help/user/media' in client.get("/help/user/phone").text
    preferences = client.get("/preferences").text
    assert 'id="new-token"' in preferences                               # the token form wraps on phones (css)
    client.post("/logout")
    _login(client, "boss")
    admin = client.get("/admin/libraries").text
    for side in ('id="ax-side"', 'id="api-side"', 'id="remote-side"'):
        assert f'<details class="ax-side ax-tree glass" {side} open>' in admin, side


def test_the_media_and_remote_pages_fit_a_phone():
    media = (REPO / "mnemerynne" / "web" / "templates" / "media.html").read_text(encoding="utf-8")
    assert 'class="mc-dock"' in media and media.index('id="mc-dock"') < media.index('id="mc-np"') < media.index('id="mc-remote"')
    remote = (REPO / "mnemerynne" / "web" / "templates" / "remote.html").read_text(encoding="utf-8")
    assert 'id="remote-list-button"' in remote
    editor = (REPO / "mnemerynne" / "web" / "templates" / "editor.html").read_text(encoding="utf-8")
    assert 'class="topbar doc-bar"' in editor and 'id="ed-show-preview"' in editor
