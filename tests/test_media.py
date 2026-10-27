import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from mnemerynne import accounts, db, media, share, users
from mnemerynne.config import Settings
from mnemerynne.media import catalog, live, probe, scrape, transcode
from mnemerynne.web.app import Services, create_app

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe aren't installed")

# What https://sadgirlsclub.wtf/rr/healthz answered on 2026-10-26.
RELAY = {"ok": True, "port": 8000, "stations": [
    {"name": "SadGirlsClub", "path": "/sgc", "aliases": ["/sgc", "/sgc.mp3"], "clients": 0, "running": False},
    {"name": "TCPMM", "path": "/tcp", "aliases": ["/tcp", "/tcp.mp3"], "clients": 1, "running": True}],
    "routes": [{"name": "SadGirlsClub", "path": "/sgc", "inputUrl": "http://10.1.1.21/stream/hls/live.m3u8",
                "clients": 0, "running": False}]}


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


@pytest.fixture(autouse=True)
def _no_network():
    live._cache.clear()
    yield
    live.TRANSPORT = None
    scrape.TRANSPORT = None
    live._cache.clear()


def _login(client, conn, directory, name="boss", account="boss"):
    uid = users.create_user(conn, name, "password123")
    accounts.link(conn, uid, account, directory)
    client.cookies.clear()
    assert client.post("/login", data={"username": name, "password": "password123"},
                       follow_redirects=False).status_code == 303
    return uid


def _aged(path, age=60):
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=60)


def _jpeg(color=(200, 40, 120), size=(64, 96), exif_date=None) -> bytes:
    from PIL import Image
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    if exif_date:
        exif = Image.Exif()
        exif[0x010F], exif[0x0110] = "Canon", "Canon EOS R6"
        exif.get_ifd(0x8769)[0x9003] = exif_date
        img.save(out, "JPEG", exif=exif)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


def _turn_on(client, group="Domain Users"):
    """Media on, for the Media group (everyone in these tests: both accounts are Domain Users)."""
    r = client.put("/api/admin/settings/media", json={"enabled": True, "media_group": group})
    assert r.status_code == 200, r.text
    return r.json()


def _screens_for(client, group="Domain Users"):
    r = client.put("/api/admin/settings/media", json={"screens_group": group})
    assert r.status_code == 200, r.text


def _scan(conn, settings):
    share.sync(conn, settings)
    return catalog.run_pass(conn, settings, budget_s=120)


# ------------------------------------------------------------------ switch
def test_media_is_off_until_an_admin_turns_it_on(client, conn, directory, root):
    _login(client, conn, directory)
    assert client.get("/media").status_code == 404
    assert client.get("/api/media/home").status_code == 404
    assert 'href="/media"' not in client.get("/files").text
    status = client.get("/api/admin/settings/media").json()
    assert status["enabled"] is False and status["library"] is None

    status = _turn_on(client)
    assert status["enabled"] is True and status["library"]["share_path"] == "Media"
    for folder in ("Music", "Movies", "Pictures"):
        assert (root / "Media" / folder).is_dir()
    assert client.get("/media").status_code == 200
    assert 'href="/media"' in client.get("/files").text
    assert conn.execute("SELECT 1 FROM access_log WHERE action = 'settings.media'").fetchone()

    assert client.put("/api/admin/settings/media", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/media/home").status_code == 404
    assert (root / "Media" / "Movies").is_dir()                    # nothing is deleted


def test_an_existing_media_folder_is_adopted(client, conn, directory, root, settings):
    (root / "media" / "movies").mkdir(parents=True)
    share.sync(conn, settings)
    _login(client, conn, directory)
    status = _turn_on(client)
    assert status["library"]["share_path"] == "media"
    assert (root / "media" / "movies").is_dir() and (root / "media" / "Music").is_dir()
    assert not (root / "Media").exists() or (root / "Media").samefile(root / "media")


def test_the_media_folder_can_be_chosen(client, conn, directory, root, settings):
    (root / "Plex" / "Movies").mkdir(parents=True)
    film = root / "Plex" / "Movies" / "Heat (1995).mkv"
    film.write_bytes(b"\x1a\x45\xdf\xa3 not a real film")
    _aged(film)
    share.sync(conn, settings)
    _login(client, conn, directory)
    status = client.get("/api/admin/settings/media").json()
    assert status["settings"]["folder"] == "Media" and status["folders"] == ["Plex"]

    # Picked before turning it on: the existing folder is used, and nothing called Media is made.
    status = client.put("/api/admin/settings/media", json={"folder": "plex", "enabled": True,
                                                            "media_group": "Domain Users"}).json()
    assert status["library"]["share_path"] == "Plex" and status["enabled"] is True
    assert (root / "Plex" / "Music").is_dir() and (root / "Plex" / "Pictures").is_dir()
    assert not (root / "Media").exists()
    assert 'data-folder="Plex"' in client.get("/media").text
    _scan(conn, settings)
    assert [m["title"] for m in client.get("/api/media/movies").json()["movies"]] == ["Heat"]
    assert client.get("/api/admin/settings/media").json()["catalog"]["counts"]["movies"] == 1

    # A new folder while it's on: made at once, with its three folders; the old catalog is kept.
    status = client.put("/api/admin/settings/media", json={"folder": "Videos"}).json()
    assert status["library"]["share_path"] == "Videos" and (root / "Videos" / "Movies").is_dir()
    assert client.get("/api/media/movies").json()["movies"] == []
    assert status["catalog"]["counts"]["movies"] == 0
    assert conn.execute("SELECT 1 FROM access_log WHERE action = 'settings.media_folder'").fetchone()
    client.put("/api/admin/settings/media", json={"folder": "Plex"})
    assert [m["title"] for m in client.get("/api/media/movies").json()["movies"]] == ["Heat"]

    for bad in ("Users", "a/b", "what?", ""):
        assert client.put("/api/admin/settings/media", json={"folder": bad}).status_code == 422, bad
    assert client.get("/api/admin/settings/media").json()["library"]["share_path"] == "Plex"


def test_ordinary_users_cant_change_media_settings(client, conn, directory):
    _login(client, conn, directory, "jane", "jsmith")
    assert client.put("/api/admin/settings/media", json={"enabled": True}).status_code == 403


def test_media_needs_the_share(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                        embed_dim=4, admin_users="root")
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    users.create_user(conn, "root", "password123")
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"username": "root", "password": "password123"})
        r = c.put("/api/admin/settings/media", json={"enabled": True})
    assert r.status_code == 422 and "share" in r.json()["detail"]
    conn.close()


