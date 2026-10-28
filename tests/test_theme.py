"""Themes (mnemerynne/theme.py, web/theme.py): the built-ins, tweaking them, custom themes from a base,
theme packs, branding, who gets what, and what every page draws."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fakes import FakeEmbedder
from mnemerynne import theme, users
from mnemerynne.web.app import Services, create_app


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


def _login(client, name):
    return client.post("/login", data={"username": name, "password": "password123"}, follow_redirects=False)


@pytest.fixture
def people(conn):
    users.create_user(conn, "boss", "password123", role="admin")
    users.create_user(conn, "alice", "password123")


def _png(size=(300, 120), color=(200, 30, 90, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _html_tag(page: str) -> str:
    return page[page.index("<html"):page.index(">", page.index("<html")) + 1]


def test_the_built_ins_are_seeded_and_stock(settings, conn, people):
    slugs = [(t["slug"], t["base"], t["builtin"], t["enabled"]) for t in theme.list_themes(conn)]
    assert slugs == [("sakura", "sakura", True, True), ("neon", "neon", True, True),
                     ("corporate", "plain", True, False), ("sadgirlsclub", "neon", True, False)]
    assert theme.css(theme.get(conn, "neon")) == ""                      # stock: nothing to generate
    assert theme.get(conn, "sadgirlsclub")["config"]["fonts"]["heading"] == "vcr-osd-mono"   # the preset fills in
    with TestClient(_app(settings)) as c:
        login = c.get("/login").text
        assert 'data-theme="neon" data-look="neon"' in _html_tag(login)   # the default, as before
        assert "theme-plain.css" in login and "theme-effects.css" in login and 'id="look-css"' not in login
        assert c.get("/theme/neon.css").status_code == 404 and c.get("/theme/nothere.css").status_code == 404
        _login(c, "alice")
        assert c.get("/api/admin/themes").status_code == 403
        assert c.put("/api/admin/themes/neon", json={"enabled": False}).status_code == 403
        assert c.put("/api/me/theme", json={"theme": "corporate"}).status_code == 422   # off: not on offer
        assert c.put("/api/me/theme", json={"theme": "vaporwave"}).status_code == 422
        prefs = c.get("/preferences").text
        assert prefs.count('class="theme-card"') == 2 and 'value="sakura"' in prefs and 'value="neon"' in prefs


def test_tweaking_neon_and_resetting_it(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        status = c.get("/api/admin/themes").json()
        neon = next(t for t in status["themes"] if t["slug"] == "neon")
        assert neon["effective"]["dark"]["primary"] == "#ff2e97" and neon["effective"]["light"] is None
        draft = neon["effective"]
        draft["dark"]["primary"] = "#39FF14"
        draft["neon"]["bloom"] = "#39ff14"
        r = c.put("/api/admin/themes/neon", json={"config": draft})
        assert r.status_code == 200
        fresh = next(t for t in r.json()["themes"] if t["slug"] == "neon")
        assert fresh["config"] == {"dark": {"primary": "#39ff14"}, "neon": {"bloom": "#39ff14"}}   # only what differs
        assert fresh["version"] == 2 and fresh["has_css"]
        page = c.get("/files").text
        assert "/theme/neon.css?v=2" in page and page.index("theme-neon.css") < page.index("/theme/neon.css?v=2")
        css = c.get("/theme/neon.css?v=2")
        assert css.status_code == 200 and css.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert 'html[data-look="neon"]' in css.text and "--rose: #39ff14;" in css.text and "--glow: 57, 255, 20;" in css.text
        assert "--bloom:" in css.text and "@media" not in css.text          # neon: one palette
        # An untouched save keeps it stock-less, and Reset drops the changes.
        assert c.post("/api/admin/themes/neon/reset").status_code == 200
        assert theme.get(conn, "neon")["config"] == {} and 'id="look-css"' not in c.get("/files").text
        assert c.put("/api/admin/themes/neon", json={"config": theme.effective("neon", {})}).status_code == 200
        assert theme.get(conn, "neon")["config"] == {}
        log = [tuple(r) for r in conn.execute("SELECT action, target FROM access_log WHERE action = 'settings.theme'")]
        assert ("settings.theme", "neon") in log


def test_a_custom_theme_from_sakura_with_both_palettes(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        r = c.post("/api/admin/themes", json={"name": "Acme Blue", "base": "sakura"})
        assert r.status_code == 201 and r.json()["slug"] == "acmeblue"
        config = {"light": {"primary": "#123456", "gradients": {"bar": {"angle": 90, "stops": [{"color": "#123456", "at": 0}, {"color": "#345678", "at": 100}]}}},
                  "dark": {"primary": "#89abcd", "page": "#101820"},
                  "radius": 4, "scene": "none", "border": "hard", "glass": False,
                  "fonts": {"heading": "geometric", "body": "mono"}, "custom_css": "h1 { letter-spacing: 1px; }"}
        assert c.put("/api/admin/themes/acmeblue", json={"config": config}).status_code == 200
        css = c.get("/theme/acmeblue.css?v=2").text
        assert "--rose: #123456;" in css and "linear-gradient(90deg, #123456 0%, #345678 100%)" in css
        assert "--brand-bar-fg: #ffffff;" in css                                  # white reads on a dark bar
        assert "@media (prefers-color-scheme: dark)" in css and "--rose: #89abcd;" in css
        assert "--brand-bar-bg: var(--glass);" in css                             # dark goes back to solid
        assert "--radius: 4px;" in css and "Avenir" in css and "monospace" in css and "letter-spacing: 1px" in css
        # alice picks it: the page carries its base, slug and attributes; the cookie follows for the sign-in page.
        _login(c, "alice")
        r = c.put("/api/me/theme", json={"theme": "acmeblue"})
        assert r.status_code == 200 and r.cookies.get("erynne_theme") == "acmeblue"
        tag = _html_tag(c.get("/files").text)
        assert 'data-theme="sakura" data-look="acmeblue"' in tag and 'data-scene="none"' in tag
        assert 'data-border="hard"' in tag and "data-flat" in tag and 'data-bar-bg' in tag
        assert 'content="light dark"' in c.get("/files").text
        c.post("/logout")
        assert 'data-look="acmeblue"' in _html_tag(c.get("/login").text)
        # Duplicate keeps the look under a new slug; delete sends alice back to the default.
        _login(c, "boss")
        r = c.post("/api/admin/themes/acmeblue/duplicate", json={"name": "Acme Blue"})
        assert r.status_code == 201 and r.json()["slug"] == "acmeblue2"
        assert theme.get(conn, "acmeblue2")["config"]["light"]["primary"] == "#123456"
        assert c.delete("/api/admin/themes/acmeblue").status_code == 200
        assert c.delete("/api/admin/themes/neon").status_code == 422           # built-ins stay
        _login(c, "alice")
        assert 'data-look="neon"' in _html_tag(c.get("/files").text)


def test_validation(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        bad = [
            ({"light": {"primary": "red"}}, "colour"),
            ({"light": {"gradients": {"page": {"angle": 10, "stops": [{"color": "#000", "at": 0}] * 5}}}}, "stops"),
            ({"custom_css": "@import url(http://evil)"}, "@import"),
            ({"radius": 99}, "radius"),
            ({"fonts": {"heading": "comic"}}, "font"),
        ]
        for config, word in bad:
            r = c.put("/api/admin/themes/sakura", json={"config": config})
            assert r.status_code == 422 and word.lower() in r.json()["detail"].lower(), (config, r.text)
        assert c.post("/api/admin/themes", json={"name": "plain", "base": "sakura"}).json()["slug"] == "plain2"
        assert c.post("/api/admin/themes", json={"name": "X", "base": "mdf"}).status_code == 422
        assert c.put("/api/admin/themes/settings", json={"default": "corporate"}).status_code == 422   # off
        assert c.put("/api/admin/themes/neon", json={"enabled": False}).status_code == 422   # it is the default
        assert c.post("/api/admin/themes/preview", json={"slug": "sakura", "config": {"light": {"primary": "#00ff00"}}}).json()["css"].count("--rose: #00ff00;") == 1


def test_theme_packs_download_and_upload(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        c.post("/api/admin/themes", json={"name": "Packed", "base": "plain"})
        c.put("/api/admin/themes/packed", json={"config": {"light": {"primary": "#aa0000"}, "scene": "image"}})
        r = c.post("/api/admin/themes/packed/assets/background", files={"file": ("bg.png", _png((900, 500)), "image/png")})
        assert r.status_code == 200
        assert (settings.theme_dir / "packed" / "background.jpg").is_file()         # re-encoded as JPEG
        assert c.get("/theme/packed/background").status_code == 200
        assert theme.attrs(theme.get(conn, "packed"))["data-scene"] == "image"
        pack = c.get("/api/admin/themes/packed/pack")
        assert pack.headers["content-disposition"] == 'attachment; filename="packed.theme.json"'
        data = pack.json()
        assert data["format"] == theme.PACK_FORMAT and data["config"]["light"]["primary"] == "#aa0000"
        assert "assets" not in data["config"] and data["assets"]["background"]["mime"] == "image/jpeg"
        # Up again: a new theme with the same look and its file in place; a clashing name gets a number.
        r = c.post("/api/admin/themes/import", files={"file": ("packed.theme.json", pack.content, "application/json")})
        assert r.status_code == 201 and r.json()["slug"] == "packed2"
        assert (settings.theme_dir / "packed2" / "background.jpg").is_file()
        assert theme.css(theme.get(conn, "packed2")) .count("--rose: #aa0000;") == 1
        assert "/theme/packed2/background?v=" in theme.css(theme.get(conn, "packed2"))
        # Refusals say why.
        r = c.post("/api/admin/themes/import", files={"file": ("x.json", json.dumps({"format": "other"}).encode(), "application/json")})
        assert r.status_code == 422 and "theme pack" in r.json()["detail"]
        bad = dict(data, assets={"font_heading": {"name": "f.woff2", "mime": "font/woff2", "base64": "AAAA"}})
        r = c.post("/api/admin/themes/import", files={"file": ("x.json", json.dumps(bad).encode(), "application/json")})
        assert r.status_code == 422 and "woff2" in r.json()["detail"]
        r = c.post("/api/admin/themes/import", files={"file": ("x.json", b"not json", "application/json")})
        assert r.status_code == 422


def test_sad_girls_club(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        assert c.put("/api/admin/themes/sadgirlsclub", json={"enabled": True}).status_code == 200
        _login(c, "alice")
        assert c.put("/api/me/theme", json={"theme": "sadgirlsclub"}).status_code == 200
        page = c.get("/files").text
        tag = _html_tag(page)
        assert 'data-theme="neon" data-look="sadgirlsclub"' in tag and 'data-border="ridge"' in tag
        assert 'data-fx="scanlines flicker jitter pulse"' in tag and "data-texture" in tag and "data-flat" in tag
        css = c.get("/theme/sadgirlsclub.css?v=1").text
        assert "--rose: #ff1493;" in css and "--ink: #ffffff;" in css and "--fx: 0.6;" in css
        assert '@font-face { font-family: "VCR OSD Mono"; src: url(/static/looks/sadgirlsclub/vcr-osd-mono.woff)' in css
        assert "--brand-texture: url(/static/looks/sadgirlsclub/panel.png);" in css
        assert "--pulse-a: #0e000f;" in css and "text-transform: uppercase" in css
        assert c.get("/static/looks/sadgirlsclub/vcr-osd-mono.woff").status_code == 200
        assert c.get("/static/looks/sadgirlsclub/panel.png").status_code == 200
        # Tweak it, then Reset brings the preset back.
        _login(c, "boss")
        draft = next(t for t in c.get("/api/admin/themes").json()["themes"] if t["slug"] == "sadgirlsclub")["effective"]
        draft["dark"]["primary"] = "#00ffff"
        c.put("/api/admin/themes/sadgirlsclub", json={"config": draft})
        assert "--rose: #00ffff;" in c.get("/theme/sadgirlsclub.css?v=2").text
        assert "--brand-texture" in c.get("/theme/sadgirlsclub.css?v=2").text    # the bundled texture survives a save
        c.post("/api/admin/themes/sadgirlsclub/reset")
        assert theme.get(conn, "sadgirlsclub")["config"]["dark"]["primary"] == "#ff1493"


def test_corporate_branding_and_enforcing(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        assert c.put("/api/admin/themes/corporate", json={"enabled": True}).status_code == 200
        assert c.put("/api/admin/brand", json={"name": "Acme Hotels", "tagline": "Welcome back", "logo_height": 2.4}).status_code == 200
        r = c.post("/api/admin/brand/assets/logo", files={"file": ("logo.png", _png(), "image/png")})
        assert r.status_code == 200 and r.json()["brand"]["logo"].startswith("/theme/brand/logo?v=")
        r = c.post("/api/admin/brand/assets/mark", files={"file": ("mark.png", _png((256, 256)), "image/png")})
        assert r.status_code == 200
        for name in ("mark-192.png", "mark-512.png", "mark-maskable-512.png", "mark-180.png"):
            assert (settings.theme_dir / "brand" / name).is_file(), name
        with Image.open(settings.theme_dir / "brand" / "mark-maskable-512.png") as im:
            assert im.size == (512, 512)
        manifest = c.get("/manifest.webmanifest").json()
        assert manifest["name"] == "Acme Hotels" and manifest["icons"][0]["src"].startswith("/theme/brand/mark-192.png?v=")
        assert c.get(manifest["icons"][2]["src"]).status_code == 200
        c.post("/logout")
        login = c.get("/login").text
        assert "Acme Hotels" in login and "Welcome back" in login and 'class="brand-logo"' in login and "🌸" not in login
        assert "/theme/brand.css?v=" in login and "--brand-logo-h: 2.4rem" in c.get("/theme/brand.css").text
        assert 'rel="icon" href="/theme/brand/mark-192.png' in login
        assert "<title>Sign in · Acme Hotels</title>" in login
        # Refused uploads.
        _login(c, "boss")
        assert c.post("/api/admin/brand/assets/logo", files={"file": ("x.png", b"MZ\x90\x00" + b"\0" * 100, "image/png")}).status_code == 422
        assert c.post("/api/admin/brand/assets/logo", files={"file": ("big.png", b"\x89PNG" + b"\0" * (2 << 20), "image/png")}).status_code == 422
        bad_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        assert c.post("/api/admin/brand/assets/logo", files={"file": ("l.svg", bad_svg, "image/svg+xml")}).status_code == 422
        good_svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'
        assert c.post("/api/admin/brand/assets/logo_dark", files={"file": ("l.svg", good_svg, "image/svg+xml")}).status_code == 200
        svg = c.get("/theme/brand/logo-dark")
        assert svg.status_code == 200 and svg.headers["content-security-policy"].startswith("default-src 'none'; sandbox")
        assert c.post("/api/admin/themes/sakura/assets/font_body", files={"file": ("f.woff2", b"nope", "font/woff2")}).status_code == 422
        assert c.delete("/api/admin/brand/assets/logo").status_code == 200 and not list((settings.theme_dir / "brand").glob("logo.*"))
        # Enforced: everyone gets Corporate whatever they picked; the picker goes; nothing to pick.
        _login(c, "alice")
        c.put("/api/me/theme", json={"theme": "sakura"})
        _login(c, "boss")
        assert c.put("/api/admin/themes/settings", json={"enforce": "corporate"}).status_code == 200
        _login(c, "alice")
        assert 'data-theme="plain" data-look="corporate"' in _html_tag(c.get("/files").text)
        prefs = c.get("/preferences").text
        assert 'id="theme-enforced"' in prefs and 'class="theme-card"' not in prefs
        assert c.put("/api/me/theme", json={"theme": "sakura"}).status_code == 422
        c.post("/logout")
        assert 'data-look="corporate"' in _html_tag(c.get("/login").text)
        _login(c, "boss")
        assert c.put("/api/admin/themes/settings", json={"enforce": ""}).status_code == 200
        assert c.put("/api/admin/themes/settings", json={"default": "corporate"}).status_code == 200
        c.post("/logout")
        assert 'data-look="sakura"' in _html_tag(c.get("/login").text)        # signed out: this browser's last pick
        c.cookies.delete("erynne_theme")
        assert 'data-look="corporate"' in _html_tag(c.get("/login").text)     # a fresh browser: the default
        _login(c, "alice")
        assert 'data-look="sakura"' in _html_tag(c.get("/files").text)        # her own choice still counts
        # A theme turned off or hidden can't be picked and falls its people back.
        _login(c, "boss")
        c.put("/api/admin/themes/sakura", json={"pickable": False})
        _login(c, "alice")
        assert 'data-look="corporate"' in _html_tag(c.get("/files").text)
        assert c.put("/api/me/theme", json={"theme": "sakura"}).status_code == 422


def test_the_settings_page_has_themes_between_the_columns_and_experimental(settings, conn, people):
    with TestClient(_app(settings)) as c:
        _login(c, "boss")
        page = c.get("/admin/libraries").text
        assert page.index('id="ax-connectors-title"') < page.index('id="ax-themes-title"') < page.index('id="theme-settings"') \
            < page.index('id="ax-experimental-title"')
        assert "admin_theme.js" in page
