"""SoundCloud in Media (media/soundcloud.py, web/soundcloud.py), against a stand-in for SoundCloud's API."""

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mnemerynne.media import soundcloud as sc
from test_media import (_hub, _login, _pair, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings, _no_network)

CREDS = ("the-id", "the-secret")
USER = "soundcloud:users:1"
OTHER = "soundcloud:users:2"
ALBUM = "soundcloud:playlists:7"
PLAYLIST = "soundcloud:playlists:8"
CDN = "https://playback.media-streaming.soundcloud.cloud/abc/aac_160k/x/playlist.m3u8"


def track(n, access="playable"):
    return {"kind": "track", "id": n, "urn": f"soundcloud:tracks:{n}", "title": f"Song {n}", "duration": 90000, "access": access,
            "artwork_url": f"https://i1.sndcdn.com/artworks-{n}-large.jpg",
            "permalink_url": f"https://soundcloud.com/aedith/song-{n}?utm_source=api",
            "user": {"username": "Aedith", "permalink_url": "https://soundcloud.com/aedith",
                     "avatar_url": "https://i1.sndcdn.com/avatars-1-large.jpg"}}


def a_set(urn, kind):
    return {"kind": "playlist", "urn": urn, "title": f"Set {kind}", "playlist_type": kind, "track_count": 2,
            "permalink_url": "https://soundcloud.com/aedith/sets/x", "user": {"username": "Aedith"}}


class Fake:
    """Answers like api.soundcloud.com; counts what it was asked and who was sent the token."""

    def __init__(self):
        self.tokens = 0
        self.calls = []
        self.authorized = []                                 # hosts that were sent an Authorization header
        self.next_href = None                                # a first page's next_href, when a test wants one
        self.status = {}                                     # path -> status to answer with instead
        self.headers = {}
        self.expires_in = 3600
        self.expires = str(int(time.time()) + 7200)
        self.params = {}                                     # path -> the query it was last asked with

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if request.headers.get("authorization", "").startswith("OAuth"):
            self.authorized.append(host)
        if host == "secure.soundcloud.com":
            self.tokens += 1
            if "token" in self.status:
                return httpx.Response(self.status["token"], json={})
            assert request.headers["authorization"].startswith("Basic ") and b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": f"token-{self.tokens}", "expires_in": self.expires_in})
        if host != sc.API_HOST:
            return httpx.Response(200, json={"collection": [track(99)]})
        self.calls.append(path)
        self.params[path] = dict(request.url.params)
        if path in self.status:
            return httpx.Response(self.status[path], json={}, headers=self.headers)
        if path == "/tracks":                                # search
            return httpx.Response(200, json={"collection": [track(21), track(22, "blocked")]})
        if path == "/playlists":
            return httpx.Response(200, json={"collection": [a_set(ALBUM, "album")]})
        if path == "/users":
            return httpx.Response(200, json={"collection": [{"kind": "user", "urn": OTHER, "username": "Other", "followers_count": 12,
                                                             "avatar_url": "https://i1.sndcdn.com/avatars-2-large.jpg",
                                                             "permalink_url": "https://soundcloud.com/other"}]})
        if path == f"/users/{OTHER}":
            return httpx.Response(200, json={"kind": "user", "urn": OTHER, "username": "Other", "permalink_url": "https://soundcloud.com/other"})
        if path == f"/users/{OTHER}/tracks":
            return httpx.Response(200, json={"collection": [track(31)]})
        if path == "/tracks/soundcloud:tracks:1/related":    # names the track itself too, which a station leaves out
            return httpx.Response(200, json={"collection": [track(1), track(41), track(42)]})
        if path == "/tracks/soundcloud:tracks:1":
            return httpx.Response(200, json=track(1))
        if path == "/resolve":
            if request.url.params["url"].endswith("/aedith"):
                return httpx.Response(302, headers={"location": f"{sc.API}/users/{USER}"})
            return httpx.Response(404, json={})
        if path == f"/users/{USER}":
            return httpx.Response(200, json={"kind": "user", "urn": USER, "username": "Aedith", "permalink_url": "https://soundcloud.com/aedith"})
        if path == f"/users/{USER}/tracks":
            body = {"collection": [track(1), track(2, "preview"), track(3, "blocked")]}
            if self.next_href:
                body["next_href"] = self.next_href
                self.next_href = None
            return httpx.Response(200, json=body)
        if path == f"/users/{USER}/likes/tracks":
            return httpx.Response(200, json={"collection": [track(4), track(5)]})
        if path == f"/users/{USER}/playlists":
            return httpx.Response(200, json={"collection": [a_set(ALBUM, "album"), a_set(PLAYLIST, "PLAYLIST"), a_set("soundcloud:playlists:9", "ep")]})
        if path == f"/playlists/{ALBUM}":
            return httpx.Response(200, json=a_set(ALBUM, "album"))
        if path == f"/playlists/{ALBUM}/tracks":
            return httpx.Response(200, json={"collection": [track(11), track(12)]})
        if path.endswith("/streams"):
            base = f"{sc.API}/tracks/x/streams"
            return httpx.Response(200, json={"hls_aac_160_url": f"{base}/aac", "hls_mp3_128_url": f"{base}/mp3", "preview_mp3_128_url": f"{base}/prev"})
        if path.endswith("/streams/aac"):
            return httpx.Response(302, headers={"location": f"{CDN}?expires={self.expires}&Signature=s"})
        return httpx.Response(404, json={})


