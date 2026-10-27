"""Signage (mnemerynne/media/signage.py): admins make playlists (pictures, clips and PDFs from the Signage
folder or the Media library, text, web pages, YouTube, announcements), schedule them for every screen,
a group or one screen, and see each screen's state; the screen fetches its ranked programme and only
the files those playlists name, by its own key."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mnemerynne import notices
from mnemerynne.media import signage
from pdfgen import make_pdf
from test_media import (_aged, _hub, _jpeg, _login, _pair, _scan, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings, _no_network)


def _picture(path, size=(64, 96)):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jpeg(size=size))
    _aged(path)


def _file_id(conn, name):
    return conn.execute("SELECT id FROM files WHERE display_name = ? AND deleted_at IS NULL", (name,)).fetchone()[0]


def _as_video(conn, settings, name, new_name, playback="direct"):
    """A catalogued picture made into a video of the library (no ffmpeg needed for the test)."""
    from mnemerynne.ingest import store
    file_id = _file_id(conn, name)
    path = store.content_path(conn, settings, file_id)
    path.rename(path.with_name(new_name))
    conn.execute("UPDATE media_items SET media_type = 'video', playback = ? WHERE file_id = ?", (playback, file_id))
    conn.execute("UPDATE files SET display_name = ? WHERE id = ?", (new_name, file_id))
    conn.commit()
    return file_id


@pytest.fixture
def lobby(client, conn, directory, root, settings):
    """Boss signed in (an admin), Media on, a Lobby folder of pictures in Media, a Signage folder with a
    picture, a clip and a PDF, and a paired screen."""
    _login(client, conn, directory)
    _turn_on(client)
    lobby = root / "Media" / "Pictures" / "Lobby"
    _picture(lobby / "a-welcome.jpg", size=(4000, 3000))
    _picture(lobby / "b-menu.jpg")
    _picture(lobby / "Old" / "c-last-year.jpg")
    _picture(lobby / "clip.jpg")
    _picture(lobby / "film.jpg")
    assert client.get("/api/admin/signage").json()["signage_library"]["name"] == "Signage"   # made on first look
    promos = root / "Signage" / "Promos"
    _picture(promos / "spa.jpg")
    _picture(promos / "reel.jpg")
    _picture(root / "Signage" / "root-poster.jpg")
    pdf = root / "Signage" / "Promos" / "rates.pdf"
    pdf.write_bytes(make_pdf([["Rates"], ["Spring"], ["Summer"]]))
    _aged(pdf)
    _scan(conn, settings)
    screen_id, key = _pair(client, "Front desk")
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    status = client.get("/api/admin/signage").json()
    folders = {f["name"]: f for f in status["tree"]["folders"]}
    libraries = {l["name"]: l for l in status["tree"]["libraries"]}
    return {"screen": screen_id, "tv": tv, "folder": folders["Lobby"]["id"], "old": folders["Old"]["id"],
            "promos": folders["Promos"]["id"], "signage": libraries["Signage"]["id"], "media": libraries["Media"]["id"]}


def _show(client, name="Lobby"):
    r = client.post("/api/admin/signage/shows", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["created"]


def test_the_browser_sees_both_libraries_with_thumbnails(client, conn, lobby):
    status = client.get("/api/admin/signage").json()
    tree = status["tree"]
    assert {l["name"]: (l["root"], l["signage"]) for l in tree["libraries"]} == {"Media": (False, False), "Signage": (True, True)}
    by_name = {f["name"]: f for f in tree["folders"]}
    assert by_name["Pictures"]["parent_id"] is None and by_name["Lobby"]["parent_id"] == by_name["Pictures"]["id"]
    assert by_name["Old"]["parent_id"] == by_name["Lobby"]["id"] and "Music" not in by_name   # Music isn't signage
    assert by_name["Promos"]["library_id"] == lobby["signage"]
    files = client.get(f"/api/admin/signage/files?library={lobby['signage']}&folder={lobby['promos']}").json()["files"]
    assert [(f["name"], f["type"], f["plays"]) for f in files] == [("rates.pdf", "document", True), ("reel.jpg", "image", True),
                                                                   ("spa.jpg", "image", True)]
    rates = files[0]
    assert rates["pages"] == 3 and rates["art"] and rates["size"] > 0
    art = client.get(rates["art"])
    assert art.status_code == 200 and art.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(art.content)).size[0] <= 960
    roots = client.get(f"/api/admin/signage/files?library={lobby['signage']}").json()["files"]
    assert [f["name"] for f in roots] == ["root-poster.jpg"]
    assert client.get(f"/api/admin/signage/files?library={lobby['media']}").json()["files"] == []   # no root pictures
    assert client.get("/api/admin/signage/files?library=999&folder=1").status_code == 422
    # A folder made from the page lands in the Signage folder, on the share.
    r = client.post("/api/admin/signage/folders", json={"library_id": lobby["signage"], "parent_id": lobby["promos"],
                                                        "name": "Winter"})
    assert r.status_code == 201, r.text
    assert (root_of(conn) / "Signage" / "Promos" / "Winter").is_dir()
    assert client.post("/api/admin/signage/folders", json={"library_id": lobby["media"], "name": "x"}).status_code == 422


def root_of(conn):
    from mnemerynne import share
    return share.current().root


def test_a_playlist_plays_on_the_screens_its_schedule_names(client, conn, settings, lobby):
    clip = _as_video(conn, settings, "clip.jpg", "clip.mp4")
    boss = conn.execute("SELECT id FROM users WHERE username = 'boss'").fetchone()[0]
    notices.create(conn, boss, "Fire drill", "Friday at **3pm**")
    notices.create(conn, boss, "Admins only", "", group_ref="Domain Admins")
    conn.commit()
    tv = lobby["tv"]
    empty = tv.get("/media/screen/signage").json()
    assert empty["idle_minutes"] == 5 and empty["programme"] == [] and empty["shows"] == {}
    assert empty["screen"]["effects"]["crt"] is False

    show = _show(client)
    background = _file_id(conn, "b-menu.jpg")
    rates = _file_id(conn, "rates.pdf")
    slides = [{"kind": "folder", "folder_id": lobby["folder"], "videos": True},
              {"kind": "image", "file_id": _file_id(conn, "spa.jpg"), "seconds": 5, "transition": "zoom", "fit": "cover"},
              {"kind": "pdf", "file_id": rates, "seconds": 15},
              {"kind": "text", "title": "Welcome", "body": "Check-in from 3pm", "image_id": background, "seconds": 20},
              {"kind": "web", "url": "https://status.example.com/board"},
              {"kind": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s", "sound": True},
              {"kind": "announcements", "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"},
              {"kind": "image", "file_id": _file_id(conn, "reel.jpg"), "enabled": False}]
    r = client.put(f"/api/admin/signage/shows/{show}", json={
        "name": "Lobby", "seconds": 8, "transition": "slide", "fit": "blur", "description": "  front  desk ",
        "effects": {"crt": True, "intensity": 0.4, "bogus": 1}, "slides": slides})
    assert r.status_code == 200, r.text
    saved = r.json()["shows"][0]
    assert saved["slides"][0]["subfolders"] is False and saved["description"] == "front desk"   # stored with defaults
    assert saved["effects"] == {"crt": True, "scanlines": True, "curvature": True, "fringe": True, "flicker": True,
                                "glow": True, "power_on": True, "intensity": 0.4}
    assert saved["slides"][1]["file"]["name"] == "spa.jpg" and saved["slides"][0]["folder_label"] == "Media/Pictures/Lobby"

    # Scheduled for every screen (weekdays, idle) and, beating it, for this screen always.
    queue = _hub(client).listen(lobby["screen"])          # the TV's open stream hears about changes
    r = client.post("/api/admin/signage/schedule", json={"show_id": show, "days": [0, 1, 2, 3, 4], "start": "08:00",
                                                         "end": "18:00", "name": "Office hours"})
    assert r.status_code == 201, r.text
    assert queue.get_nowait() == {"action": "signage"}
    r = client.post("/api/admin/signage/schedule", json={"show_id": show, "target": "screen", "target_id": lobby["screen"],
                                                         "mode": "always", "date_from": "2026-01-01", "date_to": "2099-12-31"})
    assert r.status_code == 201, r.text
    assert r.json()["shows"][0]["used_by"] == ["all screens", "Front desk"]
    assert [e["target"] for e in r.json()["schedule"]] == ["screen", "all"]

    got = tv.get("/media/screen/signage").json()
    assert [(e["target"], e["mode"]) for e in got["programme"]] == [("screen", "always"), ("all", "idle")]
    assert got["programme"][0]["effects"]["crt"] is True and got["programme"][0]["effects"]["intensity"] == 0.4
    played = got["shows"][str(show)]
    assert played["transition"] == "slide" and played["fit"] == "blur" and played["shuffle"] is False
    items = played["items"]
    assert [(i["type"], i.get("title")) for i in items] == [
        ("image", "a-welcome"), ("image", "b-menu"), ("video", "clip"), ("image", "film"),
        ("image", "spa"), ("image", "rates · p. 1"), ("image", "rates · p. 2"), ("image", "rates · p. 3"),
        ("text", "Welcome"), ("web", "https://status.example.com/board"), ("youtube", "YouTube dQw4w9WgXcQ"),
        ("notice", "Fire drill")]
    assert items[0]["seconds"] == 8 and items[4]["seconds"] == 5 and items[4]["transition"] == "zoom" and items[4]["fit"] == "cover"
    assert items[5]["seconds"] == 15 and "page=1" in items[5]["src"] and "page=3" in items[7]["src"]
    assert items[8]["seconds"] == 20 and "<strong>3pm</strong>" in items[11]["html"]
    assert items[11]["when"] == {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"} and "when" not in items[0]
    assert items[10]["id"] == "dQw4w9WgXcQ" and items[10]["sound"] is True

    # Pictures come screen-sized, PDF pages rendered, videos as they are; nothing outside the playlists.
    big = tv.get(items[0]["src"])
    assert big.status_code == 200 and big.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(big.content)).size == (1440, 1080)
    page = tv.get(items[6]["src"])
    assert page.status_code == 200 and Image.open(io.BytesIO(page.content)).size[1] == 1920
    assert tv.get(items[8]["image"]).status_code == 200
    video = tv.get(items[2]["src"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert tv.get(f"/media/screen/signage/file/{_file_id(conn, 'c-last-year.jpg')}").status_code == 404
    assert tv.get(f"/media/screen/signage/file/{_file_id(conn, 'reel.jpg')}").status_code == 404   # a disabled slide
    assert TestClient(client.app).get(items[0]["src"]).status_code == 401        # the key, nothing else
    assert clip in {i["file_id"] for i in items if i["type"] == "video"}
    # The admin preview is the same list, fetched through admin addresses.
    preview = client.get(f"/api/admin/signage/preview/{show}").json()
    assert [i["title"] for i in preview["items"]] == [i["title"] for i in items] and preview["effects"]["crt"] is True
    assert preview["items"][0]["src"].startswith("/api/admin/signage/file/")
    assert client.get(preview["items"][6]["src"]).status_code == 200
    assert client.get(f"/api/admin/signage/file/{_file_id(conn, 'reel.jpg')}").status_code == 404

    # With its subfolders, and without clips; the Signage folder's root as a folder slide.
    slides[0] |= {"subfolders": True, "videos": False}
    client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "slides": slides[:1] + [{"kind": "folder"}]})
    items = tv.get("/media/screen/signage").json()["shows"][str(show)]["items"]
    assert [i["title"] for i in items] == ["a-welcome", "b-menu", "c-last-year", "film", "root-poster"]
    assert tv.get(items[2]["src"]).status_code == 200

    # A copy, then removing the original takes it off the schedule.
    copy = client.post(f"/api/admin/signage/shows/{show}/duplicate").json()
    assert [s["name"] for s in copy["shows"]] == ["Lobby", "Lobby (2)"] and copy["shows"][1]["slides"] == copy["shows"][0]["slides"]
    r = client.delete(f"/api/admin/signage/shows/{show}")
    assert [s["name"] for s in r.json()["shows"]] == ["Lobby (2)"] and r.json()["schedule"] == []
    assert tv.get("/media/screen/signage").json()["programme"] == []
    actions = [r[0] for r in conn.execute("SELECT action FROM access_log WHERE action LIKE 'signage.%' ORDER BY id")]
    assert actions == ["signage.show_create", "signage.show_update", "signage.schedule_create", "signage.schedule_create",
                       "signage.show_update", "signage.show_create", "signage.show_delete"]
    _hub(client).unlisten(lobby["screen"], queue)


def test_groups_rank_the_programme_and_screens_report_in(client, conn, lobby):
    everyone, bar = _show(client, "Everyone"), _show(client, "Bar")
    r = client.post("/api/admin/signage/groups", json={"name": "Bar screens"})
    assert r.status_code == 201, r.text
    group = r.json()["created"]
    assert client.post("/api/admin/signage/groups", json={"name": " bar screens "}).status_code == 422
    r = client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={
        "idle_minutes": 3, "group_id": group, "location": " Bar,  ground floor ", "orientation": "portrait",
        "effects": {"crt": True, "flicker": False}})
    assert r.status_code == 200, r.text
    screen = next(s for s in r.json()["screens"] if s["id"] == lobby["screen"])
    assert screen["group"] == "Bar screens" and screen["location"] == "Bar, ground floor" and screen["orientation"] == "portrait"
    assert screen["effects"]["crt"] is True and screen["effects"]["flicker"] is False and screen["idle_minutes"] == 3
    assert r.json()["groups"] == [{"id": group, "name": "Bar screens", "screens": 1}]

    client.post("/api/admin/signage/schedule", json={"show_id": everyone, "priority": 10})
    client.post("/api/admin/signage/schedule", json={"show_id": bar, "target": "group", "target_id": group})
    got = lobby["tv"].get("/media/screen/signage").json()
    assert [e["show_id"] for e in got["programme"]] == [bar, everyone]          # the group's beats everyone's priority
    assert got["screen"]["orientation"] == "portrait" and got["programme"][0]["effects"]["flicker"] is False
    assert got["programme"][1]["effects"]["crt"] is True                       # the screen's own, no playlist override

    # The screen reports what it shows; that is kept, and counts as online without an open stream.
    r = lobby["tv"].put("/api/media/screen/state", json={"kind": "signage", "title": "Playlist: Bar", "sub": "spa",
                                                         "version": "0.2.0", "errors": ["clip.mp4"]})
    assert r.status_code == 200, r.text
    screen = next(s for s in client.get("/api/admin/signage").json()["screens"] if s["id"] == lobby["screen"])
    assert screen["online"] is True and screen["state"]["title"] == "Playlist: Bar" and screen["player_version"] == "0.2.0"
    assert screen["last_state"]["errors"] == ["clip.mp4"] and screen["last_state_at"]
    conn.execute("UPDATE media_screens SET last_seen_at = '2020-01-01 00:00:00' WHERE id = ?", (lobby["screen"],))
    conn.commit()
    screen = next(s for s in client.get("/api/admin/signage").json()["screens"] if s["id"] == lobby["screen"])
    assert screen["online"] is False and screen["state"]["title"] == "Playlist: Bar"   # the hub's copy still fresh

    # Admin commands reach the screen's stream; a screen that isn't connected says so.
    assert client.post(f"/api/admin/signage/screens/{lobby['screen']}/command", json={"action": "identify"}).status_code == 409
    queue = _hub(client).listen(lobby["screen"])
    assert client.post(f"/api/admin/signage/screens/{lobby['screen']}/command", json={"action": "identify"}).status_code == 200
    assert queue.get_nowait() == {"action": "identify"}
    assert client.post(f"/api/admin/signage/screens/{lobby['screen']}/command", json={"action": "reboot"}).status_code == 422

    # Removing the group drops its entries and frees the screen; its own stream hears about it.
    r = client.delete(f"/api/admin/signage/groups/{group}")
    assert r.json()["groups"] == [] and [e["target"] for e in r.json()["schedule"]] == ["all"]
    assert queue.get_nowait() == {"action": "signage"}
    assert next(s for s in r.json()["screens"] if s["id"] == lobby["screen"])["group"] is None
    _hub(client).unlisten(lobby["screen"], queue)

    # The CRT default for new screens lives with the Media settings.
    r = client.put("/api/admin/settings/media", json={"signage_effects": {"crt": True, "intensity": 0.9}})
    assert r.status_code == 200, r.text
    client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={"effects": None})
    assert lobby["tv"].get("/media/screen/signage").json()["screen"]["effects"]["intensity"] == 0.9
    assert client.get("/api/admin/signage").json()["settings"]["signage_effects"]["crt"] is True
    assert client.put("/api/admin/settings/media", json={"signage_effects": {"intensity": 7}}).status_code == 422


def test_slides_and_schedule_entries_are_checked(client, conn, directory, settings, lobby):
    show = _show(client)
    assert client.post("/api/admin/signage/shows", json={"name": " lobby "}).status_code == 422   # taken
    assert client.post("/api/admin/signage/shows", json={"name": "  "}).status_code == 422
    film = _as_video(conn, settings, "film.jpg", "film.mkv", playback="transcode")

    def save(*slides):
        return client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "slides": list(slides)})
    for bad, says in [({"kind": "web", "url": "javascript:alert(1)"}, "https://"),
                      ({"kind": "folder", "folder_id": 99999}, "isn't in the Signage or Media folders"),
                      ({"kind": "folder", "library_id": lobby["media"]}, "choose a folder"),
                      ({"kind": "video", "file_id": film}, "needs converting"),
                      ({"kind": "image", "file_id": film}, "picture isn't"),
                      ({"kind": "pdf", "file_id": _file_id(conn, "spa.jpg")}, "PDF isn't"),
                      ({"kind": "youtube", "url": "https://example.com/watch?v=dQw4w9WgXcQ"}, "YouTube"),
                      ({"kind": "text", "background": "red"}, "Slide 2"),
                      ({"kind": "text", "start": "09:00"}, "both From and To"),
                      ({"kind": "text", "date_from": "2026-12-01", "date_to": "2026-01-01"}, "wrong way round"),
                      ({"kind": "text", "days": [9]}, "Slide 2"),
                      ({"kind": "text", "transition": "swirl"}, "Slide 2"),
                      ({"kind": "clock"}, "Slide 2"),
                      ({"kind": "text", "image_id": film}, "background picture")]:
        r = save({"kind": "announcements"}, bad)
        assert r.status_code == 422 and says in r.json()["detail"], (bad, r.text)
    assert client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "transition": "wipe"}).status_code == 422
    assert client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "effects": {"intensity": 2}}).status_code == 422

    def entry(**body):
        return client.post("/api/admin/signage/schedule", json={"show_id": show} | body)
    assert entry(show_id=999).status_code == 422
    assert entry(days=[7]).status_code == 422
    assert entry(start="25:00").status_code == 422
    assert entry(mode="sometimes").status_code == 422
    assert entry(target="screen").status_code == 422
    assert entry(target="screen", target_id=999).status_code == 422
    assert entry(target="group", target_id=999).status_code == 422
    assert entry(date_from="2026-02-30").status_code == 422
    assert entry(date_from="2026-03-01", date_to="2026-02-01").status_code == 422
    assert entry(priority=1000).status_code == 422
    assert client.put("/api/admin/signage/screens/999", json={}).status_code == 422
    assert client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={"group_id": 999}).status_code == 422
    assert client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={"orientation": "upside"}).status_code == 422
    assert client.put("/api/admin/signage/schedule/999", json={"show_id": show}).status_code == 422
    assert client.delete("/api/admin/signage/schedule/999").status_code == 422

    # Only admins.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get("/api/admin/signage").status_code == 403
    assert client.post("/api/admin/signage/shows", json={"name": "Mine"}).status_code == 403
    assert client.post("/api/admin/signage/schedule", json={"show_id": show}).status_code == 403


def test_pairing_a_screen_again_keeps_its_signage(client, conn, lobby):
    show = _show(client)
    client.post("/api/admin/signage/schedule", json={"show_id": show, "target": "screen", "target_id": lobby["screen"]})
    queue = _hub(client).listen(lobby["screen"])          # the TV on the old key
    again, key = _pair(client, "front desk")
    assert again == lobby["screen"] and queue.get_nowait() == {"action": "unpaired"}
    assert lobby["tv"].get("/media/screen/signage").status_code == 401        # the old key stopped
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.get("/media/screen/signage").json()["programme"][0]["show_id"] == show
    _hub(client).unlisten(lobby["screen"], queue)


def test_old_rules_become_schedule_entries(tmp_path):
    """A database from signage 0.1 (per-screen JSON rules) comes through migration 0045 as entries."""
    from mnemerynne import db, users
    from mnemerynne.config import Settings
    s = Settings(_env_file=None, data_dir=tmp_path / "d", secret_key="test-secret-key-for-the-test-suite-only", embed_dim=4)
    c = db.connect(s.db_path)
    files = db._migration_files()
    old = next(n for n, p in files if p.name == "0044_screen_safe_area.sql")
    for number, path in files:
        if number > old:
            break
        c.executescript(f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\nCOMMIT;")
    uid = users.create_user(c, "boss", "password123", role="admin")
    c.execute("INSERT INTO media_screens(user_id, name, token_hash) VALUES (?, 'TV', 'x')", (uid,))
    show = c.execute("INSERT INTO signage_shows(name) VALUES ('Lobby')").lastrowid
    c.execute("INSERT INTO signage_screens(screen_id, idle_minutes, rules) VALUES (1, 7, ?)", (json.dumps([
        {"show_id": show, "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00", "mode": "idle"},
        {"show_id": show, "days": [5, 6], "start": "00:00", "end": "00:00", "mode": "always"},
        {"show_id": 999, "days": [0], "start": "00:00", "end": "00:00", "mode": "idle"}]),))
    c.commit()
    db.migrate(c, s.embed_dim)
    entries = signage.schedule(c)
    assert [(e["target"], e["target_id"], e["days"], e["start"], e["end"], e["mode"], e["priority"]) for e in entries] == [
        ("screen", 1, [0, 1, 2, 3, 4], "08:00", "18:00", "idle", 100), ("screen", 1, [5, 6], "00:00", "00:00", "always", 99)]
    assert c.execute("SELECT idle_minutes FROM signage_screens WHERE screen_id = 1").fetchone()[0] == 7
    assert "rules" not in [r[1] for r in c.execute("PRAGMA table_info(signage_screens)")]
    assert "pages" in [r[1] for r in c.execute("PRAGMA table_info(media_items)")]
    c.close()


def test_the_pages_carry_the_signage_panel_and_the_shared_renderer(client, conn, lobby):
    page = client.get("/admin/libraries").text
    for piece in ('id="signage-side"', 'id="signage-tree"', 'id="signage-files"', 'id="signage-tabs"', 'data-sg-tab="schedule"',
                  'id="signage-upload"', "signage_render.js", "signage_crt.css", "admin_signage.js"):
        assert piece in page, piece
    tv = lobby["tv"].get("/media/screen").text
    assert "signage_render.js" in tv and "signage_crt.css" in tv
    assert client.get("/static/signage_render.js").status_code == 200
    assert client.get("/help/admin/signage").status_code == 200 and "CRT effects" in client.get("/help/admin/signage").text
