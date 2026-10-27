"""Signage (mnemerynne/media/signage.py): admins make slideshows (a Media folder's pictures and clips,
videos, text, web pages, announcements) and set per screen when they play; the screen fetches its
rules and only the files those name, by its own key."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mnemerynne import notices
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
    """Boss signed in (an admin), Media on, a Lobby folder of pictures, and a paired screen."""
    _login(client, conn, directory)
    _turn_on(client)
    lobby = root / "Media" / "Pictures" / "Lobby"
    _picture(lobby / "a-welcome.jpg", size=(4000, 3000))
    _picture(lobby / "b-menu.jpg")
    _picture(lobby / "Old" / "c-last-year.jpg")
    _picture(lobby / "clip.jpg")
    _picture(lobby / "film.jpg")
    _scan(conn, settings)
    screen_id, key = _pair(client, "Front desk")
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    folders = {f["path"]: f["id"] for f in client.get("/api/admin/signage").json()["folders"]}
    return {"screen": screen_id, "tv": tv, "folder": folders["Pictures/Lobby"], "old": folders["Pictures/Lobby/Old"]}


def test_a_slideshow_plays_on_the_screen_its_rules_name(client, conn, settings, lobby):
    clip = _as_video(conn, settings, "clip.jpg", "clip.mp4")
    boss = conn.execute("SELECT id FROM users WHERE username = 'boss'").fetchone()[0]
    notices.create(conn, boss, "Fire drill", "Friday at **3pm**")
    notices.create(conn, boss, "Admins only", "", group_ref="Domain Admins")
    conn.commit()
    tv = lobby["tv"]
    assert tv.get("/media/screen/signage").json() == {"idle_minutes": 5, "rules": [], "shows": {}}

    r = client.post("/api/admin/signage/shows", json={"name": "Lobby"})
    assert r.status_code == 201, r.text
    show = r.json()["created"]
    background = _file_id(conn, "b-menu.jpg")
    slides = [{"kind": "folder", "folder_id": lobby["folder"], "videos": True},
              {"kind": "text", "title": "Welcome", "body": "Check-in from 3pm", "image_id": background, "seconds": 20},
              {"kind": "web", "url": "https://status.example.com/board"},
              {"kind": "announcements"}]
    r = client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "seconds": 8, "slides": slides})
    assert r.status_code == 200, r.text
    assert r.json()["shows"][0]["slides"][0]["subfolders"] is False      # stored with its defaults

    queue = _hub(client).listen(lobby["screen"])          # the TV's open stream hears about changes
    r = client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={
        "idle_minutes": 3, "rules": [{"show_id": show, "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"},
                                      {"show_id": show, "mode": "always"}]})
    assert r.status_code == 200, r.text
    assert queue.get_nowait() == {"action": "signage"}
    listed = next(s for s in r.json()["screens"] if s["id"] == lobby["screen"])
    assert listed["owner"] == "boss" and listed["rules"][0]["mode"] == "idle" and listed["rules"][1]["days"] == list(range(7))
    assert r.json()["shows"][0]["used_by"] == ["Front desk"]

    got = tv.get("/media/screen/signage").json()
    assert got["idle_minutes"] == 3 and len(got["rules"]) == 2
    items = got["shows"][str(show)]["items"]
    assert [(i["type"], i.get("title")) for i in items] == [
        ("image", "a-welcome"), ("image", "b-menu"), ("video", "clip"), ("image", "film"),
        ("text", "Welcome"), ("web", "https://status.example.com/board"), ("notice", "Fire drill")]
    assert items[0]["seconds"] == 8 and items[4]["seconds"] == 20
    assert "<strong>3pm</strong>" in items[6]["html"]

    # Pictures come screen-sized; videos as they are; nothing outside the slideshow.
    big = tv.get(items[0]["src"])
    assert big.status_code == 200 and big.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(big.content)).size == (1440, 1080)
    assert tv.get(items[4]["image"]).status_code == 200
    video = tv.get(items[2]["src"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert tv.get(f"/media/screen/signage/file/{_file_id(conn, 'c-last-year.jpg')}").status_code == 404
    assert TestClient(client.app).get(items[0]["src"]).status_code == 401        # the key, nothing else
    assert clip in {int(i["src"].split("/")[-1].split("?")[0]) for i in items if i["type"] == "video"}

    # With its subfolders, and without clips.
    slides[0] |= {"subfolders": True, "videos": False}
    client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "slides": slides[:1]})
    items = tv.get("/media/screen/signage").json()["shows"][str(show)]["items"]
    assert [i["title"] for i in items] == ["a-welcome", "b-menu", "c-last-year", "film"]
    assert tv.get(items[2]["src"]).status_code == 200

    # Removing the slideshow takes it off the screens' rules.
    r = client.delete(f"/api/admin/signage/shows/{show}")
    assert r.json()["shows"] == [] and tv.get("/media/screen/signage").json()["rules"] == []
    assert conn.execute("SELECT count(*) FROM access_log WHERE action LIKE 'signage.%'").fetchone()[0] == 5
    _hub(client).unlisten(lobby["screen"], queue)


def test_slides_and_rules_are_checked(client, conn, directory, settings, lobby):
    show = client.post("/api/admin/signage/shows", json={"name": "Lobby"}).json()["created"]
    assert client.post("/api/admin/signage/shows", json={"name": " lobby "}).status_code == 422   # taken
    assert client.post("/api/admin/signage/shows", json={"name": "  "}).status_code == 422
    film = _as_video(conn, settings, "film.jpg", "film.mkv", playback="transcode")

    def save(*slides):
        return client.put(f"/api/admin/signage/shows/{show}", json={"name": "Lobby", "slides": list(slides)})
    for bad, says in [({"kind": "web", "url": "javascript:alert(1)"}, "https://"),
                      ({"kind": "folder", "folder_id": 99999}, "isn't in the Media library"),
                      ({"kind": "video", "file_id": film}, "needs converting"),
                      ({"kind": "text", "background": "red"}, "Slide 2"),
                      ({"kind": "clock"}, "Slide 2"),
                      ({"kind": "text", "image_id": film}, "background picture")]:
        r = save({"kind": "announcements"}, bad)
        assert r.status_code == 422 and says in r.json()["detail"], (bad, r.text)

    def rules(*rules):
        return client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={"rules": list(rules)})
    assert rules({"show_id": 999}).status_code == 422
    assert rules({"show_id": show, "days": [7]}).status_code == 422
    assert rules({"show_id": show, "start": "25:00"}).status_code == 422
    assert rules({"show_id": show, "mode": "sometimes"}).status_code == 422
    assert client.put("/api/admin/signage/screens/999", json={"rules": []}).status_code == 422

    # Only admins.
    _login(client, conn, directory, "jane", "jsmith")
    assert client.get("/api/admin/signage").status_code == 403
    assert client.post("/api/admin/signage/shows", json={"name": "Mine"}).status_code == 403


def test_pairing_a_screen_again_keeps_its_signage(client, conn, lobby):
    show = client.post("/api/admin/signage/shows", json={"name": "Lobby"}).json()["created"]
    client.put(f"/api/admin/signage/screens/{lobby['screen']}", json={"rules": [{"show_id": show}]})
    queue = _hub(client).listen(lobby["screen"])          # the TV on the old key
    again, key = _pair(client, "front desk")
    assert again == lobby["screen"] and queue.get_nowait() == {"action": "unpaired"}
    assert lobby["tv"].get("/media/screen/signage").status_code == 401        # the old key stopped
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    assert tv.get("/media/screen/signage").json()["rules"][0]["show_id"] == show
    _hub(client).unlisten(lobby["screen"], queue)