@pytest.fixture
def fake():
    found = Fake()
    sc.TRANSPORT = httpx.MockTransport(found)
    return found


def _lookups(fake):
    return [p for p in fake.calls if p.endswith("/streams")]


@pytest.fixture
def on(client, conn, directory, fake):
    """Signed in as an admin, Media on, the key saved, boss showing the aedith profile."""
    _login(client, conn, directory)
    _turn_on(client)
    assert client.put("/api/admin/settings/media", json={"soundcloud_id": "the-id", "soundcloud_secret": "the-secret"}).status_code == 200
    assert client.put("/api/media/soundcloud/profile", json={"profile": "aedith"}).json() == {"profile": "aedith"}
    return client


# ------------------------------------------------------------- profile names
@pytest.mark.parametrize("typed", ["aedith", " aedith ", "soundcloud.com/aedith", "https://soundcloud.com/aedith",
                                   "http://www.soundcloud.com/aedith/", "https://m.soundcloud.com/aedith/likes",
                                   "https://soundcloud.com/aedith/sets?x=1"])
def test_a_profile_is_reduced_to_its_name(typed):
    assert sc.clean_profile(typed) == "aedith"


@pytest.mark.parametrize("typed", ["soundcloud.com@evil.com", "https://soundcloud.com@evil.com/aedith", "https://evil.com/aedith",
                                   "https://soundcloud.com.evil.com/aedith", "https://soundcloud.com:8443/aedith",
                                   "https://on.soundcloud.com/abc", "ftp://soundcloud.com/aedith", "../../users/1",
                                   "https://soundcloud.com/aedith/some-track", "discover", "a b", "https://soundcloud.com/"])
def test_anything_else_is_refused(typed):
    with pytest.raises(sc.SoundCloudError):
        sc.clean_profile(typed)


def test_an_empty_profile_clears_it():
    assert sc.clean_profile("  ") == ""


# ------------------------------------------------------------- the token
def test_one_token_is_reused_and_a_refused_one_replaced_once(fake):
    sc.profile(CREDS, "aedith")
    sc.listing(CREDS, USER, "likes")
    assert fake.tokens == 1
    sc._token["value"] = "token-1"
    fake.status[f"/users/{USER}/tracks"] = 401
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.listing(CREDS, USER, "tracks")
    assert exc.value.status == 503 and fake.tokens == 2          # one new try, not a loop


