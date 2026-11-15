"""Media → Retro (mnemerynne/media/retro.py, web/retro.py): the Games folder, the shelves, the ROMs and box art,
each person's saves, the player page, and the vendored emulator."""

import importlib.util
import io
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from mnemerynne import accounts, db, share, users
from mnemerynne.config import Settings
from mnemerynne.media import retro
from mnemerynne.services import registry
from mnemerynne.web.app import WEB_ROUTERS, Services, create_app

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "mnemerynne" / "web" / "static" / "vendor" / "emulatorjs"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64          # enough of a PNG for the server's check


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "storage" / "share"
    path.mkdir(parents=True)
    (path / share.MARKER).write_text("")
    return path


@pytest.fixture
def settings(tmp_path, root):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)


@pytest.fixture
def directory():
    return FakeDirectory({"jsmith": {"Domain Users"}, "boss": {"Domain Users", "Domain Admins"}})


@pytest.fixture
def checker(root):
    return FakeShareChecker(root)


@pytest.fixture
def conn(settings, directory, checker):
    share.configure(settings, directory, checker)
    connection = db.connect(settings.db_path)
    db.migrate(connection, settings.embed_dim)
    yield connection
    connection.close()


@pytest.fixture
def client(settings, conn, directory, checker):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=checker))) as c:
        yield c


def _login(client, conn, directory, name="boss", account="boss"):
    found = users.get_user(conn, name)                          # signing in again as someone made earlier
    uid = found["id"] if found else users.create_user(conn, name, "password123")
    accounts.link(conn, uid, account, directory)
    client.cookies.clear()
    assert client.post("/login", data={"username": name, "password": "password123"},
                       follow_redirects=False).status_code == 303
    return uid


