"""media/popularity.py: Retro's games ranked by how many people played them on RetroAchievements, through a
pretend RetroAchievements (httpx.MockTransport). ROMs are matched by RetroAchievements' own hash rules (the NES
without its iNES header, the SNES without a copier header), else by title (a PS1 disc); player counts are asked
once and kept; the games list carries them; a wrong key stops quietly."""

import hashlib
import importlib.util
import json
import time
from pathlib import Path

import httpx

from conftest import FeatureConn
from mnemerynne import share
from mnemerynne.media import popularity
from test_retro import _by_title, _login, _put, _turn_on, client, conn, checker, directory, root, settings  # noqa: F401

_spec = importlib.util.spec_from_file_location("make_retro_test_rom",
                                               Path(__file__).parent.parent / "python" / "make_retro_test_rom.py")
roms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(roms)


def test_hashes_follow_retroachievements_rules():
    nes = roms.nes()
    assert nes[:4] == b"NES\x1a"
    assert popularity.ra_hash(nes, "nes") == hashlib.md5(nes[16:]).hexdigest()          # without the iNES header
    snes = roms.snes()
    assert popularity.ra_hash(b"\0" * 512 + snes, "snes") == hashlib.md5(snes).hexdigest()   # without a copier header
    assert popularity.ra_hash(snes, "snes") == hashlib.md5(snes).hexdigest()
    gba = roms.gba()
    assert popularity.ra_hash(gba, "gba") == hashlib.md5(gba).hexdigest()               # the whole file


def test_titles_are_matched_loosely_but_exactly():
    key = popularity.title_key
    assert key("Legend of Zelda, The - A Link to the Past (USA)") == key("Legend of Zelda, The: A Link to the Past")
    assert key("Sonic & Knuckles") == key("Sonic and Knuckles")
    assert key("The Lion King [!]") == "lion king"
    assert key("Metroid II") != key("Metroid")                                        # no guessing
    assert popularity._titles("Pokemon Red Version | Pokemon Blue Version") == ["pokemon red version",
                                                                                "pokemon blue version"]


class FakeRA:
    """RetroAchievements' Web API, as far as popularity asks it."""

    def __init__(self, key="good-key"):
        self.key, self.calls = key, []
        self.systems = [{"ID": 7, "Name": "NES/Famicom"}, {"ID": 5, "Name": "Game Boy Advance"},
                        {"ID": 12, "Name": "PlayStation"}, {"ID": 21, "Name": "PlayStation 2"},
                        {"ID": 3, "Name": "SNES/Super Famicom"}, {"ID": 1, "Name": "Genesis/Mega Drive"},
                        {"ID": 4, "Name": "Game Boy"}, {"ID": 6, "Name": "Game Boy Color"}]
        self.lists = {
            7: [{"ID": 70, "Title": "Lumi Quest", "Hashes": [hashlib.md5(roms.nes()[16:]).hexdigest()]}],
            5: [{"ID": 50, "Title": "Lumi Advance", "Hashes": [hashlib.md5(roms.gba()).hexdigest()]},
                {"ID": 51, "Title": "Nobody Has It", "Hashes": ["0" * 32]}],
            12: [{"ID": 120, "Title": "~Hack~ Lumi Disc", "Hashes": []},
                 {"ID": 121, "Title": "Lumi Disc", "Hashes": []}],
        }
        self.players = {70: 1234, 50: 98765, 121: 4321}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.params.get("y") != self.key:
            return httpx.Response(401, json={"message": "Unauthenticated."})
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "API_GetConsoleIDs.php":
            return httpx.Response(200, json=self.systems)
        if name == "API_GetGameList.php":
            assert request.url.params.get("h") == "1"
            return httpx.Response(200, json=self.lists.get(int(request.url.params["i"]), []))
        if name == "API_GetGameExtended.php":
            gid = int(request.url.params["i"])
            return httpx.Response(200, json={"ID": gid, "NumDistinctPlayers": self.players.get(gid, 0)})
        return httpx.Response(404)


def _setup(client, conn, directory, root, settings, key="good-key"):
    _login(client, conn, directory)
    _turn_on(client)
    assert client.put("/api/admin/settings/media", json={"retroachievements_key": key}).status_code == 200
    games = root / "Games"
    _put(games / "NES" / "Quest.nes", roms.nes())                    # by hash (the iNES header left out)
    _put(games / "GBA" / "Odd Name (USA).gba", roms.gba())          # by hash, whatever its name
    _put(games / "PS1" / "Lumi Disc (USA).exe", roms.psx())         # a PS1 game: by title
    _put(games / "GBA" / "Homebrew.gba", b"\0" * 4096)              # unknown to RetroAchievements
    share.sync(conn, settings)
    return FeatureConn(conn, "media", "retro_ra_roms")


def test_the_share_s_games_get_their_player_counts(client, conn, directory, root, settings):
    media_conn = _setup(client, conn, directory, root, settings)
    ra = FakeRA()
    with httpx.Client(transport=httpx.MockTransport(ra)) as fake:
        done = popularity.refresh(media_conn, settings, time.monotonic() + 30, client=fake, pause=0)
    assert done["lists"] == 7 and done["roms"] == 4 and done["players"] == 3
    found = {r["console"] + ":" + str(r["game_id"]): r["matched_by"]
             for r in media_conn.execute("SELECT console, game_id, matched_by FROM retro_ra_roms")}
    assert found == {"nes:70": "hash", "gba:50": "hash", "psx:121": "title", "gba:None": None}   # not the ~Hack~

    titles = _by_title(client)
    assert titles["Quest"]["players"] == 1234 and titles["Odd Name"]["players"] == 98765
    assert titles["Lumi Disc"]["players"] == 4321 and titles["Homebrew"]["players"] is None

    status = client.get("/api/admin/settings/media").json()
    assert status["retroachievements_key_set"] is True
    assert status["retro"]["popularity"]["found"] == 3 and status["retro"]["popularity"]["ranked"] == 3
    assert "good-key" not in json.dumps(status)                      # the key itself never comes back

    # The next pass asks nothing: the lists are a day old, the counts too, every ROM is checked.
    ra.calls.clear()
    with httpx.Client(transport=httpx.MockTransport(ra)) as fake:
        assert popularity.refresh(media_conn, settings, time.monotonic() + 30, client=fake, pause=0) == \
            {"lists": 0, "roms": 0, "players": 0}
    assert ra.calls == []


def test_a_wrong_key_or_no_key_asks_nothing_and_breaks_nothing(client, conn, directory, root, settings, caplog):
    media_conn = _setup(client, conn, directory, root, settings, key="wrong-key")
    ra = FakeRA()
    with httpx.Client(transport=httpx.MockTransport(ra)) as fake:
        assert popularity.refresh(media_conn, settings, time.monotonic() + 30, client=fake, pause=0) == \
            {"lists": 0, "roms": 0, "players": 0}
    assert ra.calls == ["API_GetConsoleIDs.php"] and "didn't accept the Web API key" in caplog.text
    assert all(g["players"] is None for g in _by_title(client).values())   # the pages fall back to A-Z

    assert client.put("/api/admin/settings/media", json={"retroachievements_key": ""}).status_code == 200
    ra.calls.clear()
    with httpx.Client(transport=httpx.MockTransport(ra)) as fake:
        popularity.refresh(media_conn, settings, time.monotonic() + 30, client=fake, pause=0)
    assert ra.calls == []                                              # no key: nothing asked