def test_many_at_once_share_one_token(fake):
    done = []
    threads = [threading.Thread(target=lambda: done.append(sc.profile(CREDS, "aedith"))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(done) == 8 and fake.tokens == 1


def test_a_bad_key_stops_asking_for_tokens(fake):
    fake.status["token"] = 401
    for _ in range(3):
        with pytest.raises(sc.SoundCloudError) as exc:
            sc.profile(CREDS, "aedith")
        assert exc.value.status == 503 and "key" in str(exc.value)
    assert fake.tokens == 1


def test_the_token_goes_only_to_the_api_even_when_a_page_points_elsewhere(fake):
    fake.next_href = "https://evil.example/steal?page=2"
    out = sc.listing(CREDS, USER, "tracks")
    assert [t["title"] for t in out["items"]] == ["Song 1", "Song 2"] and out["next_offset"] is None
    assert set(fake.authorized) == {sc.API_HOST}


def test_a_redirect_off_the_api_isnt_followed():
    sc.TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600}) if r.url.host == "secure.soundcloud.com"
                                       else httpx.Response(302, headers={"location": "https://evil.example/"}))
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.profile(CREDS, "aedith")
    assert exc.value.status == 502


# ------------------------------------------------------------- lists
def test_lists_never_look_up_streams_and_blocked_tracks_are_left_out(fake):
    out = sc.listing(CREDS, USER, "tracks")
    assert out["key"] == f"user:{USER}:tracks" and out["blocked"] == 1 and _lookups(fake) == []
    first, second = out["items"]
    assert first == {"id": "soundcloud:tracks:1", "source": "soundcloud", "title": "Song 1", "artist": "Aedith", "album": None,
                     "duration": 90.0, "art": "https://i1.sndcdn.com/artworks-1-t500x500.jpg", "preview": False,
                     "link": "https://soundcloud.com/aedith/song-1", "artist_link": "https://soundcloud.com/aedith"}
    assert second["preview"] is True


def test_albums_and_playlists_are_told_apart(fake):
    assert [s["id"] for s in sc.listing(CREDS, USER, "albums")["items"]] == [ALBUM, "soundcloud:playlists:9"]
    assert [s["id"] for s in sc.listing(CREDS, USER, "playlists")["items"]] == [PLAYLIST]
    assert fake.calls.count(f"/users/{USER}/playlists") == 1     # one list from SoundCloud, read once


def test_a_list_is_reused_then_stands_in_when_soundcloud_is_limiting(fake):
    sc.listing(CREDS, USER, "likes")
    sc.listing(CREDS, USER, "likes")
    assert fake.calls.count(f"/users/{USER}/likes/tracks") == 1
    entry = next(iter(sc._lists.values()))
    entry.at -= sc.LIST_SECONDS + 1                               # gone stale, but not too stale
    fake.status[f"/users/{USER}/likes/tracks"] = 503
    assert len(sc.listing(CREDS, USER, "likes")["items"]) == 2
    entry.at -= sc.STALE_SECONDS
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.listing(CREDS, USER, "likes")
    assert exc.value.status == 502


def test_an_address_that_isnt_soundclouds_never_reaches_the_page():
    assert sc._link("https://evil.example/x") is None and sc._link("javascript:alert(1)") is None
    assert sc._art("https://evil.example/x-large.jpg") is None
    assert sc._link("https://soundcloud.com/a/b?utm=1") == "https://soundcloud.com/a/b"


# ------------------------------------------------------------- playing
def test_a_track_signs_once_and_hands_the_browser_the_cdn(fake):
    out = sc.stream(CREDS, "soundcloud:tracks:1")
    assert out["url"].startswith(CDN) and out["mode"] == "hls" and out["session"] is None and out["start"] == 0 and out["preview"] is False
    sc.stream(CREDS, "soundcloud:tracks:1")
    assert len(_lookups(fake)) == 1                              # the same signed link until it nearly runs out
    assert set(fake.authorized) == {sc.API_HOST}                 # the CDN's address goes to the browser, unfetched