def _turn_on(client, **extra):
    """Media on for everyone (both accounts are Domain Users), and Retro with it."""
    r = client.put("/api/admin/settings/media", json={"enabled": True, "media_group": "Domain Users",
                                                       "retro_enabled": True, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _put(path: Path, body: bytes) -> Path:
    """A file on the share, old enough for the share scan to take it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


def _games(client):
    r = client.get("/api/media/retro/games")
    assert r.status_code == 200, r.text
    return r.json()


def _by_title(client):
    return {g["title"]: g for g in _games(client)["games"]}


# ------------------------------------------------------------------- switch
def test_retro_is_off_until_an_admin_turns_it_on(client, conn, directory, root):
    _login(client, conn, directory)
    client.put("/api/admin/settings/media", json={"enabled": True, "media_group": "Domain Users"})
    assert client.get("/media/retro").status_code == 404
    assert client.get("/api/media/retro/games").status_code == 404
    assert 'href="/media/retro"' not in client.get("/media").text
    status = client.get("/api/admin/settings/media").json()
    assert status["settings"]["retro_enabled"] is False and status["retro"]["library"] is None
    assert not (root / "Games").exists()

    status = _turn_on(client)
    assert (root / "Games" / "GBA").is_dir()                       # the folder, a folder per console in it
    assert status["settings"]["retro_enabled"] is True and status["retro"]["library"]["share_path"] == "Games"
    assert [c["key"] for c in status["retro"]["consoles"]] == list(retro.CONSOLES)
    assert 'href="/media/retro"' in client.get("/media").text
    assert client.get("/media/retro").status_code == 200
    assert conn.execute("SELECT 1 FROM access_log WHERE action = 'settings.retro'").fetchone()

    assert client.put("/api/admin/settings/media", json={"retro_enabled": False}).status_code == 200
    assert client.get("/api/media/retro/games").status_code == 404
    assert (root / "Games" / "GBA").is_dir()                       # nothing is deleted
    # Media off takes Retro with it, whatever Retro's own switch says.
    _turn_on(client)
    client.put("/api/admin/settings/media", json={"enabled": False})
    assert client.get("/api/media/retro/games").status_code == 404


def test_the_games_folder_can_be_chosen_but_not_medias_or_signages(client, conn, directory, root, settings):
    (root / "Roms" / "gba").mkdir(parents=True)
    share.sync(conn, settings)
    _login(client, conn, directory)
    status = _turn_on(client, retro_folder="roms")
    assert status["retro"]["library"]["share_path"] == "Roms"       # the existing folder, in its own case
    assert not (root / "Games").exists()
    assert [p.name for p in (root / "Roms").iterdir()] == ["gba"]  # its gba folder counts as GBA: none made
    for taken in ("Media", "Signage", "media"):
        r = client.put("/api/admin/settings/media", json={"retro_folder": taken})
        assert r.status_code == 422, taken
    assert client.put("/api/admin/settings/media", json={"retro_folder": "a/b"}).status_code == 422
    assert client.get("/api/admin/settings/media").json()["retro"]["library"]["share_path"] == "Roms"


# ------------------------------------------------------------------ shelves
def test_the_shelves_list_the_roms_with_titles_and_box_art(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    gba = root / "Games" / "GBA"
    _put(gba / "Metroid_Fusion (USA) [!].gba", b"ROM-metroid")
    _put(gba / "Metroid_Fusion (USA) [!].png", PNG)                # its box art: the same name
    _put(gba / "Puzzles" / "Tetris Worlds (Europe).zip", b"PK\x03\x04 zipped")   # a folder inside a console's
    _put(gba / "readme.txt", b"not a game")
    _put(root / "Games" / "loose.gba", b"ROM-loose")               # outside every console's folder
    share.sync(conn, settings)

    data = _games(client)
    assert data["library"] is True and [c["key"] for c in data["consoles"]] == ["gba"]
    games = {g["title"]: g for g in data["games"]}
    assert sorted(games) == ["Metroid Fusion", "Tetris Worlds"]
    metroid = games["Metroid Fusion"]
    assert metroid["console"] == "gba" and metroid["name"] == "Metroid_Fusion (USA) [!].gba"
    assert metroid["cover"] and games["Tetris Worlds"]["cover"] is None
    assert metroid["played_at"] is None and metroid["has_save"] is False and metroid["can_continue"] is False
    cover = client.get(metroid["cover"])
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/png"
    assert "sandbox" in cover.headers["content-security-policy"]
    assert client.get("/api/admin/settings/media").json()["retro"]["games"] == 2


def test_titles_drop_no_intros_tags():
    assert retro.title_of("Pokemon - Emerald Version (USA, Europe).gba") == "Pokemon - Emerald Version"
    assert retro.title_of("Golden_Sun (Rev 1) [!].gba") == "Golden Sun"
    assert retro.title_of("(Homebrew).gba") == "(Homebrew)"           # nothing left: the file's name


def test_the_shelves_follow_share_permissions(client, conn, directory, root, settings, checker):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "Open.gba", b"ROM-open")
    _put(root / "Games" / "GBA" / "Grown-ups" / "Closed.gba", b"ROM-closed")
    share.sync(conn, settings)
    closed = _by_title(client)["Closed"]["id"]

    checker.rules = {"Games/GBA/Grown-ups": (False, False)}
    share.forget_rights()
    _login(client, conn, directory, "jane", "jsmith")
    assert list(_by_title(client)) == ["Open"]
    assert client.get(f"/media/retro/play/{closed}").status_code == 404
    assert client.get(f"/api/media/retro/saves/{closed}").status_code == 404
    assert client.put(f"/api/media/retro/saves/{closed}/sram", content=b"x").status_code == 404

    checker.rules = {"Games": (False, False)}
    share.forget_rights()
    data = _games(client)
    assert data["library"] is False and data["games"] == []


# ------------------------------------------------------------------- player
def test_the_player_page_and_the_rom(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "Evil.gba", b"ROM-evil-bytes")
    share.sync(conn, settings)
    game = _games(client)["games"][0]

    page = client.get(f"/media/retro/play/{game['id']}")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "connect-src 'self' blob: data:" in csp and "'wasm-unsafe-eval'" in csp and "frame-ancestors 'self'" in csp
    player = _player_data(page.text)
    assert player["title"] == "Evil" and player["core"] == "mgba" and player["console"] == "gba"
    assert player["slots"] == ["1", "2", "3", "4"] and player["saves"] == []

    sha = conn.execute("SELECT blob_sha256 FROM files WHERE id = ?", (game["id"],)).fetchone()[0]
    assert player["rom"] == f"/media/retro/rom/{game['id']}/{sha[:16]}.gba"   # named by its content
    rom = client.get(player["rom"])
    assert rom.status_code == 200 and rom.content == b"ROM-evil-bytes"
    assert rom.headers["content-type"] == "application/octet-stream"
    assert client.get(f"/media/retro/rom/{game['id']}/{'0' * 16}.gba").status_code == 404   # another name
    assert client.get(player["rom"], headers={"Range": "bytes=0-2"}).content == b"ROM"
    head = client.head(player["rom"])                               # the emulator asks the size first
    assert head.status_code == 200 and head.headers["content-length"] == str(len(b"ROM-evil-bytes"))

    # A file name a Linux share allows but Windows doesn't (so straight into the row): no "<" of it reaches the page
    # as it is, so neither "<script>" nor "<!--" changes how the browser reads the script block.
    conn.execute("UPDATE files SET display_name = 'Evil <script><!--x.gba' WHERE id = ?", (game["id"],))
    conn.commit()
    html = client.get(f"/media/retro/play/{game['id']}").text
    block = html.split('<script id="rp-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert "<" not in block and json.loads(block)["title"] == "Evil <script><!--x"


def _player_data(html: str) -> dict:
    """The game's JSON in the player page, up to the first </script> as a browser reads it."""
    return json.loads(html.split('<script id="rp-data" type="application/json">', 1)[1].split("</script>", 1)[0])


def test_not_a_rom_is_not_a_game(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "notes.txt", b"hello")
    _put(root / "Media" / "Movies" / "Heat.gba", b"ROM-in-media")   # a .gba outside the Games folder
    share.sync(conn, settings)
    for name in ("notes.txt", "Heat.gba"):
        file_id = conn.execute("SELECT id FROM files WHERE display_name = ?", (name,)).fetchone()[0]
        assert client.get(f"/media/retro/play/{file_id}").status_code == 404
        assert client.post(f"/api/media/retro/played/{file_id}").status_code == 404
        assert client.get(f"/media/retro/cover/{file_id}").status_code == 404


# -------------------------------------------------------------------- saves
def test_saves_are_each_persons_own_and_follow_the_game_not_the_file(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "Zelda (USA).gba", b"ROM-zelda")
    share.sync(conn, settings)
    zelda = _by_title(client)["Zelda"]["id"]
    base = f"/api/media/retro/saves/{zelda}/"

    assert client.get(base + "sram").status_code == 404
    assert client.put(base + "sram", content=b"\x01\x02battery").status_code == 204
    assert client.put(base + "auto", content=b"state-auto").status_code == 204
    assert client.put(base + "auto/shot", content=PNG).status_code == 204
    assert client.put(base + "2", content=b"state-two").status_code == 204
    assert client.get(base + "sram").content == b"\x01\x02battery"
    assert client.get(base + "auto/shot").content == PNG
    slots = {s["slot"]: s for s in client.get(base[:-1]).json()["saves"]}
    assert set(slots) == {"sram", "auto", "2"} and slots["auto"]["has_shot"] and not slots["2"]["has_shot"]
    assert slots["sram"]["size"] == 9

    assert client.post(f"/api/media/retro/played/{zelda}").status_code == 204
    shelf = _by_title(client)["Zelda"]
    assert shelf["has_save"] and shelf["can_continue"] and shelf["played_at"]

    # A new state drops the old screenshot (it showed another moment).
    assert client.put(base + "auto", content=b"state-auto-2").status_code == 204
    assert client.get(base + "auto/shot").status_code == 404

    # A copy of the same ROM elsewhere, or the ROM renamed: the same game, the same saves.
    _put(root / "Games" / "GBA" / "Backup" / "zelda copy.gba", b"ROM-zelda")
    (root / "Games" / "GBA" / "Zelda (USA).gba").rename(root / "Games" / "GBA" / "The Legend of Zelda.gba")
    share.sync(conn, settings)
    games = _by_title(client)
    assert games["zelda copy"]["has_save"] and games["The Legend of Zelda"]["can_continue"]
    copy = games["zelda copy"]["id"]
    assert client.get(f"/api/media/retro/saves/{copy}/sram").content == b"\x01\x02battery"

    # Someone else's saves are their own.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get(base + "sram").status_code == 404 and client.get(base[:-1]).json()["saves"] == []
    assert not _by_title(client)["The Legend of Zelda"]["has_save"]

    _login(client, conn, directory)
    assert client.delete(base + "2").status_code == 204
    assert client.delete(base + "2").status_code == 404
    assert {s["slot"] for s in client.get(base[:-1]).json()["saves"]} == {"sram", "auto"}


def test_saves_are_checked(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _put(root / "Games" / "GBA" / "Game.gba", b"ROM")
    share.sync(conn, settings)
    base = f"/api/media/retro/saves/{_games(client)['games'][0]['id']}/"
    assert client.put(base + "9", content=b"x").status_code == 422             # no such slot
    assert client.put(base + "sram", content=b"").status_code == 422           # empty
    assert client.put(base + "sram", content=b"x" * (retro.MAX_SRAM + 1)).status_code == 413
    assert client.put(base + "1/shot", content=PNG).status_code == 404         # no state there yet
    client.put(base + "1", content=b"state")
    assert client.put(base + "1/shot", content=b"GIF89a").status_code == 422   # not a PNG
    assert client.put(base + "sram/shot", content=PNG).status_code == 422      # only states have one


def test_a_persons_saves_go_with_them(client, conn, directory, root, settings):
    media = db.FEATURE_DBS["media"]
    assert ("retro_saves", "user_id") in media.user_columns and ("retro_plays", "user_id") in media.user_columns


# ------------------------------------------------------------------ wiring
def test_retro_runs_wherever_media_runs():
    assert "mnemerynne.web.retro:router" in WEB_ROUTERS
    assert "mnemerynne.web.retro:router" in registry.SERVICES["media"].routers
    assert "mnemerynne.web.retro:router" in registry.web_drops(["media"])        # behind /media/ and /api/media/


def test_the_vendored_emulator_matches_its_pins():
    spec = importlib.util.spec_from_file_location("fetch_emulatorjs", REPO / "python" / "fetch_emulatorjs.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    out = io.StringIO()
    from contextlib import redirect_stdout
    with redirect_stdout(out):
        assert fetch.check() == 0, out.getvalue()
    for console in retro.CONSOLES.values():                       # every console has its core, both builds
        assert console.core in fetch.CORES
        for name in (f"{console.core}-wasm.data", f"{console.core}-legacy-wasm.data", f"reports/{console.core}.json"):
            assert (VENDOR / "cores" / name).is_file(), name
    player = (REPO / "mnemerynne" / "web" / "static" / "retro-player.js").read_text(encoding="utf-8")
    for script in json.loads(player.split("const SCRIPTS = ", 1)[1].split(";", 1)[0].replace("\n", " ")):
        assert (VENDOR / "src" / script).is_file(), script
