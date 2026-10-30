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
    assert "Pair this screen" in stranger.get("/media/screen", follow_redirects=False).text   # a code, not sign-in
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
        cmd = queue.get_nowait()
        # Every command has an id to ask after, and says who sent it (the countdown on a box shows that).
        assert cmd.pop("id") == r.json()["id"] and cmd.pop("sender") == "boss" and cmd.pop("override") is False
        return cmd

    cmd = sent({"action": "play_item", "file_id": film, "start": 60})
    assert cmd["item"]["title"] == "Heat" and cmd["start"] == 60
    cmd = sent({"action": "play_youtube", "url": "https://youtu.be/dQw4w9WgXcQ"})
    assert cmd["source"]["embed"] == live.YOUTUBE_EMBED + "dQw4w9WgXcQ"
    assert sent({"action": "play_station", "key": "relay-sgc"})["station"]["name"] == "SadGirlsClub"
    assert sent({"action": "skip", "by": -10}) == {"action": "skip", "by": -10}
    assert sent({"action": "volume", "level": 0.4}) == {"action": "volume", "level": 0.4}
    assert sent({"action": "mute"}) == {"action": "mute"}
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
                                                   "duration": 6000, "paused": False, "volume": 0.4,
                                                   "muted": True}).status_code == 200
    mine = client.get("/api/media/screens").json()["screens"][0]
    assert mine["online"] is True and mine["state"]["title"] == "Heat" and mine["state"]["position"] == 61.5
    assert mine["state"]["muted"] is True

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
    assert "Pair this screen" in tv.get("/media/screen", follow_redirects=False).text