def test_a_link_that_failed_in_the_browser_isnt_handed_back(on, fake):
    body = {"id": "soundcloud:tracks:1"}
    on.post("/api/media/soundcloud/stream", json=body)
    on.post("/api/media/soundcloud/stream", json=body)
    assert len(_lookups(fake)) == 1                              # an ordinary repeat still reuses it
    assert on.post("/api/media/soundcloud/stream", json={**body, "fresh": True}).status_code == 200
    assert len(_lookups(fake)) == 2


def test_a_link_about_to_run_out_isnt_reused(fake):
    fake.expires = str(int(time.time()) + sc.STREAM_MARGIN - 5)
    sc.stream(CREDS, "soundcloud:tracks:1")
    sc.stream(CREDS, "soundcloud:tracks:1")
    assert len(_lookups(fake)) == 2


def test_the_daily_limit_is_kept_below_soundclouds(fake, monkeypatch):
    monkeypatch.setattr(sc, "STREAM_STOP", 2)
    sc.stream(CREDS, "soundcloud:tracks:1")
    sc.stream(CREDS, "soundcloud:tracks:2")
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.stream(CREDS, "soundcloud:tracks:3")
    assert exc.value.status == 429 and "hours" in str(exc.value) and len(_lookups(fake)) == 2


def test_a_429_stops_every_call_until_the_reset(fake):
    fake.status[f"/users/{USER}/likes/tracks"] = 429
    fake.headers = {"x-ratelimit-reset": str(int(time.time()) + 600)}
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.listing(CREDS, USER, "likes")
    assert exc.value.status == 429 and "minutes" in str(exc.value)
    fake.calls.clear()
    for call in (lambda: sc.profile(CREDS, "aedith"), lambda: sc.stream(CREDS, "soundcloud:tracks:1")):
        with pytest.raises(sc.SoundCloudError):
            call()
    assert fake.calls == []


def test_a_blocked_track_is_refused_plainly(fake):
    fake.status["/tracks/soundcloud:tracks:3/streams"] = 403
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.stream(CREDS, "soundcloud:tracks:3")
    assert exc.value.status == 403


# ---------------------------------------------------------------- the API
def test_the_page_gets_a_profile_its_lists_and_a_set(on, fake):
    me = on.get("/api/media/soundcloud/profile").json()
    assert me["configured"] and me["source"] == "own" and me["editable"] and me["user"]["urn"] == USER and me["error"] is None
    assert [t["id"] for t in on.get("/api/media/soundcloud/list/likes").json()["items"]] == ["soundcloud:tracks:4", "soundcloud:tracks:5"]
    got = on.get(f"/api/media/soundcloud/set/{ALBUM}").json()
    assert got["album"] is True and got["key"] == f"set:{ALBUM}" and [t["title"] for t in got["tracks"]] == ["Song 11", "Song 12"]
    assert on.get("/api/media/soundcloud/set/..%2F..%2Fme").status_code == 404
    played = on.post("/api/media/soundcloud/stream", json={"id": "soundcloud:tracks:4"})
    assert played.status_code == 200 and played.json()["url"].startswith(CDN)
    assert on.post("/api/media/soundcloud/stream", json={"id": "../../me"}).status_code == 422


def test_an_unknown_profile_can_be_fixed_right_there(on, fake):
    assert on.put("/api/media/soundcloud/profile", json={"profile": "nobody"}).status_code == 200
    me = on.get("/api/media/soundcloud/profile").json()
    assert me["user"] is None and "nobody" in me["error"] and me["editable"]
    r = on.get("/api/media/soundcloud/list/tracks")
    assert r.status_code == 422 and "nobody" in r.json()["detail"]     # not 404: the page hides a 404's words
    assert on.put("/api/media/soundcloud/profile", json={"profile": "https://evil.com/x"}).status_code == 422


def test_without_a_key_the_tab_says_so(client, conn, directory, fake):
    _login(client, conn, directory)
    _turn_on(client)
    client.put("/api/media/soundcloud/profile", json={"profile": "aedith"})
    assert client.get("/api/media/soundcloud/profile").json()["configured"] is False
    r = client.get("/api/media/soundcloud/list/tracks")
    assert r.status_code == 503 and "isn't set up" in r.json()["detail"] and fake.tokens == 0