# ----------------------------------------------------------------- catalog
def test_pictures_are_catalogued_with_exif_and_thumbnails(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    album = root / "Media" / "Pictures" / "Japan 2026"
    album.mkdir()
    (album / "fuji.jpg").write_bytes(_jpeg(exif_date="2026:04:02 06:15:00"))
    _aged(album / "fuji.jpg")
    (root / "Media" / "stray.jpg").write_bytes(_jpeg())          # not in a section: ignored
    _aged(root / "Media" / "stray.jpg")
    stats = _scan(conn, settings)
    assert stats.read == 1

    root_view = client.get("/api/media/pictures").json()
    assert [f["name"] for f in root_view["folders"]] == ["Japan 2026"] and root_view["folders"][0]["art"]
    inside = client.get(f"/api/media/pictures?folder={root_view['folders'][0]['id']}").json()
    pic = inside["items"][0]
    assert pic["taken_at"] == "2026-04-02T06:15:00" and pic["camera"] == "Canon EOS R6"
    assert (pic["width"], pic["height"]) == (64, 96)
    assert client.get(pic["art"]).headers["content-type"] == "image/jpeg"
    assert client.get(pic["stream"]).content == (album / "fuji.jpg").read_bytes()
    assert not catalog.run_pass(conn, settings).read             # nothing changed: nothing read

    thumbs = list((settings.media_dir / "art").rglob("*.jpg"))
    (album / "fuji.jpg").unlink()
    _scan(conn, settings)
    assert conn.execute("SELECT count(*) FROM media_items").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM media_artwork").fetchone()[0] == 0
    assert thumbs and not any(t.exists() for t in thumbs)


@needs_ffmpeg
def test_music_and_movies_are_catalogued(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    disc = root / "Media" / "Music" / "Daft Punk" / "Discovery"
    disc.mkdir(parents=True)
    for n, title in ((1, "One More Time"), (2, "Aerodynamic")):
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libmp3lame",
                "-metadata", f"title={title}", "-metadata", "artist=Daft Punk", "-metadata", "album=Discovery",
                "-metadata", f"track={n}", "-metadata", "date=2001", str(disc / f"0{n} {title}.mp3"))
    (disc / "cover.jpg").write_bytes(_jpeg((10, 10, 200), (300, 300)))
    films = root / "Media" / "Movies"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-t", "2", "-c:v", "mpeg4",
            str(films / "Blade.Runner.1982.1080p.BluRay.avi"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-f", "lavfi", "-i", "sine=duration=2", "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(films / "Alien (1979).mp4"))
    (films / "Alien (1979)-poster.jpg").write_bytes(_jpeg())      # its poster, not a picture
    for p in list(disc.iterdir()) + list(films.iterdir()):
        _aged(p)
    stats = _scan(conn, settings)
    assert stats.read == 4 and stats.failed == 0

    albums = client.get("/api/media/music/albums").json()["albums"]
    assert [(a["album"], a["artist"], a["tracks"], a["year"]) for a in albums] == [("Discovery", "Daft Punk", 2, 2001)]
    album = client.get("/api/media/music/album", params={"key": albums[0]["key"]}).json()
    assert [t["title"] for t in album["tracks"]] == ["One More Time", "Aerodynamic"]
    assert album["art"] and client.get(album["art"]).status_code == 200
    assert client.get("/api/media/music/artists").json()["artists"][0]["name"] == "Daft Punk"

    movies = {m["title"]: m for m in client.get("/api/media/movies").json()["movies"]}
    assert set(movies) == {"Alien", "Blade Runner"}
    assert movies["Blade Runner"]["year"] == 1982 and movies["Blade Runner"]["playback"] == "transcode"
    assert movies["Alien"]["playback"] == "direct" and movies["Alien"]["art"]
    art = conn.execute("SELECT a.source FROM media_items mi JOIN media_artwork a ON a.id = mi.artwork_id "
                       "WHERE mi.title = 'Blade Runner'").fetchone()
    assert art["source"] == "frame"                                # no poster beside it: a frame of the film
    assert client.get("/api/media/pictures").json()["items"] == []
    assert [m["title"] for m in client.get("/api/media/movies", params={"q": "ali"}).json()["movies"]] == ["Alien"]


def test_unreadable_files_are_reported_and_tried_again_when_they_change(client, conn, directory, root, settings,
                                                                         monkeypatch):
    _login(client, conn, directory)
    _turn_on(client)
    bad = root / "Media" / "Pictures" / "broken.jpg"
    bad.write_bytes(b"not really a jpeg")
    _aged(bad)
    calls = []
    real = catalog._read

    def boom(*args):
        calls.append(1)
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(catalog, "_read", boom)
    assert _scan(conn, settings).failed == 1
    status = client.get("/api/admin/settings/media").json()["catalog"]
    assert status["errors"][0]["name"] == "broken.jpg" and "exploded" in status["errors"][0]["error"]
    assert catalog.run_pass(conn, settings).failed == 0 and len(calls) == 1   # not again until it changes
    monkeypatch.setattr(catalog, "_read", real)
    bad.write_bytes(_jpeg())
    _aged(bad, age=30)
    assert _scan(conn, settings).read == 1
    assert client.get("/api/admin/settings/media").json()["catalog"]["errors"] == []


# ------------------------------------------------------------------ access
def test_media_follows_share_permissions(client, conn, directory, root, settings, checker):
    _login(client, conn, directory)
    _turn_on(client)
    (root / "Media" / "Movies" / "Alien (1979).mkv").write_bytes(b"\x1a\x45\xdf\xa3 not a real film")
    (root / "Media" / "Pictures" / "cat.jpg").write_bytes(_jpeg())
    for p in (root / "Media" / "Movies" / "Alien (1979).mkv", root / "Media" / "Pictures" / "cat.jpg"):
        _aged(p)
    _scan(conn, settings)
    film = conn.execute("SELECT file_id, artwork_id FROM media_items WHERE section = 'movies'").fetchone()
    pic = conn.execute("SELECT file_id, artwork_id FROM media_items WHERE section = 'pictures'").fetchone()

    checker.rules = {"Media/Movies": (False, False)}
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get("/api/media/movies").json()["movies"] == []
    assert client.get(f"/api/media/items/{film['file_id']}").status_code == 404
    assert client.get(f"/media/files/{film['file_id']}/stream").status_code == 404
    assert client.post(f"/api/media/play/{film['file_id']}", json={}).status_code == 404
    assert client.get(f"/media/files/{pic['file_id']}/stream").status_code == 200
    assert client.get(f"/media/art/{pic['artwork_id']}").status_code == 200
    home = client.get("/api/media/home").json()
    assert home["movies"] == [] and len(home["pictures"]) == 1

    checker.rules = {"Media": (False, False)}
    assert client.get("/api/media/home").json()["library"] is False
    assert client.get(f"/media/art/{pic['artwork_id']}").status_code == 404


def test_streams_answer_range_requests(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    clip = root / "Media" / "Pictures" / "clip.jpg"
    body = _jpeg(size=(400, 400))
    clip.write_bytes(body)
    _aged(clip)
    _scan(conn, settings)
    file_id = conn.execute("SELECT file_id FROM media_items").fetchone()[0]
    r = client.get(f"/media/files/{file_id}/stream", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == body[10:20]
    assert r.headers["content-disposition"].startswith("inline")


# ---------------------------------------------------------------- playback
@pytest.mark.parametrize("kind, container, video, audio, caps, expected", [
    ("video", "mp4", "h264", "aac", None, "direct"),
    ("video", "webm", "vp9", "opus", None, "direct"),
    ("video", "mkv", "h264", "aac", None, "remux"),
    ("video", "mkv", "h264", "ac3", None, "audio"),
    ("video", "mkv", "h264", "ac3", {"ac3": True}, "remux"),
    ("video", "mkv", "hevc", "aac", None, "transcode"),
    ("video", "mp4", "hevc", "aac", {"hevc": True}, "direct"),
    ("video", "avi", "mpeg4", "mp3", None, "transcode"),
    ("audio", "flac", None, "flac", None, "direct"),
    ("audio", "m4a", None, "alac", None, "transcode"),
    ("audio", "wma", None, "wmav2", None, "transcode"),
    ("image", "jpg", None, None, None, "direct"),
])
def test_playback_decisions(kind, container, video, audio, caps, expected):
    assert probe.decide(kind, container, video, audio, caps) == expected


@pytest.mark.parametrize("stem, expected", [
    ("Blade.Runner.1982.1080p.BluRay.x264", ("Blade Runner", 1982)),
    ("2001 A Space Odyssey (1968)", ("2001 A Space Odyssey", 1968)),
    ("1917 (2019)", ("1917", 2019)),
    ("Spirited Away [2001] 720p", ("Spirited Away", 2001)),
    ("Alien", ("Alien", None)),
    ("Heat.1080p.WEB-DL", ("Heat", None)),
])
def test_film_names(stem, expected):
    assert probe.movie_name(stem) == expected


@needs_ffmpeg
def test_films_the_browser_cant_play_are_transcoded_into_hls(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    client.put("/api/admin/settings/media", json={"hwaccel": "software", "max_transcodes": 1})
    films = root / "Media" / "Movies"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-f", "lavfi", "-i", "sine=duration=6", "-t", "6",
            "-c:v", "mpeg4", "-c:a", "libmp3lame", str(films / "Old (1999).avi"))
    _aged(films / "Old (1999).avi")
    _scan(conn, settings)
    file_id = conn.execute("SELECT file_id FROM media_items").fetchone()[0]

    r = client.post(f"/api/media/play/{file_id}", json={"start": 2})
    assert r.status_code == 200, r.text
    play = r.json()
    assert play["mode"] == "transcode" and play["backend"] == "software" and play["start"] == 2
    playlist = client.get(play["url"])
    assert playlist.status_code == 200 and "#EXTINF" in playlist.text
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert '#EXT-X-MAP:URI="init.mp4"' in playlist.text
    first = next(line for line in playlist.text.splitlines() if line.endswith(".m4s"))
    assert client.get(play["url"].replace("index.m3u8", first)).status_code == 200
    init = client.get(play["url"].replace("index.m3u8", "init.mp4"))
    assert init.status_code == 200 and init.headers["content-type"] == "video/mp4"
    assert not (Path.cwd() / "init.mp4").exists()
    assert client.get(play["url"].replace("index.m3u8", "../../etc/passwd")).status_code == 404

    # Another person can't fetch someone else's session, or stop it.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get(play["url"]).status_code == 404
    client.delete(f"/api/media/sessions/{play['session']}")

    client.cookies.clear()
    client.post("/login", data={"username": "boss", "password": "password123"})
    assert client.get(play["url"]).status_code == 200
    assert client.delete(f"/api/media/sessions/{play['session']}").status_code == 200
    assert client.get(play["url"]).status_code == 404

    client.put("/api/admin/settings/media", json={"max_transcodes": 0})
    off = client.post(f"/api/media/play/{file_id}", json={})
    assert off.status_code == 503 and "turned off" in off.json()["detail"]


def test_transcoding_slots_are_limited(settings, tmp_path):
    class Running:
        def poll(self):
            return None

    manager = transcode.Manager(settings)
    manager._sessions["a"] = transcode.Session("a", "user:1:video", 1, 5, None, "transcode", 0.0, tmp_path, Running(),
                                               "software", time.monotonic())
    with pytest.raises(transcode.TranscodeError, match="busy"):
        manager.start_file(user_id=2, slot="video", file_id=6, path=tmp_path / "x.avi", kind="video",
                           video_codec="mpeg4", audio_codec="mp3", mode="transcode", start=0, hwaccel="software",
                           max_transcodes=1)
    manager._sessions.clear()


def test_transcoding_commands_use_the_gpu_that_was_chosen(settings, tmp_path):
    manager = transcode.Manager(settings)
    manager._vaapi = "/dev/dri/renderD129"
    cmd = manager.file_command(tmp_path / "x.mkv", "video", "hevc", "dts", "transcode", 30, "vaapi")
    assert cmd[cmd.index("-hwaccel") + 1] == "vaapi" and "/dev/dri/renderD129" in cmd
    assert "h264_vaapi" in cmd and cmd[cmd.index("-ss") + 1] == "30.000"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    cmd = manager.file_command(tmp_path / "x.avi", "video", "mpeg4", "mp3", "transcode", 0, "vaapi")
    assert "-hwaccel" not in cmd and "hwupload" in cmd[cmd.index("-vf") + 1]   # decoded on the CPU, then uploaded
    cmd = manager.file_command(tmp_path / "x.mkv", "video", "h264", "aac", "remux", 0, None)
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"
    cmd = manager.file_command(tmp_path / "x.mkv", "video", "hevc", "aac", "transcode", 0, "nvenc")
    assert "h264_nvenc" in cmd and cmd[cmd.index("-hwaccel") + 1] == "cuda"


# ---------------------------------------------------------- progress, home
def test_resume_positions_feed_continue_watching(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    (root / "Media" / "Movies" / "Heat (1995).mkv").write_bytes(b"\x1a\x45\xdf\xa3 not a real film")
    _aged(root / "Media" / "Movies" / "Heat (1995).mkv")
    _scan(conn, settings)
    file_id = conn.execute("SELECT file_id FROM media_items").fetchone()[0]
    assert client.put(f"/api/media/progress/{file_id}", json={"position": 1234.5, "duration": 6000}).status_code == 200
    home = client.get("/api/media/home").json()
    assert home["continue"][0]["title"] == "Heat" and home["continue"][0]["progress"]["position"] == 1234.5
    client.put(f"/api/media/progress/{file_id}", json={"position": 5990, "duration": 6000})
    assert client.get("/api/media/home").json()["continue"] == []   # finished


# ---------------------------------------------------------------- live, radio
def _relay_transport(doc=RELAY, status_code=200):
    def handler(request):
        assert request.url.path == "/rr/healthz"
        return httpx.Response(status_code, json=doc)
    return httpx.MockTransport(handler)


def test_the_relay_station_list_is_read_like_the_firmware_reads_it():
    stations = live.parse_directory("https://sadgirlsclub.wtf/rr/healthz", RELAY)
    assert [(s["name"], s["url"], s["running"], s["listeners"]) for s in stations] == [
        ("SadGirlsClub", "https://sadgirlsclub.wtf/rr/sgc", False, 0),
        ("TCPMM", "https://sadgirlsclub.wtf/rr/tcp", True, 1)]
    routes_only = live.parse_directory("http://pi.local:8000/healthz", {"routes": RELAY["routes"]})
    assert routes_only[0]["url"] == "http://pi.local:8000/sgc"
    assert live.parse_directory("https://x/rr/healthz", {"stations": [{"name": "Abs", "url": "https://y/a.mp3"}]})[0][
        "url"] == "https://y/a.mp3"


def test_live_sources_and_radio(client, conn, directory):
    live.TRANSPORT = _relay_transport()
    _login(client, conn, directory)
    _turn_on(client)
    bad = client.post("/api/admin/media/sources", json={"kind": "rtmp", "name": "Cam", "url": "http://x/live"})
    assert bad.status_code == 422
    client.post("/api/admin/media/sources", json={"kind": "hls", "name": "SGC TV",
                                                  "url": "http://10.1.1.21/stream/hls/live.m3u8"})
    client.post("/api/admin/media/sources", json={"kind": "rtmp", "name": "Desk cam", "url": "rtmp://10.1.1.21/live/cam"})
    made = client.post("/api/admin/media/sources", json={"kind": "radio", "name": "Telehack",
                                                         "url": "http://telehack.com/radio"}).json()
    data = client.get("/api/media/live").json()
    assert [s["name"] for s in data["streams"]] == ["SGC TV", "Desk cam"] and data["problem"] is None
    stations = {s["name"]: s for s in data["stations"]}
    assert stations["SadGirlsClub"]["play"] == "https://sadgirlsclub.wtf/rr/sgc"       # https: straight from the relay
    assert stations["Telehack"]["play"] == f"/media/radio/src-{made['id']}/stream"    # http: through the server
    assert client.get("/media/radio/nope/stream").status_code == 404

    client.delete(f"/api/admin/media/sources/{made['id']}")
    assert "Telehack" not in {s["name"] for s in client.get("/api/media/live").json()["stations"]}


EMBED = live.YOUTUBE_EMBED


@pytest.mark.parametrize("link, embed", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", EMBED + "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc123", EMBED + "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", EMBED + "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", EMBED + "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/jfKfPfyJRdk?feature=share", EMBED + "jfKfPfyJRdk"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", EMBED + "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
     EMBED + "dQw4w9WgXcQ?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"),
    ("https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
     EMBED + "videoseries?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"),
    ("https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow", EMBED + "live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow"),
    ("https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow/live", EMBED + "live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow"),
    (EMBED + "live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow", EMBED + "live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow"),
])
def test_youtube_links_become_embed_addresses(link, embed):
    assert live.youtube_embed(link) == embed


@pytest.mark.parametrize("link, problem", [
    ("https://www.youtube.com/@LofiGirl", "channel id"),
    ("https://vimeo.com/123456", "isn't a YouTube link"),
    ("https://www.youtube.com/watch?v=short", "doesn't look right"),
    ("https://www.youtube.com/", "Use a YouTube"),
])
def test_youtube_links_that_cant_be_embedded_are_refused(link, problem):
    with pytest.raises(live.LiveError, match=problem):
        live.youtube_embed(link)


def test_youtube_sources(client, conn, directory):
    live.TRANSPORT = _relay_transport()
    _login(client, conn, directory)
    _turn_on(client)
    r = client.post("/api/admin/media/sources", json={"kind": "youtube", "name": "Lofi",
                                                      "url": "https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow"})
    assert r.status_code == 201
    assert client.post("/api/admin/media/sources", json={"kind": "youtube", "name": "Handle",
                                                         "url": "https://www.youtube.com/@LofiGirl"}).status_code == 422
    stream = client.get("/api/media/live").json()["streams"][0]
    assert stream == {"id": r.json()["id"], "kind": "youtube", "name": "Lofi", "what": "live",
                      "embed": EMBED + "live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow"}
    assert client.post(f"/api/media/live/{stream['id']}/watch").status_code == 404   # the browser plays it, not ffmpeg
    page = client.get("/media").text
    assert 'id="mc-yt"' in page and 'referrerpolicy="strict-origin-when-cross-origin"' in page


def test_a_relay_that_doesnt_answer_is_reported(client, conn, directory):
    live.TRANSPORT = _relay_transport(status_code=502)
    _login(client, conn, directory)
    _turn_on(client)
    data = client.get("/api/media/live").json()
    assert data["stations"] == [] and "couldn't be read" in data["problem"]


def test_http_stations_are_relayed_through_the_server(client, conn, directory):
    def handler(request):
        if request.url.path == "/rr/healthz":
            return httpx.Response(200, json={"stations": [{"name": "Plain", "path": "http://radio.lan/live.mp3"}]})
        return httpx.Response(200, content=b"ID3fakeaudio", headers={"content-type": "audio/mpeg"})
    live.TRANSPORT = httpx.MockTransport(handler)
    _login(client, conn, directory)
    _turn_on(client)
    station = client.get("/api/media/live").json()["stations"][0]
    r = client.get(station["play"])
    assert r.status_code == 200 and r.content == b"ID3fakeaudio" and r.headers["content-type"] == "audio/mpeg"


# ---------------------------------------------------------------- online
def test_films_and_albums_are_looked_up_online(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    client.put("/api/admin/settings/media", json={"online_metadata": True, "tmdb_key": "0123456789abcdef0123456789abcdef"})
    (root / "Media" / "Movies" / "Alien (1979).mkv").write_bytes(b"\x1a\x45\xdf\xa3 not a real film")
    _aged(root / "Media" / "Movies" / "Alien (1979).mkv")
    seen = []

    def handler(request):
        seen.append(request.url)
        if request.url.host == "api.themoviedb.org":
            assert request.url.params["api_key"] == "0123456789abcdef0123456789abcdef"
            assert (request.url.params["query"], request.url.params["year"]) == ("Alien", "1979")
            return httpx.Response(200, json={"results": [{"id": 348, "title": "Alien", "release_date": "1979-05-25",
                                                          "overview": "In space no one can hear you scream.",
                                                          "poster_path": "/p.jpg", "backdrop_path": "/b.jpg"}]})
        if request.url.host == "musicbrainz.org":
            assert 'release:"Discovery"' in request.url.params["query"]
            return httpx.Response(200, json={"releases": [{"id": "mbid-1", "score": 100}]})
        if request.url.host in ("image.tmdb.org", "coverartarchive.org"):
            return httpx.Response(200, content=_jpeg((1, 2, 3), (500, 750)))
        return httpx.Response(404)

    scrape.TRANSPORT = httpx.MockTransport(handler)
    _scan(conn, settings)
    movie = client.get("/api/media/movies").json()["movies"][0]
    assert movie["overview"].startswith("In space") and movie["tmdb_id"] == 348 and movie["fanart"]
    sources = {r["source"] for r in conn.execute("SELECT source FROM media_artwork")}
    assert "tmdb" in sources

    # An album (tags written straight into the catalog, as if read from files).
    pic = root / "Media" / "Music" / "x.mp3"
    pic.write_bytes(b"not audio")
    _aged(pic)
    share.sync(conn, settings)
    catalog.run_pass(conn, settings)
    key = catalog.album_key("Daft Punk", "Discovery")
    with db.transaction(conn):
        conn.execute("UPDATE media_items SET album = 'Discovery', album_artist = 'Daft Punk', album_key = ? "
                     "WHERE section = 'music'", (key,))
        conn.execute("INSERT OR IGNORE INTO media_albums(album_key) VALUES (?)", (key,))
    catalog.run_pass(conn, settings)
    row = conn.execute("SELECT a.mbid, w.source FROM media_albums a JOIN media_artwork w ON w.id = a.artwork_id "
                       "WHERE a.album_key = ?", (key,)).fetchone()
    assert (row["mbid"], row["source"]) == ("mbid-1", "caa")
    assert client.get("/api/media/music/albums").json()["albums"][0]["art"]
    status = client.get("/api/admin/settings/media").json()
    assert status["tmdb_key_set"] is True and "tmdb_key_enc" not in json.dumps(status)


# ---------------------------------------------------------------- screens
def _pair(client, name="Living room"):
    _screens_for(client)
    r = client.post("/api/media/screens", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"], client.cookies.get("mnemerynne_screen")


def _hub(client):
    from mnemerynne.web.media import hub

    class _Req:
        app = client.app
    return hub(_Req())


def test_a_paired_screen_keeps_media_only(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    assert key and client.get("/api/media/screens").json()["screens"][0]["this"] is True

    tv = TestClient(client.app)                      # the TV after its sign-in ran out: only the screen key
    tv.cookies.set("mnemerynne_screen", key)
    page = tv.get("/media/screen")
    assert page.status_code == 200 and f'data-screen-id="{screen_id}"' in page.text and "Sign out" not in page.text
    assert tv.get("/api/media/home").status_code == 200
    assert tv.get("/api/locations").status_code == 401          # nothing but Media
    assert tv.get("/api/media/screens").status_code == 401      # and it can't cast or manage screens
    assert tv.get("/admin/libraries", follow_redirects=False).status_code == 303

    stranger = TestClient(client.app)
    assert stranger.get("/media/screen", follow_redirects=False).headers["location"] == "/media#screens"
    stranger.cookies.set("mnemerynne_screen", "not-a-key")
    assert stranger.get("/api/media/home").status_code == 401


def test_casting_to_a_screen(client, conn, directory, root, settings):
    live.TRANSPORT = _relay_transport()
    _login(client, conn, directory)
    _turn_on(client)
    (root / "Media" / "Movies" / "Heat (1995).mkv").write_bytes(b"\x1a\x45\xdf\xa3 not a real film")
    _aged(root / "Media" / "Movies" / "Heat (1995).mkv")
    _scan(conn, settings)
    film = conn.execute("SELECT file_id FROM media_items").fetchone()[0]
    screen_id, key = _pair(client)

    off = client.post(f"/api/media/screens/{screen_id}/command", json={"action": "toggle"})
    assert off.status_code == 409 and "isn't connected" in off.json()["detail"]

    queue = _hub(client).listen(screen_id)               # the TV's open stream
    def sent(body):
        r = client.post(f"/api/media/screens/{screen_id}/command", json=body)
        assert r.status_code == 200, r.text
        return queue.get_nowait()

    cmd = sent({"action": "play_item", "file_id": film, "start": 60})
    assert cmd["item"]["title"] == "Heat" and cmd["start"] == 60
    cmd = sent({"action": "play_youtube", "url": "https://youtu.be/dQw4w9WgXcQ"})
    assert cmd["source"]["embed"] == live.YOUTUBE_EMBED + "dQw4w9WgXcQ"
    assert sent({"action": "play_station", "key": "relay-sgc"})["station"]["name"] == "SadGirlsClub"
    assert sent({"action": "skip", "by": -10}) == {"action": "skip", "by": -10}
    assert sent({"action": "volume", "level": 0.4}) == {"action": "volume", "level": 0.4}
    assert sent({"action": "night"}) == {"action": "night"}                      # the next step
    assert sent({"action": "night", "level": 0.55}) == {"action": "night", "level": 0.55}
    assert client.post(f"/api/media/screens/{screen_id}/command",
                       json={"action": "play_youtube", "url": "https://vimeo.com/1"}).status_code == 422
    assert client.post(f"/api/media/screens/{screen_id}/command",
                       json={"action": "play_item", "file_id": 999}).status_code == 404

    # What the TV reports is what the remote shows.
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.put("/api/media/screen/state", json={"kind": "video", "title": "Heat", "position": 61.5,
                                                   "duration": 6000, "paused": False, "volume": 0.4}).status_code == 200
    mine = client.get("/api/media/screens").json()["screens"][0]
    assert mine["online"] is True and mine["state"]["title"] == "Heat" and mine["state"]["position"] == 61.5

    # Someone else can't see or drive it.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get("/api/media/screens").json()["screens"] == []
    assert client.post(f"/api/media/screens/{screen_id}/command", json={"action": "stop"}).status_code == 404
    _hub(client).unlisten(screen_id, queue)


def test_the_screen_stream_and_unpairing(client, conn, directory, monkeypatch):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    hub = _hub(client)
    real_listen = hub.listen

    def listen_then_unpair(sid):
        queue = real_listen(sid)
        queue.put_nowait({"action": "unpaired"})   # ends the stream, so the test can read all of it
        return queue
    monkeypatch.setattr(hub, "listen", listen_then_unpair)
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    r = tv.get("/media/screen/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert "event: hello" in r.text and '"name": "Living room"' in r.text and '"action": "unpaired"' in r.text
    assert "mnemerynne_screen=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert conn.execute("SELECT last_seen_at FROM media_screens").fetchone()[0]

    assert client.delete(f"/api/media/screens/{screen_id}").status_code == 200
    assert tv.get("/media/screen/stream").status_code == 401
    assert tv.get("/api/media/home").status_code == 401
    assert tv.get("/media/screen", follow_redirects=False).status_code == 303


def test_screen_names_are_checked_and_pairing_again_replaces(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    assert client.post("/api/media/screens", json={"name": "  "}).status_code == 422
    first, key1 = _pair(client, "Living room")
    second, key2 = _pair(client, "living  room")
    assert key1 != key2                                # re-pairing issues a new key
    assert [s["name"] for s in client.get("/api/media/screens").json()["screens"]] == ["living room"]


# ------------------------------------------------------------- app, share
def test_erynne_is_installable_and_a_share_target(client, conn, directory):
    m = client.get("/manifest.webmanifest")                     # public: fetched without the session
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    data = m.json()
    assert data["display"] == "standalone" and data["start_url"] == "/" and data["scope"] == "/"
    assert {i["sizes"] for i in data["icons"]} >= {"192x192", "512x512"}
    assert any(i["purpose"] == "maskable" for i in data["icons"])
    for icon in data["icons"]:
        assert client.get(icon["src"]).headers["content-type"] == "image/png"
    assert data["share_target"] == {"action": "/media/share", "method": "GET",
                                    "params": {"title": "title", "text": "text", "url": "url"}}
    _login(client, conn, directory)
    assert 'rel="manifest" href="/manifest.webmanifest"' in client.get("/files").text


@pytest.mark.parametrize("params, link", [
    ({"text": "https://youtu.be/dQw4w9WgXcQ?si=xyz"}, "https://youtu.be/dQw4w9WgXcQ?si=xyz"),           # YouTube app
    ({"title": "Rick Astley", "text": "Never Gonna Give You Up https://youtu.be/dQw4w9WgXcQ"},
     "https://youtu.be/dQw4w9WgXcQ"),
    ({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "x"}, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
])
def test_shared_links_open_screens_ready_to_play(client, conn, directory, params, link):
    from urllib.parse import quote
    _login(client, conn, directory)
    _turn_on(client)
    r = client.get("/media/share", params=params, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/media#cast/" + quote(link, safe="")


def test_sharing_while_signed_out_comes_back_after_sign_in(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    client.cookies.clear()
    r = client.get("/media/share", params={"text": "https://youtu.be/dQw4w9WgXcQ"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    back = client.post("/login", data={"username": "boss", "password": "password123"}, follow_redirects=False)
    assert back.headers["location"].startswith("/media/share?text=")
    assert client.get(back.headers["location"], follow_redirects=False).headers["location"].startswith("/media#cast/")
    assert client.get("/media/share", params={"text": "nothing to see"}, follow_redirects=False).headers[
        "location"] == "/media#screens"


# ------------------------------------------------------------------ groups
def test_media_and_screens_are_for_their_groups_only(client, conn, directory, root, settings, checker):
    _login(client, conn, directory)
    # On, but no group yet: nobody has it, admins included; the settings stay reachable.
    assert client.put("/api/admin/settings/media", json={"enabled": True}).json()["settings"]["media_group"] == ""
    assert client.get("/media").status_code == 404 and client.get("/api/media/home").status_code == 404
    assert 'href="/media"' not in client.get("/files").text
    assert client.get("/media/share", params={"text": "https://youtu.be/dQw4w9WgXcQ"},
                      follow_redirects=False).headers["location"] == "/"

    status = client.put("/api/admin/settings/media", json={"media_group": "Domain Admins"}).json()
    assert status["settings"]["media_group"].lower() == "domain admins"
    assert client.get("/media").status_code == 200 and 'href="/media"' in client.get("/files").text
    assert 'data-view="screens"' not in client.get("/media").text            # screens: no group yet
    assert client.post("/api/media/screens", json={"name": "TV"}).status_code == 403
    assert client.get("/api/media/screens").json()["allowed"] is False
    assert client.put("/api/admin/settings/media", json={"media_group": "No Such Group"}).status_code == 422
    assert conn.execute("SELECT 1 FROM access_log WHERE action = 'settings.media_options'").fetchone()

    _screens_for(client, "Domain Admins")
    assert 'data-view="screens"' in client.get("/media").text
    screen_id = client.post("/api/media/screens", json={"name": "TV"}).json()["id"]
    key = client.cookies.get("mnemerynne_screen")
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.get("/api/media/home").status_code == 200

    # jane is a Domain User, not a Domain Admin: no Media at all.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get("/media").status_code == 404 and client.get("/api/media/movies").status_code == 404
    assert 'href="/media"' not in client.get("/files").text

    # The owner leaves a group (here: the setting changes): the TV stops at once, its key with it.
    client.cookies.clear()
    client.post("/login", data={"username": "boss", "password": "password123"})
    _screens_for(client, "Domain Users; ")      # boss is still in it
    assert tv.get("/api/media/home").status_code == 200
    _screens_for(client, "")                    # nobody
    assert tv.get("/media/screen/stream").status_code == 401
    assert tv.get("/media/screen", follow_redirects=False).status_code == 303
    assert tv.get("/api/media/screen/state").status_code in (401, 405)
    _screens_for(client, "Domain Admins")
    client.put("/api/admin/settings/media", json={"media_group": "Domain Users"})
    assert tv.get("/api/media/home").status_code == 200
    client.put("/api/admin/settings/media", json={"media_group": ""})
    assert tv.get("/api/media/home").status_code == 404       # Media closed to its owner: the TV too
    # Out of the groups, the owner can still remove it.
    assert client.delete(f"/api/media/screens/{screen_id}").status_code in (200, 404)