def test_screen_names_are_checked_and_pairing_again_replaces(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    assert client.post("/api/media/screens", json={"name": "  "}).status_code == 422
    first, key1 = _pair(client, "Living room")
    second, key2 = _pair(client, "living  room")
    assert key1 != key2                                # re-pairing issues a new key
    assert [s["name"] for s in client.get("/api/media/screens").json()["screens"]] == ["living room"]


def test_a_screen_keeps_its_underscan(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert 'data-safe-x="0.0" data-safe-y="0.0"' in tv.get("/media/screen").text
    assert 'id="mc-calibrate"' not in tv.get("/media/screen").text       # in Kiosk settings (and the phone) now

    r = tv.put("/media/screen/safe-area", json={"x": 3.2, "y": 2.5})
    assert r.status_code == 200 and r.json() == {"x": 3.0, "y": 2.5}         # half-percent steps
    assert 'data-safe-x="3.0" data-safe-y="2.5"' in tv.get("/media/screen").text
    assert client.get("/api/media/screens").json()["screens"][0]["safe"] == {"x": 3.0, "y": 2.5}
    assert tv.put("/media/screen/safe-area", json={"x": 11, "y": 0}).status_code == 422
    assert TestClient(client.app).put("/media/screen/safe-area", json={"x": 1, "y": 1}).status_code == 401   # by the key

    # The phone drives the TV's calibration screen.
    queue = _hub(client).listen(screen_id)
    for body, sent in [({"action": "calibrate"}, {"action": "calibrate", "step": "open"}),
                       ({"action": "calibrate", "step": "wider"}, {"action": "calibrate", "step": "wider"})]:
        assert client.post(f"/api/media/screens/{screen_id}/command", json=body).status_code == 200
        assert {k: v for k, v in queue.get_nowait().items() if k not in ("id", "sender", "override")} == sent
    assert client.post(f"/api/media/screens/{screen_id}/command",
                       json={"action": "calibrate", "step": "sideways"}).status_code == 422
    assert tv.put("/api/media/screen/state", json={"kind": "calibrating", "title": "Adjusting"}).status_code == 200
    _hub(client).unlisten(screen_id, queue)

    # Pairing it again keeps it (it's the same TV).
    _pair(client)
    assert conn.execute("SELECT safe_x, safe_y FROM media_screens WHERE id = ?", (screen_id,)).fetchone()[:] == (3.0, 2.5)


def test_a_screen_keeps_its_text_size(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    page = tv.get("/media/screen").text
    assert 'data-tv-scale="auto"' in page and "--ui-scale" not in page and 'data-ui-scale=""' in page   # automatic
    assert client.get("/api/media/screens").json()["screens"][0]["scale"] is None

    r = tv.put("/media/screen/ui-scale", json={"scale": 1.6})
    assert r.status_code == 200 and r.json() == {"scale": 1.5}                   # quarter steps
    page = tv.get("/media/screen").text
    assert "--ui-scale: 1.5" in page and 'data-tv-scale="auto"' not in page and 'data-ui-scale="1.5"' in page
    assert client.get("/api/media/screens").json()["screens"][0]["scale"] == 1.5
    for wrong in (0.5, 3.5, "big"):
        assert tv.put("/media/screen/ui-scale", json={"scale": wrong}).status_code == 422
    assert tv.put("/media/screen/ui-scale", json={}).status_code == 422          # automatic is an explicit null
    assert TestClient(client.app).put("/media/screen/ui-scale", json={"scale": 2}).status_code == 401   # by the key
    assert tv.put("/media/screen/ui-scale", json={"scale": None}).json() == {"scale": None}
    assert 'data-tv-scale="auto"' in tv.get("/media/screen").text
    with pytest.raises(Exception):                                                # the database holds the limits too
        conn.execute("UPDATE media_screens SET ui_scale = 5 WHERE id = ?", (screen_id,))

    # The phone drives the TV's Text size screen; the underscan's steps aren't its.
    queue = _hub(client).listen(screen_id)
    for body, sent in [({"action": "text_size"}, {"action": "text_size", "step": "open"}),
                       ({"action": "text_size", "step": "bigger"}, {"action": "text_size", "step": "bigger"}),
                       ({"action": "text_size", "step": "auto"}, {"action": "text_size", "step": "auto"})]:
        assert client.post(f"/api/media/screens/{screen_id}/command", json=body).status_code == 200
        assert {k: v for k, v in queue.get_nowait().items() if k not in ("id", "sender", "override")} == sent
    for body in ({"action": "text_size", "step": "wider"}, {"action": "calibrate", "step": "bigger"}):
        assert client.post(f"/api/media/screens/{screen_id}/command", json=body).status_code == 422
    _hub(client).unlisten(screen_id, queue)

    # Pairing it again keeps it.
    tv.put("/media/screen/ui-scale", json={"scale": 2})
    _pair(client)
    assert conn.execute("SELECT ui_scale FROM media_screens WHERE id = ?", (screen_id,)).fetchone()[0] == 2.0


def _tv_code(tv):
    r = tv.post("/media/screen/pair")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_tv_pairs_with_a_code_from_the_phone(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    tv = TestClient(client.app)                       # no sign-in, no keyboard
    page = tv.get("/media/screen")
    assert page.status_code == 200 and "Pair this screen" in page.text and "screen-pair.js" in page.text
    pairing = _tv_code(tv)
    code = pairing["code"]
    assert len(code) == 9 and code[4] == "-" and pairing["qr"].startswith("<svg")
    assert pairing["url"].endswith("/media/pair?code=" + code.replace("-", ""))
    assert "HttpOnly" in tv.post("/media/screen/pair").headers["set-cookie"]
    assert _tv_code(tv)["code"] == code               # reloading the page keeps the same code
    assert tv.get("/media/screen/pair").json()["state"] == "waiting"
    assert tv.get("/api/media/home").status_code == 401

    # The QR code opens Media → Screens on the phone with the code filled in.
    link = client.get("/media/pair", params={"code": code}, follow_redirects=False)
    assert link.headers["location"] == "/media#pair/" + code.replace("-", "")
    r = client.post("/api/media/screens/claim", json={"code": " " + code.lower().replace("-", " "), "name": "Den"})
    assert r.status_code == 201, r.text
    screen_id = r.json()["id"]

    status = tv.get("/media/screen/pair")
    assert status.json() == {"state": "paired", "url": "/media/screen"}
    assert "mnemerynne_screen=" in status.headers["set-cookie"]
    page = tv.get("/media/screen")
    assert f'data-screen-id="{screen_id}"' in page.text and 'data-screen-name="Den"' in page.text
    assert tv.get("/api/media/home").status_code == 200
    assert tv.get("/media/screen/pair").json()["state"] == "expired"      # the key is handed over once
    assert client.post("/api/media/screens/claim", json={"code": code, "name": "Again"}).status_code == 422
    assert [s["name"] for s in client.get("/api/media/screens").json()["screens"]] == ["Den"]


def test_pairing_codes_run_out_and_wrong_ones_are_limited(client, conn, directory, monkeypatch):
    from mnemerynne.media import screens
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    tv = TestClient(client.app)
    code = _tv_code(tv)["code"]
    for _ in range(screens.CLAIM_TRIES):
        r = client.post("/api/media/screens/claim", json={"code": "BBBB-BBBB", "name": "TV"})
        assert r.status_code == 422 and "isn't on any screen" in r.json()["detail"]
    r = client.post("/api/media/screens/claim", json={"code": code, "name": "TV"})
    assert r.status_code == 429                       # even the right code, until the window passes
    assert tv.get("/media/screen/pair").json()["state"] == "waiting"

    later = time.monotonic() + screens.PAIR_SECONDS + 1
    monkeypatch.setattr(screens.time, "monotonic", lambda: later)
    assert tv.get("/media/screen/pair").json()["state"] == "expired"
    assert _tv_code(tv)["code"] != code               # a new one
    assert client.post("/api/media/screens/claim", json={"code": code, "name": "TV"}).status_code == 422
    assert client.get("/api/media/screens").json()["screens"] == []


def test_pairing_codes_need_the_screens_group_and_sign_in(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    tv = TestClient(client.app)
    code = _tv_code(tv)["code"]
    assert client.post("/api/media/screens/claim", json={"code": code, "name": "TV"}).status_code == 403
    signed_out = TestClient(client.app)
    assert signed_out.post("/api/media/screens/claim", json={"code": code, "name": "TV"}).status_code == 401
    r = signed_out.get("/media/pair", params={"code": code}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    client.put("/api/admin/settings/media", json={"enabled": False})
    assert tv.post("/media/screen/pair").status_code == 404 and tv.get("/media/screen").status_code == 404


# ---------------------------------------------------------------- remotes
def _remote(client, name="Kitchen tablet"):
    """A remote panel paired from the phone: its client (no cookies kept, like the panel) and its headers."""
    panel = TestClient(client.app)
    pairing = panel.post("/media/remote/pair").json()
    assert len(pairing["code"]) == 9 and pairing["url"].endswith("/media/pair?code=" + pairing["code"].replace("-", ""))
    assert panel.post("/media/remote/pair/status", json={"secret": pairing["secret"]}).json()["state"] == "waiting"
    r = client.post("/api/media/screens/claim", json={"code": pairing["code"], "name": name})
    assert r.status_code == 201 and r.json()["kind"] == "remote", r.text
    got = panel.post("/media/remote/pair/status", json={"secret": pairing["secret"]}).json()
    assert got["state"] == "paired" and got["name"] == name and "mnemerynne_screen" not in panel.cookies
    assert panel.post("/media/remote/pair/status", json={"secret": pairing["secret"]}).json()["state"] == "expired"
    return panel, {"Authorization": "Bearer " + got["key"]}, r.json()["id"]


def test_a_remote_pairs_with_a_code_and_controls_only_its_owners_screens(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    panel, auth, remote_id = _remote(client)
    assert client.get("/api/media/screens").json()["remotes"][0]["name"] == "Kitchen tablet"
    assert [s["name"] for s in client.get("/api/media/screens").json()["screens"]] == ["Living room"]  # not a screen

    seen = panel.get("/api/media/remote", headers=auth).json()
    assert seen["name"] == "Kitchen tablet" and seen["owner"] == "boss"
    assert seen["screens"] == [{"id": screen_id, "name": "Living room", "online": False, "speaker": False, "state": None}]
    assert seen["speaker"] is None                                          # paired without a speaker
    assert conn.execute("SELECT last_seen_at FROM media_remotes").fetchone()[0]
    off = panel.post(f"/api/media/remote/screens/{screen_id}/command", json={"action": "toggle"}, headers=auth)
    assert off.status_code == 409

    queue = _hub(client).listen(screen_id)                # the TV's open stream
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.put("/api/media/screen/state", json={"kind": "audio", "title": "One More Time", "volume": 0.4,
                                                    "art": "/media/art/1", "position": 12}).status_code == 200
    state = panel.get("/api/media/remote", headers=auth).json()["screens"][0]["state"]
    assert state["title"] == "One More Time" and state["volume"] == 0.4 and "art" not in state
    for body in ({"action": "toggle"}, {"action": "skip", "by": 30}, {"action": "volume", "level": 0.5},
                 {"action": "night"}, {"action": "stop"}):
        r = panel.post(f"/api/media/remote/screens/{screen_id}/command", json=body, headers=auth)
        assert r.status_code == 200, r.text
        sent = queue.get_nowait()
        assert sent["action"] == body["action"] and sent["sender"] == "boss" and sent["override"] is False
    assert sent == {"action": "stop", "id": sent["id"], "sender": "boss", "override": False}
    r = panel.post(f"/api/media/remote/screens/{screen_id}/command", json={"action": "press", "button": "left"}, headers=auth)
    assert r.status_code == 200, r.text                   # the panel's arrows and Enter
    sent = queue.get_nowait()
    assert sent == {"action": "press", "button": "left", "id": sent["id"], "sender": "boss", "override": False}

    # Only the remote's commands, and nothing else of Media.
    for body in ({"action": "play_item", "file_id": 1}, {"action": "calibrate"}, {"action": "play_youtube", "url": "x"},
                 {"action": "press"}, {"action": "press", "button": "Escape"}):
        assert panel.post(f"/api/media/remote/screens/{screen_id}/command", json=body, headers=auth).status_code == 422
    assert panel.get("/api/media/home", headers=auth).status_code == 401
    assert panel.get("/api/media/screens", headers=auth).status_code == 401
    assert panel.get("/api/media/remote", headers={"Authorization": "Bearer not-a-key"}).status_code == 401
    assert panel.get("/api/media/remote").status_code == 401

    # Someone else's screen: not there, though the remote's owner is an admin.
    _login(client, conn, directory, "jane", "jsmith")
    other_id = client.post("/api/media/screens", json={"name": "Jane's TV"}).json()["id"]
    assert panel.post(f"/api/media/remote/screens/{other_id}/command", json={"action": "stop"},
                      headers=auth).status_code == 404
    assert client.delete(f"/api/media/remotes/{remote_id}").status_code == 404     # only its owner removes it

    client.cookies.clear()
    client.post("/login", data={"username": "boss", "password": "password123"})
    assert client.delete(f"/api/media/remotes/{remote_id}").status_code == 200
    assert panel.get("/api/media/remote", headers=auth).status_code == 401
    assert client.get("/api/media/screens").json()["remotes"] == []
    _hub(client).unlisten(screen_id, queue)


def test_a_remotes_poll_never_waits_on_the_write_lock(client, conn, directory, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _pair(client)
    panel, auth, _ = _remote(client)
    writer = db.connect(settings.db_path)               # another writer, holding the lock (a catalog pass, say)
    writer.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    assert panel.get("/api/media/remote", headers=auth).status_code == 200
    assert time.monotonic() - started < 5               # it used to wait out the 10 s busy timeout
    writer.execute("ROLLBACK")
    writer.close()
    assert conn.execute("SELECT last_seen_at FROM media_remotes").fetchone()[0] is None  # skipped while locked
    assert panel.get("/api/media/remote", headers=auth).status_code == 200
    assert conn.execute("SELECT last_seen_at FROM media_remotes").fetchone()[0]           # so the next poll saves it
    conn.execute("UPDATE media_remotes SET last_seen_at = NULL")
    assert panel.get("/api/media/remote", headers=auth).status_code == 200
    assert conn.execute("SELECT last_seen_at FROM media_remotes").fetchone()[0] is None  # then once a minute at most


# ---- built-in games (mnemerynne/games.py)
def test_built_in_games_open_from_media_and_show_on_a_remote(client, conn, directory):
    from mnemerynne import games
    anonymous = TestClient(client.app)
    _login(client, conn, directory)
    _turn_on(client)
    assert 'data-view="games"' in client.get("/media").text                # built in: listed with no setup
    listed = client.get("/api/media/games").json()["games"]
    assert [g["slug"] for g in listed] == [g.slug for g in games.GAMES] and listed[0]["name"] == "Prism Drop"
    page = client.get("/media/games/prism-drop")
    assert page.status_code == 200
    for name in ("games/common.js", "games/common.css", "games/prism-drop/rules.js", "games/prism-drop/game.js",
                 "games/prism-drop/game.css"):
        assert f"/static/{name}?v=" in page.text
        assert (Path(__file__).parent.parent / "mnemerynne" / "web" / "static" / name).is_file()
    assert 'id="pd-field"' in page.text and "messages.js" not in page.text   # the game's partial, none of the app's scripts
    assert client.get("/media/games/no-such-game").status_code == 404
    assert anonymous.get("/api/media/games").status_code == 401
    assert anonymous.get("/media/games/prism-drop").status_code == 401

    # On the TV: it plays as its owner, and its score line reaches the remote panels.
    screen_id, key = _pair(client)
    panel, auth, _ = _remote(client)
    queue = _hub(client).listen(screen_id)
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.get("/api/media/games").status_code == 200 and tv.get("/media/games/prism-drop").status_code == 200
    r = tv.put("/api/media/screen/state", json={"kind": "game", "title": "Prism Drop", "sub": "Score 35 · drop 3 of 10 · pocket 5"})
    assert r.status_code == 200, r.text
    state = panel.get("/api/media/remote", headers=auth).json()["screens"][0]["state"]
    assert state["kind"] == "game" and state["title"] == "Prism Drop" and state["sub"].startswith("Score 35")
    r = panel.post(f"/api/media/remote/screens/{screen_id}/command", json={"action": "press", "button": "enter"}, headers=auth)
    assert r.status_code == 200 and queue.get_nowait()["button"] == "enter"   # media.js hands it to the game
    _hub(client).unlisten(screen_id, queue)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js isn't installed")
def test_prism_drop_rules_pass_in_node():
    tests = sorted(str(p) for p in (Path(__file__).parent / "js").glob("*.test.mjs"))
    assert tests
    r = subprocess.run(["node", "--test", *tests], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


def test_a_remote_starts_channels_forgets_itself_and_follows_the_group(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    client.put("/api/admin/settings/media", json={"radio_directory_url": ""})
    screen_id, _ = _pair(client)
    for n in range(3):
        client.post("/api/admin/media/sources", json={"kind": "hls", "name": f"Cam {n}",
                                                      "url": f"http://10.1.1.21/stream/hls/{n}.m3u8"})
    client.post("/api/admin/media/sources", json={"kind": "radio", "name": "Telehack", "url": "http://10.1.1.21/r.mp3"})
    panel, auth, _ = _remote(client)

    page = panel.get("/api/media/remote/channels", params={"offset": 2, "limit": 2}, headers=auth).json()
    assert page["total"] == 4 and [c["name"] for c in page["channels"]] == ["Cam 2", "Telehack"]
    station = page["channels"][1]
    assert station["kind"] == "station" and station["key"].startswith("src-")
    queue = _hub(client).listen(screen_id)
    r = panel.post(f"/api/media/remote/screens/{screen_id}/command",
                   json={"action": "play_station", "key": station["key"]}, headers=auth)
    assert r.status_code == 200 and queue.get_nowait()["station"]["name"] == "Telehack"
    assert panel.post(f"/api/media/remote/screens/{screen_id}/command",
                      json={"action": "play_station", "key": "nope"}, headers=auth).status_code == 404
    _hub(client).unlisten(screen_id, queue)

    # The owner leaves the screens group: the remote stops at once; back in, it works again.
    _screens_for(client, "")
    assert panel.get("/api/media/remote", headers=auth).status_code == 401
    _screens_for(client)
    assert panel.get("/api/media/remote", headers=auth).status_code == 200

    # A remote's pairing secret can't collect a TV's key, nor a TV's a remote's.
    tv = TestClient(client.app)
    tv.post("/media/screen/pair")
    assert panel.post("/media/remote/pair/status",
                      json={"secret": tv.cookies.get("mnemerynne_screen_pairing")}).json()["state"] == "expired"

    assert panel.delete("/api/media/remote", headers=auth).status_code == 200      # the panel's Forget
    assert panel.get("/api/media/remote", headers=auth).status_code == 401
    assert conn.execute("SELECT count(*) FROM media_remotes").fetchone()[0] == 0


def _speaker(client, name="Bedroom speaker"):
    """A remote panel with a speaker, paired from the phone as a speaker."""
    panel = TestClient(client.app)
    pairing = panel.post("/media/remote/pair", json={"speaker": True}).json()
    r = client.post("/api/media/screens/claim", json={"code": pairing["code"], "name": name})
    assert r.status_code == 201 and r.json()["kind"] == "speaker", r.text
    got = panel.post("/media/remote/pair/status", json={"secret": pairing["secret"]}).json()
    assert got["state"] == "paired" and got["name"] == name
    return panel, {"Authorization": "Bearer " + got["key"]}, r.json()["id"]


@needs_ffmpeg
def test_a_speaker_plays_only_what_is_cast_to_it_through_tickets(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    disc = root / "Media" / "Music" / "Daft Punk" / "Discovery"
    disc.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libmp3lame", "-metadata", "title=One More Time",
            "-metadata", "artist=Daft Punk", "-metadata", "album=Discovery", "-metadata", "track=1",
            str(disc / "01 One More Time.mp3"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "alac", "-metadata", "title=Aerodynamic",
            "-metadata", "artist=Daft Punk", "-metadata", "album=Discovery", "-metadata", "track=2",
            str(disc / "02 Aerodynamic.m4a"))
    for p in disc.iterdir():
        _aged(p)
    _scan(conn, settings)
    album = client.get("/api/media/music/albums").json()["albums"][0]
    first = client.get("/api/media/music/album", params={"key": album["key"]}).json()["tracks"][0]

    panel, auth, remote_id = _speaker(client)
    seen = panel.get("/api/media/remote", headers=auth).json()
    speaker_id = seen["speaker"]
    assert speaker_id and [s["speaker"] for s in seen["screens"] if s["id"] == speaker_id] == [True]
    assert any(s["id"] == speaker_id and s["speaker"] for s in client.get("/api/media/screens").json()["screens"])
    # Its key never opens Media, nor the speaker's screen as a TV would.
    assert panel.get("/api/media/music/albums", headers=auth).status_code == 401
    assert panel.get(first["stream"], headers=auth).status_code == 401
    assert panel.get("/media/screen/stream", headers=auth).status_code == 401

    queue = _hub(client).listen(speaker_id)                               # the speaker's open stream
    r = client.post(f"/api/media/screens/{speaker_id}/command", json={"action": "play_album", "key": album["key"]})
    assert r.status_code == 200, r.text
    sent = queue.get_nowait()
    assert sent["action"] == "play_queue" and sent["index"] == 0 and sent["sender"] == "boss"
    tracks = sent["tracks"]
    assert [(t["title"], t["sub"]) for t in tracks] == [("One More Time", "Daft Punk - Discovery"),
                                                        ("Aerodynamic", "Daft Punk - Discovery")]
    assert all(t["ticket"].endswith(".mp3") for t in tracks)                # ALAC goes out converted to MP3
    played = panel.get("/media/speaker/audio/" + tracks[0]["ticket"])       # the ticket alone, no key
    assert played.status_code == 200 and played.headers["content-type"] == "audio/mpeg"
    assert played.content == (disc / "01 One More Time.mp3").read_bytes()
    assert panel.get("/media/speaker/audio/" + tracks[0]["ticket"], headers={"Range": "bytes=0-9"}).status_code == 206
    converted = panel.get("/media/speaker/audio/" + tracks[1]["ticket"])
    assert converted.status_code == 200 and converted.content[:3] == b"ID3"
    assert panel.get("/media/speaker/audio/" + tracks[0]["ticket"].replace(".mp3", ".flac")).status_code == 404
    assert panel.get("/media/speaker/audio/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.mp3").status_code == 404

    # From its own remote too; what a speaker can't play is refused.
    r = panel.post(f"/api/media/remote/screens/{speaker_id}/command", json={"action": "toggle"}, headers=auth)
    assert r.status_code == 200 and queue.get_nowait()["action"] == "toggle"
    for action in ("calibrate", "text_size"):
        refused = client.post(f"/api/media/screens/{speaker_id}/command", json={"action": action})
        assert refused.status_code == 422 and "speaker" in refused.json()["detail"]

    # It reports what it plays with the remote's key; a remote without a speaker has no such thing.
    assert panel.put("/api/media/speaker/state", json={"kind": "audio", "title": "One More Time", "volume": 0.5},
                     headers=auth).status_code == 200
    mine = [s for s in panel.get("/api/media/remote", headers=auth).json()["screens"] if s["id"] == speaker_id][0]
    assert mine["state"]["title"] == "One More Time"
    plain, plain_auth, _ = _remote(client)
    assert plain.put("/api/media/speaker/state", json={"kind": "idle"}, headers=plain_auth).status_code == 404
    assert plain.get("/api/media/remote", headers=plain_auth).json()["speaker"] is None

    # A speaker doesn't take over a TV's name.
    _pair(client, "Den")
    clash = TestClient(client.app).post("/media/remote/pair", json={"speaker": True}).json()
    assert client.post("/api/media/screens/claim", json={"code": clash["code"], "name": "Den"}).status_code == 422

    # Removing the remote removes its speaker and ends its tickets at once.
    assert client.delete(f"/api/media/remotes/{remote_id}").status_code == 200
    assert queue.get_nowait()["action"] == "unpaired"
    assert panel.get("/media/speaker/audio/" + tracks[0]["ticket"]).status_code == 404
    assert conn.execute("SELECT count(*) FROM media_screens WHERE speaker = 1").fetchone()[0] == 0
    _hub(client).unlisten(speaker_id, queue)


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
    assert "Pair this screen" in tv.get("/media/screen", follow_redirects=False).text
    assert tv.get("/api/media/screen/state").status_code in (401, 405)
    _screens_for(client, "Domain Admins")
    client.put("/api/admin/settings/media", json={"media_group": "Domain Users"})
    assert tv.get("/api/media/home").status_code == 200
    client.put("/api/admin/settings/media", json={"media_group": ""})
    assert tv.get("/api/media/home").status_code == 404       # Media closed to its owner: the TV too
    # Out of the groups, the owner can still remove it.
    assert client.delete(f"/api/media/screens/{screen_id}").status_code in (200, 404)


# ---------------------------------------------------------------------- TV
def _fake_video(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1a\x45\xdf\xa3 not a real episode")
    _aged(path)


def _bluey(root):
    """Media/TV/Bluey with two seasons, a special and a poster; a stray episode right in TV; an empty show."""
    show = root / "Media" / "TV" / "Bluey"
    _fake_video(show / "Season 1" / "Bluey S01E02 Hammerbarn.mkv")
    _fake_video(show / "Season 1" / "Bluey S01E01 Magic Xylophone.mkv")
    _fake_video(show / "Season 2" / "Bluey.S02E01.Dance.Mode.1080p.WEB-DL.mkv")
    _fake_video(show / "Specials" / "Bluey - Christmas Swim.mkv")
    (show / "poster.jpg").write_bytes(_jpeg((40, 120, 200), (200, 300)))
    _aged(show / "poster.jpg")
    _fake_video(root / "Media" / "TV" / "Stray S01E01.mkv")
    (root / "Media" / "TV" / "Nothing Yet").mkdir()


@pytest.mark.parametrize("stem, folder, expected", [
    ("Bluey S01E02 Hammerbarn", "Season 1", ("Hammerbarn", 1, 2)),
    ("bluey.s2e10.the.creek.720p.WEB-DL", "", ("the creek", 2, 10)),
    ("The Office 3x07 - Branch Wars", "Season 3", ("Branch Wars", 3, 7)),
    ("Show.S01E01E02.1080p", "Season 1", ("Episode 1", 1, 1)),
    ("05 - The Pool", "Season 4", ("The Pool", 4, 5)),
    ("Episode 7", "Series 2", ("Episode 7", 2, 7)),
    ("E03 Grannies", "S03", ("Grannies", 3, 3)),
    ("Christmas Swim", "Specials", ("Christmas Swim", 0, None)),
    ("Pilot", "Bluey", ("Pilot", None, None)),
])
def test_episode_names(stem, folder, expected):
    assert probe.episode_name(stem, folder) == expected


def test_tv_shows_are_catalogued_by_show_folder(client, conn, directory, root, settings):
    from mnemerynne.media import signage
    _login(client, conn, directory)
    _turn_on(client)
    assert (root / "Media" / "TV").is_dir()
    _bluey(root)
    assert _scan(conn, settings).read == 4                        # the stray one in TV itself belongs to no show

    shows = client.get("/api/media/tv").json()["shows"]
    assert [(s["name"], s["episodes"], s["seasons"]) for s in shows] == [("Bluey", 4, 2)]   # empty folders left out
    poster = conn.execute("SELECT a.source, a.kind FROM media_tv_shows t JOIN media_artwork a ON a.id = t.artwork_id"
                          ).fetchone()
    assert tuple(poster) == ("local", "poster") and client.get(shows[0]["art"]).status_code == 200

    show = client.get(f"/api/media/tv/{shows[0]['id']}").json()
    assert [s["label"] for s in show["seasons"]] == ["Season 1", "Season 2", "Specials"]
    assert [[(e["season"], e["episode"], e["title"]) for e in s["episodes"]] for s in show["seasons"]] == [
        [(1, 1, "Magic Xylophone"), (1, 2, "Hammerbarn")], [(2, 1, "Dance Mode")], [(0, None, "Bluey - Christmas Swim")]]
    first = show["seasons"][0]["episodes"][0]
    assert first["tv_show"] == "Bluey" and first["tv_show_id"] == show["id"] and first["section"] == "tv"
    assert show["queue"] == [e["id"] for s in show["seasons"][:2] for e in s["episodes"]]   # Specials don't play on
    assert show["next_up"]["id"] == first["id"] and "progress" not in show["next_up"]
    assert client.get(f"/api/media/items/{first['id']}").json()["tv_show"] == "Bluey"

    assert client.get(f"/api/media/tv/{shows[0]['id'] + 999}").status_code == 404
    tv_root = media.tv_root(conn, media.library(conn)["id"])
    assert client.get(f"/api/media/tv/{tv_root}").status_code == 404     # TV itself is no show
    assert client.get("/api/admin/settings/media").json()["catalog"]["counts"]["tv"] == 4
    assert client.get("/api/media/home").json()["tv"][0]["name"] == "Bluey"
    assert "tv" in signage._sections(conn).values()                      # signage may pick episode clips

    # The show's folder removed: its episodes and its poster go.
    shutil.rmtree(root / "Media" / "TV" / "Bluey")
    _scan(conn, settings)
    assert client.get("/api/media/tv").json()["shows"] == []
    assert conn.execute("SELECT count(*) FROM media_tv_shows").fetchone()[0] == 0


def test_tv_next_up_and_continue_watching(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _bluey(root)
    _scan(conn, settings)
    show = client.get("/api/media/tv").json()["shows"][0]
    e1, e2, s2e1 = client.get(f"/api/media/tv/{show['id']}").json()["queue"]

    def watch(file_id, position):
        assert client.put(f"/api/media/progress/{file_id}", json={"position": position, "duration": 420}).status_code == 200

    def next_up():
        return client.get(f"/api/media/tv/{show['id']}").json()["next_up"]

    watch(e1, 200)                                                       # part-way: resume it
    assert next_up()["id"] == e1 and next_up()["progress"]["position"] == 200
    cont = client.get("/api/media/home").json()["continue"]
    assert [(c["id"], c["tv_show"]) for c in cont] == [(e1, "Bluey")]

    watch(e1, 415)                                                       # finished: the next one, from the start
    assert next_up()["id"] == e2 and "progress" not in next_up()
    assert [c["id"] for c in client.get("/api/media/home").json()["continue"]] == [e2]
    assert client.get("/api/media/tv").json()["shows"][0]["next_up"]["id"] == e2

    watch(s2e1, 418)                                                     # the last one done: caught up
    assert next_up() is None
    assert client.get("/api/media/home").json()["continue"] == []


def test_tv_shows_follow_share_permissions(client, conn, directory, root, settings, checker):
    _login(client, conn, directory)
    _turn_on(client)
    _bluey(root)
    _fake_video(root / "Media" / "TV" / "Bluey" / "Season 3" / "Bluey S03E01 Housework.mkv")
    _scan(conn, settings)
    show = client.get("/api/media/tv").json()["shows"][0]
    poster = conn.execute("SELECT artwork_id FROM media_tv_shows").fetchone()[0]

    checker.rules = {"Media/TV/Bluey/Season 3": (False, False)}
    _login(client, conn, directory, "jane", "jsmith")
    seen = client.get(f"/api/media/tv/{show['id']}").json()
    assert [s["label"] for s in seen["seasons"]] == ["Season 1", "Season 2", "Specials"]   # a closed season: hidden

    checker.rules = {"Media/TV/Bluey": (False, False)}
    assert client.get("/api/media/tv").json()["shows"] == []
    assert client.get(f"/api/media/tv/{show['id']}").status_code == 404
    assert client.get(f"/media/art/{poster}").status_code == 404
    assert client.get("/api/media/home").json()["tv"] == []


def test_the_tv_folder_is_added_to_an_older_media_folder_once(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    # As it was before TV: three section folders, and nothing recorded about which were made.
    (root / "Media" / "TV").rmdir()
    conn.execute("DELETE FROM meta WHERE key = ?", (media.SECTIONS_MADE_KEY,))
    _scan(conn, settings)
    assert (root / "Media" / "TV").is_dir()
    (root / "Media" / "TV").rmdir()                                      # removed on purpose: left alone
    _scan(conn, settings)
    assert not (root / "Media" / "TV").exists()


def test_a_screen_browses_and_is_sent_tv_shows(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _bluey(root)
    _scan(conn, settings)
    assert 'data-view="tv"' in client.get("/media").text
    screen_id, key = _pair(client)
    show = client.get("/api/media/tv").json()["shows"][0]

    tv = TestClient(client.app)                                          # the TV, by its screen key alone
    tv.cookies.set("mnemerynne_screen", key)
    assert 'data-view="tv"' in tv.get("/media/screen").text
    assert [s["name"] for s in tv.get("/api/media/tv").json()["shows"]] == ["Bluey"]
    assert tv.get(f"/api/media/tv/{show['id']}").json()["queue"]        # what it plays on through

    queue = _hub(client).listen(screen_id)
    r = client.post(f"/api/media/screens/{screen_id}/command",           # Play on the show: its next episode
                    json={"action": "play_item", "file_id": show["next_up"]["id"], "start": 0})
    assert r.status_code == 200, r.text
    item = queue.get_nowait()["item"]
    assert (item["tv_show"], item["tv_show_id"], item["season"], item["episode"]) == ("Bluey", show["id"], 1, 1)
    assert client.post(f"/api/media/screens/{screen_id}/command", json={"action": "next"}).status_code == 200
    assert queue.get_nowait()["action"] == "next"                        # the TV steps to the next episode
    _hub(client).unlisten(screen_id, queue)


def test_tv_shows_are_looked_up_online(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    client.put("/api/admin/settings/media", json={"online_metadata": True, "tmdb_key": "0123456789abcdef0123456789abcdef"})
    show = root / "Media" / "TV" / "Doctor Who (2005)"
    _fake_video(show / "Season 1" / "Doctor Who S01E01.mkv")                          # no title in the name
    _fake_video(show / "Season 1" / "Doctor Who S01E02 The End of the World.mkv")
    _fake_video(show / "Specials" / "The Christmas Invasion.mkv")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/3/search/tv":
            assert request.url.params["api_key"] == "0123456789abcdef0123456789abcdef"
            assert (request.url.params["query"], request.url.params["first_air_date_year"]) == ("Doctor Who", "2005")
            return httpx.Response(200, json={"results": [{"id": 57243, "name": "Doctor Who", "first_air_date": "2005-03-26",
                                                          "overview": "The Doctor travels in time.",
                                                          "poster_path": "/p.jpg", "backdrop_path": "/b.jpg"}]})
        if request.url.path == "/3/tv/57243/season/1":
            return httpx.Response(200, json={"episodes": [
                {"id": 1001, "episode_number": 1, "name": "Rose", "overview": "Rose meets the Doctor."},
                {"id": 1002, "episode_number": 2, "name": "The End of the World (TMDB)", "overview": "Year five billion."}]})
        if request.url.host == "image.tmdb.org":
            return httpx.Response(200, content=_jpeg((1, 2, 3), (500, 750)))
        return httpx.Response(404)

    scrape.TRANSPORT = httpx.MockTransport(handler)
    _scan(conn, settings)
    found = client.get("/api/media/tv").json()["shows"][0]
    detail = client.get(f"/api/media/tv/{found['id']}").json()
    assert (detail["overview"], detail["year"], found["year"]) == ("The Doctor travels in time.", 2005, 2005)
    assert detail["fanart"] and client.get(detail["art"]).status_code == 200
    episodes = detail["seasons"][0]["episodes"]
    assert [(e["title"], e["overview"]) for e in episodes] == [
        ("Rose", "Rose meets the Doctor."),                                  # TMDB names an unnamed episode
        ("The End of the World", "Year five billion.")]                      # the file's own name is kept
    assert seen.count("/3/tv/57243/season/1") == 1                          # one request a season
    assert conn.execute("SELECT count(*) FROM media_items WHERE section = 'tv' AND scraped_at IS NULL").fetchone()[0] == 0
    sources = conn.execute("SELECT a.source FROM media_tv_shows t JOIN media_artwork a ON a.id = t.artwork_id").fetchone()
    assert sources["source"] == "tmdb"
    assert client.get("/api/media/tv", params={"q": "travels in"}).json()["shows"][0]["name"] == "Doctor Who (2005)"

    # The show's own poster, put in its folder later, replaces TMDB's; nothing is asked again.
    calls = len(seen)
    (show / "poster.jpg").write_bytes(_jpeg((9, 9, 9), (200, 300)))
    _aged(show / "poster.jpg")
    _scan(conn, settings)
    assert conn.execute("SELECT a.source FROM media_tv_shows t JOIN media_artwork a ON a.id = t.artwork_id"
                        ).fetchone()["source"] == "local"
    assert len(seen) == calls


def test_a_shows_poster_is_found_after_its_episodes(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    show = root / "Media" / "TV" / "Bluey"
    _fake_video(show / "Season 1" / "Bluey S01E01 Magic Xylophone.mkv")
    _scan(conn, settings)
    assert client.get("/api/media/tv").json()["shows"][0]["art"] is None   # no poster, no still (not a real video)

    (show / "poster.jpg").write_bytes(_jpeg((40, 120, 200), (200, 300)))
    (show / "fanart.jpg").write_bytes(_jpeg((200, 120, 40), (640, 360)))
    for p in (show / "poster.jpg", show / "fanart.jpg"):
        _aged(p)
    assert _scan(conn, settings).read == 0                                    # no episode is new
    found = client.get("/api/media/tv").json()["shows"][0]
    detail = client.get(f"/api/media/tv/{found['id']}").json()
    assert found["art"] and detail["fanart"] and client.get(detail["fanart"]).status_code == 200


def test_tv_shows_can_be_searched_and_sorted(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _bluey(root)
    _fake_video(root / "Media" / "TV" / "Archer" / "Season 1" / "Archer S01E01 Mole Hunt.mkv")
    _scan(conn, settings)

    def names(**params):
        r = client.get("/api/media/tv", params=params)
        assert r.status_code == 200, r.text
        return [s["name"] for s in r.json()["shows"]]

    assert names() == ["Archer", "Bluey"]
    assert names(q="BLU") == ["Bluey"] and names(q="nothing like it") == []
    with db.transaction(conn):   # Archer's episode added last; Bluey the newer show
        conn.execute("UPDATE files SET created_at = '2030-01-01 00:00:00' WHERE display_name LIKE 'Archer%'")
        conn.execute("UPDATE media_tv_shows SET year = CASE WHEN folder_id = (SELECT id FROM folders WHERE name = 'Bluey') "
                     "THEN 2018 ELSE 2009 END")
    assert names(sort="added") == ["Archer", "Bluey"]
    assert names(sort="year") == ["Bluey", "Archer"]
    bluey = client.get("/api/media/tv", params={"q": "bluey"}).json()["shows"][0]
    client.put(f"/api/media/progress/{bluey['next_up']['id']}", json={"position": 100, "duration": 420})
    assert names(sort="watched") == ["Bluey", "Archer"]                    # never watched: last
    assert client.get("/api/media/tv", params={"sort": "random"}).status_code == 422


def test_episodes_can_be_marked_watched_without_playing(client, conn, directory, root, settings, checker):
    _login(client, conn, directory)
    _turn_on(client)
    _bluey(root)
    _scan(conn, settings)
    show_id = client.get("/api/media/tv").json()["shows"][0]["id"]
    show = client.get(f"/api/media/tv/{show_id}").json()
    season1 = [e["id"] for e in show["seasons"][0]["episodes"]]

    r = client.put("/api/media/watched", json={"file_ids": season1, "watched": True})
    assert r.status_code == 200 and r.json()["changed"] == 2
    show = client.get(f"/api/media/tv/{show_id}").json()
    assert all(e["progress"]["position"] == e["progress"]["duration"] for e in show["seasons"][0]["episodes"])
    assert show["next_up"]["season"] == 2                                  # on to season 2
    assert [c["season"] for c in client.get("/api/media/home").json()["continue"]] == [2]

    client.put("/api/media/watched", json={"file_ids": season1[:1], "watched": False})
    show = client.get(f"/api/media/tv/{show_id}").json()
    assert "progress" not in show["seasons"][0]["episodes"][0]

    checker.rules = {"Media/TV/Bluey": (False, False)}
    _login(client, conn, directory, "jane", "jsmith")
    assert client.put("/api/media/watched", json={"file_ids": season1, "watched": True}).status_code == 404
    assert client.put("/api/media/watched", json={"file_ids": [], "watched": True}).status_code == 422