def test_the_key_never_comes_back_or_reaches_the_log(on, conn):
    answer = on.get("/api/admin/settings/media").text
    assert "the-secret" not in answer and "the-id" not in answer and json.loads(answer)["soundcloud_set"] is True
    logged = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM access_log"))
    assert "the-secret" not in logged and "the-id" not in logged and "SoundCloud key changed" in logged
    # Saving only the secret still saves.
    assert on.put("/api/admin/settings/media", json={"soundcloud_secret": "new-secret"}).status_code == 200
    from mnemerynne import media
    stored = media.load_settings(conn)
    assert media.soundcloud_credentials(on.app.state.settings, stored) == ("the-id", "new-secret")
    assert "new-secret" not in json.dumps(stored)
    assert on.put("/api/admin/settings/media", json={"soundcloud_id": "", "soundcloud_secret": ""}).json()["soundcloud_set"] is False


def test_the_token_never_reaches_a_browser(on, fake):
    for url in ("/api/media/soundcloud/profile", "/api/media/soundcloud/list/tracks", f"/api/media/soundcloud/set/{ALBUM}"):
        assert "token-1" not in on.get(url).text
    assert "token-1" not in on.post("/api/media/soundcloud/stream", json={"id": "soundcloud:tracks:1"}).text


# ------------------------------------------------------------------ search
def test_a_search_finds_tracks_without_looking_up_streams(fake):
    out = sc.search(CREDS, "tracks", "  some   song ")
    assert out["kind"] == "tracks" and out["key"] == "search:some song" and out["blocked"] == 1 and _lookups(fake) == []
    assert [t["id"] for t in out["items"]] == ["soundcloud:tracks:21"]
    assert fake.params["/tracks"]["q"] == "some song" and fake.params["/tracks"]["linked_partitioning"] == "true"


def test_a_search_is_reused_whatever_its_case(fake):
    sc.search(CREDS, "tracks", "Song")
    sc.search(CREDS, "tracks", "sONG")
    assert fake.calls.count("/tracks") == 1


def test_a_search_finds_playlists_and_profiles(fake):
    sets = sc.search(CREDS, "playlists", "x")
    assert sets["kind"] == "sets" and sets["key"] is None and [s["id"] for s in sets["items"]] == [ALBUM]
    assert fake.params["/playlists"]["show_tracks"] == "false"
    people = sc.search(CREDS, "users", "x")
    assert people["kind"] == "users" and people["key"] is None
    assert people["items"] == [{"id": OTHER, "name": "Other", "art": "https://i1.sndcdn.com/avatars-2-t500x500.jpg", "followers": 12,
                                "link": "https://soundcloud.com/other"}]


def test_a_searchs_words_stay_a_query_value(fake):
    sc.search(CREDS, "tracks", "../../me?x=1&y=2")
    assert "/tracks" in fake.calls and fake.params["/tracks"]["q"] == "../../me?x=1&y=2" and set(fake.authorized) == {sc.API_HOST}


@pytest.mark.parametrize("typed", ["", "    ", "\n\t"])
def test_an_empty_search_is_refused_without_asking(fake, typed):
    with pytest.raises(sc.SoundCloudError) as exc:
        sc.search(CREDS, "tracks", typed)
    assert exc.value.status == 422 and fake.calls == [] and fake.tokens == 0


