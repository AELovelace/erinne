"""Retro on a paired TV (2026-11-14): its pages open by the TV's screen key, as the TV's owner, without the site's menu
or the owner's messages, inside Media's game layer (the TV context marks them framed); the TV's Media page offers
Retro in its game layer and 🔗 Controllers, and loads the controller helper. Signed out and not a TV: sign in."""

from fastapi.testclient import TestClient

from mnemerynne import share
from test_retro import _games, _login, _put, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

FRAME = {"Sec-Fetch-Dest": "iframe"}


def _tv(client):
    """The signed-in owner pairs this browser as a screen, then signs out: only the screen key is left."""
    assert client.put("/api/admin/settings/media", json={"screens_group": "Domain Users"}).status_code == 200
    r = client.post("/api/media/screens", json={"name": "Living room"})
    assert r.status_code == 201, r.text
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", client.cookies.get("mnemerynne_screen"))
    return tv


def test_retro_opens_on_a_tv_as_its_owner(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "Evil.gba", b"ROM-evil-bytes")
    share.sync(conn, settings)
    game = _games(client)["games"][0]
    tv = _tv(client)

    shelves = tv.get("/media/retro", headers=FRAME)
    assert shelves.status_code == 200, shelves.text
    assert 'class="topbar' not in shelves.text and "← Media" not in shelves.text   # the game layer's bar instead
    assert "messages.js" not in shelves.text and "notify.js" not in shelves.text  # none of the owner's messages
    assert "retro-tv.js" in shelves.text and "games/gamepad.js" in shelves.text
    assert 'class="tv tv-page"' in shelves.text                                   # framed: the TV's text size, no strip
    assert tv.get("/api/media/retro/games").json()["games"][0]["id"] == game["id"]   # the owner's games
    player = tv.get(f"/media/retro/play/{game['id']}", headers=FRAME)
    assert player.status_code == 200 and "retro-tv.js" in player.text and "games/gamepad.js" in player.text

    # The same pages for the signed-in owner still have the site's menu.
    assert "← Media" in client.get("/media/retro").text
    # Nobody at all: the sign-in page.
    nobody = TestClient(client.app)
    assert nobody.get("/media/retro", follow_redirects=False).status_code in (302, 303, 401)


def test_the_tvs_media_page_offers_retro_and_controllers(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    tv = _tv(client)
    page = tv.get("/media/screen")
    assert page.status_code == 200
    html = page.text
    assert 'id="mc-retro"' in html and 'href="/media/retro"' not in html            # Retro in the game layer, not a link
    assert 'id="mc-controllers"' in html and 'data-view="controllers"' in html
    assert html.index("games/gamepad.js") < html.index("media.js")                  # the controller helper first
    own = client.get("/media").text                                                  # a signed-in browser isn't a TV
    assert 'id="mc-controllers"' not in own and 'href="/media/retro"' in own