def test_the_page_searches_and_opens_a_profile(on, fake):
    found = on.get("/api/media/soundcloud/search/tracks", params={"q": "song"})
    assert found.status_code == 200 and found.json()["key"] == "search:song"
    assert on.get("/api/media/soundcloud/search/tracks", params={"q": ""}).status_code == 422
    assert on.get("/api/media/soundcloud/search/albums", params={"q": "x"}).status_code == 422
    assert on.get(f"/api/media/soundcloud/user/{OTHER}").json()["name"] == "Other"
    assert on.get("/api/media/soundcloud/user/..%2F..%2Fme").status_code == 404
    tab = on.get("/api/media/soundcloud/list/tracks", params={"account": OTHER}).json()
    assert tab["key"] == f"user:{OTHER}:tracks" and [t["title"] for t in tab["items"]] == ["Song 31"]
    assert on.get("/api/media/soundcloud/list/tracks", params={"account": "../../me"}).status_code == 404
    assert on.get("/api/media/soundcloud/profile").json()["profile"] == "aedith"        # opening someone else's changes nothing


def test_search_works_with_no_saved_profile(on, fake):
    on.put("/api/media/soundcloud/profile", json={"profile": ""})
    assert on.get("/api/media/soundcloud/search/tracks", params={"q": "song"}).status_code == 200


# ---------------------------------------------------------------- stations
def test_a_station_is_a_tracks_relatives_without_it_or_any_stream_lookup(fake):
    out = sc.related(CREDS, "soundcloud:tracks:1")
    assert out["key"] == "related:soundcloud:tracks:1" and [t["id"] for t in out["tracks"]] == ["soundcloud:tracks:41", "soundcloud:tracks:42"]
    assert fake.params["/tracks/soundcloud:tracks:1/related"]["access"] == "playable" and _lookups(fake) == []   # no 30-second previews


def test_the_page_starts_a_station(on, fake):
    got = on.get("/api/media/soundcloud/station/soundcloud:tracks:1")
    assert got.status_code == 200 and len(got.json()["tracks"]) == 2
    assert on.get("/api/media/soundcloud/station/..%2F..%2Fme").status_code == 404


# ------------------------------------------------------------- whose profile
def _tv(client, key):
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    return tv


def test_a_screen_shows_its_own_profile_else_its_owners(on, fake):
    screen_id, key = _pair(on)
    tv = _tv(on, key)
    me = tv.get("/api/media/soundcloud/profile?screen=1").json()
    assert me["source"] == "owner" and me["profile"] == "aedith" and me["editable"] is False
    assert on.put(f"/api/media/screens/{screen_id}/soundcloud", json={"profile": "soundcloud.com/other"}).json() == {"profile": "other"}
    assert tv.get("/api/media/soundcloud/profile?screen=1").json()["source"] == "screen"
    assert tv.get("/api/media/soundcloud/profile?screen=1").json()["profile"] == "other"
    assert [s["soundcloud"] for s in on.get("/api/media/screens").json()["screens"]] == ["other"]


def test_a_paired_computer_is_its_person_except_on_the_tv_page(on, fake):
    screen_id, _ = _pair(on)                                     # this browser now has a session and the screen's key
    on.put(f"/api/media/screens/{screen_id}/soundcloud", json={"profile": "other"})
    assert on.get("/api/media/soundcloud/profile").json()["profile"] == "aedith"
    assert on.get("/api/media/soundcloud/profile?screen=1").json()["profile"] == "other"


def test_other_peoples_screens_are_refused(on, conn, directory, fake):
    screen_id, _ = _pair(on)
    directory_user = TestClient(on.app)
    _login(directory_user, conn, directory, "jsmith", "jsmith")
    assert directory_user.put(f"/api/media/screens/{screen_id}/soundcloud", json={"profile": "x"}).status_code == 404


# ----------------------------------------------------------------- casting
def test_casting_rebuilds_the_list_on_the_server(on, fake):
    screen_id, _ = _pair(on)
    queue = _hub(on).listen(screen_id)                           # the TV's open stream
    r = on.post(f"/api/media/screens/{screen_id}/command",
                json={"action": "play_soundcloud", "key": f"user:{USER}:likes", "track": "soundcloud:tracks:5"})
    assert r.status_code == 200, r.text
    sent = queue.get_nowait()
    assert sent["action"] == "play_soundcloud" and [t["id"] for t in sent["tracks"]] == ["soundcloud:tracks:5"] and sent["index"] == 0
    assert _lookups(fake) == []                                  # the TV asks as each one starts
    for bad in ("user:soundcloud:users:1:albums", "https://evil.example", "set:../../me", ""):
        refused = on.post(f"/api/media/screens/{screen_id}/command", json={"action": "play_soundcloud", "key": bad})
        assert refused.status_code == 422, bad
    gone = on.post(f"/api/media/screens/{screen_id}/command",
                   json={"action": "play_soundcloud", "key": f"user:{USER}:likes", "track": "soundcloud:tracks:404"})
    assert gone.status_code == 404


def test_a_station_cast_to_a_screen_starts_with_its_track_and_says_so(on, fake):
    screen_id, _ = _pair(on)
    queue = _hub(on).listen(screen_id)
    r = on.post(f"/api/media/screens/{screen_id}/command", json={"action": "play_soundcloud", "key": "related:soundcloud:tracks:1"})
    assert r.status_code == 200, r.text
    sent = queue.get_nowait()
    assert sent["station"] is True and [t["id"] for t in sent["tracks"]] == ["soundcloud:tracks:1", "soundcloud:tracks:41", "soundcloud:tracks:42"]
    assert _lookups(fake) == []


def test_a_search_cast_to_a_screen_is_rebuilt_from_its_words(on, fake):
    screen_id, _ = _pair(on)
    queue = _hub(on).listen(screen_id)
    r = on.post(f"/api/media/screens/{screen_id}/command",
                json={"action": "play_soundcloud", "key": "search:some song", "track": "soundcloud:tracks:21"})
    assert r.status_code == 200, r.text
    sent = queue.get_nowait()
    assert sent["station"] is False and [t["id"] for t in sent["tracks"]] == ["soundcloud:tracks:21"]
    assert fake.params["/tracks"]["q"] == "some song"
    for bad in ("related:../../me", "related:soundcloud:users:1", "search:", "search:   ", "search:" + "x" * 101):
        assert on.post(f"/api/media/screens/{screen_id}/command", json={"action": "play_soundcloud", "key": bad}).status_code == 422, bad


def test_what_a_screen_reports_about_soundcloud_isnt_written_down(on, conn, fake, media_conn):
    screen_id, key = _pair(on)
    tv = _tv(on, key)
    r = tv.put("/api/media/screen/state", json={"kind": "audio", "title": "Song 1", "sub": "Aedith", "external": True})
    assert r.status_code == 200, r.text
    assert _hub(on).state(screen_id)["title"] == "Song 1"        # in memory, for the phone's remote
    row = media_conn.execute("SELECT last_state FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
    assert "Song 1" not in row["last_state"] and "Aedith" not in row["last_state"] and json.loads(row["last_state"])["title"] == "SoundCloud"


def test_kiosk_settings_change_one_thing_at_a_time(on, conn, directory, fake):
    screen_id, key = _pair(on)
    tv = _tv(on, key)
    started = tv.post("/media/screen/kiosk/unlock").json()
    assert on.post("/api/media/kiosk/approve", json={"code": started["code"]}).status_code in (200, 204)
    grant = tv.get("/media/screen/kiosk/unlock", params={"secret": started["secret"]}).json()["grant"]
    box = TestClient(on.app)
    session = box.post("/api/media/kiosk/redeem", json={"grant": grant}).json()
    headers = {"X-Kiosk-Session": session["session"]}
    r = box.put("/api/media/kiosk/screen", json={"soundcloud_profile": "https://soundcloud.com/other/"}, headers=headers)
    assert r.status_code == 200 and r.json()["screen"]["soundcloud_profile"] == "other" and r.json()["screen"]["top_bar"] is False
    r = box.put("/api/media/kiosk/screen", json={"top_bar": True}, headers=headers)      # what an older box's helper sends
    assert r.json()["screen"] == {"id": screen_id, "name": "Living room", "top_bar": True, "soundcloud_profile": "other"}
    assert box.put("/api/media/kiosk/screen", json={"soundcloud_profile": "https://evil.com/x"}, headers=headers).status_code == 422
