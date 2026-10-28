"""Themes: every look is a row in `themes` (migration 0050). A theme has a base (sakura, neon or plain:
the three bundled stylesheets) and a JSON config of overrides on top of the base's stock values:
palettes (light and dark; neon is always dark), gradients, shape, fonts, scene, effects and custom CSS.
`css()` turns a theme into the stylesheet a page loads last (/theme/<slug>.css), scoped to
html[data-look="<slug>"], so the bundled stylesheets never change. Branding (name, logo, mark) is
organisation-wide and lives in meta 'theme_settings' with the default and enforced theme. Each theme's
files (background, texture, fonts) and the brand's live under <data_dir>/theme/."""

from __future__ import annotations

import base64
import copy
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mnemerynne.config import Settings


class ThemeError(ValueError):
    """Something the admin can fix: shown as a 422."""


BASES = ("sakura", "neon", "plain")
SETTINGS_KEY = "theme_settings"
PACK_FORMAT = "mnemerynne-theme/1"
MAX_CUSTOM_CSS = 20 * 1024
MAX_PACK_BYTES = 16 * 1024 * 1024
MAX_NAME = 60
FALLBACK = "neon"
RESERVED_SLUGS = {"plain", "brand", "import", "preview", "settings"}

# Palette tokens: config key -> the CSS variable the stylesheets read.
TOKENS = (
    ("page", "--blush"), ("primary", "--rose"), ("primary_hover", "--rose-deep"), ("text", "--ink"),
    ("muted", "--muted"), ("surface", "--glass"), ("surface_strong", "--glass-strong"), ("line", "--line"),
    ("highlight", "--petal"), ("highlight_soft", "--petal-soft"), ("error", "--error"),
    ("ok_bg", "--ok-bg"), ("ok_fg", "--ok-fg"), ("bad_bg", "--bad-bg"), ("bad_fg", "--bad-fg"),
    ("busy_bg", "--busy-bg"), ("busy_fg", "--busy-fg"),
)
NEON_TOKENS = (("cyan", "--cyan"), ("fringe_l", "--fringe-l"), ("fringe_r", "--fringe-r"))
GRADIENT_PLACES = (("page", "--brand-page-bg"), ("bar", "--brand-bar-bg"), ("button", "--brand-button-bg"))
EFFECTS = ("scanlines", "flicker", "jitter", "pulse")

# System font stacks: nothing to download.
FONT_STACKS = {
    "system-sans": ("System sans", 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'),
    "system-serif": ("System serif", '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Noto Serif", serif'),
    "condensed": ("Condensed", '"Bahnschrift", "DIN Alternate", "Eurostile", "Roboto Condensed", "Arial Narrow", system-ui, sans-serif'),
    "humanist": ("Humanist", 'Seravek, "Gill Sans Nova", Ubuntu, Calibri, "DejaVu Sans", sans-serif'),
    "geometric": ("Geometric", 'Avenir, Montserrat, Corbel, "URW Gothic", "Century Gothic", sans-serif'),
    "mono": ("Monospace", 'ui-monospace, "Cascadia Code", "Source Code Pro", Menlo, Consolas, monospace'),
}
# Fonts shipped with the app (static/looks/): a key the editor lists next to the stacks.
BUNDLED_FONTS = {
    "vcr-osd-mono": {"name": "VCR OSD Mono", "family": "VCR OSD Mono", "file": "looks/sadgirlsclub/vcr-osd-mono.woff",
                     "format": "woff"},
}

_SAKURA_LIGHT = {
    "page": "#fbeef0", "primary": "#c25a72", "primary_hover": "#9c4157", "text": "#3d2530", "muted": "#8a6b74",
    "surface": "rgba(255, 250, 251, 0.78)", "surface_strong": "rgba(255, 252, 253, 0.92)", "line": "rgba(194, 90, 114, 0.18)",
    "highlight": "#f4b6c2", "highlight_soft": "#fadbe1", "error": "#b3263e",
    "ok_bg": "#e3f4e6", "ok_fg": "#2b6a3a", "bad_bg": "#fde2e6", "bad_fg": "#a3263d", "busy_bg": "#f7e1ec", "busy_fg": "#8c3a63",
    "tint": 0.35, "gradients": {"page": None, "bar": None, "button": None},
}
_SAKURA_DARK = {
    "page": "#1c0c16", "primary": "#f08aa2", "primary_hover": "#f6a9bb", "text": "#f6e7eb", "muted": "#c8a9b2",
    "surface": "rgba(43, 24, 34, 0.78)", "surface_strong": "rgba(48, 27, 38, 0.92)", "line": "rgba(240, 138, 162, 0.2)",
    "highlight": "#f4b6c2", "highlight_soft": "#fadbe1", "error": "#ff9fb0",
    "ok_bg": "#1f3a28", "ok_fg": "#a8dcb5", "bad_bg": "#4a1f2a", "bad_fg": "#ffb3c1", "busy_bg": "#3d2338", "busy_fg": "#f2bfd9",
    "tint": 0.62, "gradients": {"page": None, "bar": None, "button": None},
}
_NEON_DARK = {
    "page": "#07020d", "primary": "#ff2e97", "primary_hover": "#ff7ac0", "text": "#ffe9f5", "muted": "#b98aa9",
    "surface": "rgba(12, 3, 20, 0.84)", "surface_strong": "rgba(18, 5, 28, 0.95)", "line": "rgba(255, 46, 151, 0.5)",
    "highlight": "#ff2e97", "highlight_soft": "rgba(255, 46, 151, 0.14)", "error": "#ff4d6d",
    "ok_bg": "rgba(0, 255, 170, 0.12)", "ok_fg": "#5dffc4", "bad_bg": "rgba(255, 45, 85, 0.16)", "bad_fg": "#ff8aa0",
    "busy_bg": "rgba(255, 46, 151, 0.16)", "busy_fg": "#ff9ad0",
    "tint": 0.6, "gradients": {"page": None, "bar": None, "button": None},
}
_PLAIN_LIGHT = {
    "page": "#f4f6fa", "primary": "#1f4e9c", "primary_hover": "#163b78", "text": "#1b2330", "muted": "#5b6677",
    "surface": "rgba(255, 255, 255, 0.82)", "surface_strong": "rgba(255, 255, 255, 0.94)", "line": "rgba(31, 78, 156, 0.18)",
    "highlight": "#dbe6f7", "highlight_soft": "#edf2fb", "error": "#b3261e",
    "ok_bg": "#e3f4e6", "ok_fg": "#2b6a3a", "bad_bg": "#fde2e6", "bad_fg": "#a3263d", "busy_bg": "#e8eefc", "busy_fg": "#2f4f8f",
    "tint": 0.35, "gradients": {"page": None, "bar": None, "button": None},
}
_PLAIN_DARK = {
    "page": "#0f1520", "primary": "#7aa7ff", "primary_hover": "#a3c1ff", "text": "#e8edf5", "muted": "#9aa6b8",
    "surface": "rgba(22, 30, 44, 0.82)", "surface_strong": "rgba(26, 35, 52, 0.94)", "line": "rgba(122, 167, 255, 0.22)",
    "highlight": "#223457", "highlight_soft": "#182742", "error": "#ff8f86",
    "ok_bg": "#1f3a28", "ok_fg": "#a8dcb5", "bad_bg": "#4a1f2a", "bad_fg": "#ffb3c1", "busy_bg": "#1d2b4a", "busy_fg": "#b9ccff",
    "tint": 0.6, "gradients": {"page": None, "bar": None, "button": None},
}
_NO_EFFECTS = {"scanlines": False, "flicker": False, "jitter": False, "pulse": False, "intensity": 0.6,
               "pulse_a": None, "pulse_b": None}

# What each base draws with nothing overridden: the editor shows these, and "Reset to stock" returns to them.
STOCK: dict[str, dict] = {
    "sakura": {"light": _SAKURA_LIGHT, "dark": _SAKURA_DARK, "neon": None, "scene": "base", "radius": 14, "glass": True,
               "border": "soft", "fonts": {"heading": "system-serif", "body": "system-sans"}, "effects": _NO_EFFECTS,
               "custom_css": "", "assets": {"background": None, "texture": None, "font_heading": None, "font_body": None}},
    "neon": {"light": None, "dark": _NEON_DARK,
             "neon": {"cyan": "#00f0ff", "fringe_l": "rgba(0, 240, 255, 0.75)", "fringe_r": "rgba(255, 20, 80, 0.8)", "bloom": "#ff2e97"},
             "scene": "base", "radius": 0, "glass": True, "border": "soft",
             "fonts": {"heading": "condensed", "body": "system-sans"}, "effects": _NO_EFFECTS, "custom_css": "",
             "assets": {"background": None, "texture": None, "font_heading": None, "font_body": None}},
    "plain": {"light": _PLAIN_LIGHT, "dark": _PLAIN_DARK, "neon": None, "scene": "none", "radius": 10, "glass": True,
              "border": "soft", "fonts": {"heading": "system-sans", "body": "system-sans"}, "effects": _NO_EFFECTS,
              "custom_css": "", "assets": {"background": None, "texture": None, "font_heading": None, "font_body": None}},
}

# Built-in presets: what a built-in with an empty config means. Corporate is the plain base's stock look;
# Sad Girls Club is sadgirlsclub.wtf's engine/style/main.css on the neon base.
PRESETS: dict[str, dict] = {
    "corporate": {},
    "sadgirlsclub": {
        "dark": {"page": "#000000", "primary": "#ff1493", "primary_hover": "#ff69b4", "text": "#ffffff", "muted": "#bfbfbf",
                 "surface": "#050505", "surface_strong": "#000000", "line": "#ffffff",
                 "highlight": "rgba(255, 20, 147, 0.25)", "highlight_soft": "rgba(255, 20, 147, 0.12)", "error": "#ff0050",
                 "ok_bg": "#001a10", "ok_fg": "#00ff9c", "bad_bg": "#1a0008", "bad_fg": "#ff0050",
                 "busy_bg": "#1a0010", "busy_fg": "#ff1493"},
        "neon": {"cyan": "#001eff", "fringe_l": "rgba(0, 30, 255, 0.5)", "fringe_r": "rgba(255, 0, 80, 0.3)", "bloom": "#ffffff"},
        "scene": "none", "radius": 0, "glass": False, "border": "ridge",
        "fonts": {"heading": "vcr-osd-mono", "body": "vcr-osd-mono"},
        "effects": {"scanlines": True, "flicker": True, "jitter": True, "pulse": True, "intensity": 0.6,
                    "pulse_a": "#0e000f", "pulse_b": "#1a001c"},
        "custom_css": "button { text-transform: uppercase; }\na:visited { color: #ff3333; }\n",
        "texture_bundled": "looks/sadgirlsclub/panel.png",
    },
}

DEFAULT_SETTINGS = {"default": FALLBACK, "enforce": "",
                    "brand": {"name": "", "tagline": "", "logo_height": 2.0,
                              "assets": {"logo": None, "logo_dark": None, "mark": None}},
                    "version": 1}

# ------------------------------------------------------------------ validation
_COLOR = re.compile(r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|"
                    r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+|1\.0)\s*)?\))$")
_CSS_REFUSED = (re.compile(r"@import", re.I), re.compile(r"expression\s*\(", re.I), re.compile(r"</style", re.I))


def _color(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.strip()
    if not _COLOR.match(value):
        raise ValueError(f"{value!r} is not a colour (#rrggbb or rgba(r, g, b, a))")
    return value.lower() if value.startswith("#") else value


class Stop(BaseModel):
    model_config = ConfigDict(extra="ignore")
    color: str
    at: float = Field(ge=0, le=100)
    _c = field_validator("color")(classmethod(lambda cls, v: _color(v)))


class Gradient(BaseModel):
    model_config = ConfigDict(extra="ignore")
    angle: float = Field(180, ge=0, le=360)
    stops: list[Stop] = Field(min_length=2, max_length=4)


class Palette(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: str | None = None
    primary: str | None = None
    primary_hover: str | None = None
    text: str | None = None
    muted: str | None = None
    surface: str | None = None
    surface_strong: str | None = None
    line: str | None = None
    highlight: str | None = None
    highlight_soft: str | None = None
    error: str | None = None
    ok_bg: str | None = None
    ok_fg: str | None = None
    bad_bg: str | None = None
    bad_fg: str | None = None
    busy_bg: str | None = None
    busy_fg: str | None = None
    tint: float | None = Field(None, ge=0, le=1)
    gradients: dict[Literal["page", "bar", "button"], Gradient | None] = Field(default_factory=dict)

    _colors = field_validator(*[k for k, _ in TOKENS])(classmethod(lambda cls, v: _color(v)))


class NeonExtras(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cyan: str | None = None
    fringe_l: str | None = None
    fringe_r: str | None = None
    bloom: str | None = None
    _colors = field_validator("cyan", "fringe_l", "fringe_r", "bloom")(classmethod(lambda cls, v: _color(v)))


class Fonts(BaseModel):
    model_config = ConfigDict(extra="ignore")
    heading: str | None = None
    body: str | None = None

    @field_validator("heading", "body")
    @classmethod
    def _known(cls, v):
        if v is None or v == "":
            return None
        if v not in FONT_STACKS and v not in BUNDLED_FONTS and v != "upload":
            raise ValueError(f"{v!r} is not a font the box knows")
        return v


class Effects(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scanlines: bool = False
    flicker: bool = False
    jitter: bool = False
    pulse: bool = False
    intensity: float = Field(0.6, ge=0, le=1)
    pulse_a: str | None = None
    pulse_b: str | None = None
    _colors = field_validator("pulse_a", "pulse_b")(classmethod(lambda cls, v: _color(v)))


class ThemeConfig(BaseModel):
    """A theme's overrides. Anything missing means the base's stock value; unknown keys are dropped."""
    model_config = ConfigDict(extra="ignore")
    light: Palette | None = None
    dark: Palette | None = None
    neon: NeonExtras | None = None
    scene: Literal["base", "none", "image"] | None = None
    radius: float | None = Field(None, ge=0, le=20)
    glass: bool | None = None
    border: Literal["soft", "hard", "ridge"] | None = None
    fonts: Fonts | None = None
    effects: Effects | None = None
    custom_css: str | None = Field(None, max_length=MAX_CUSTOM_CSS)

    @field_validator("custom_css")
    @classmethod
    def _safe_css(cls, v):
        if v is None:
            return None
        for pattern in _CSS_REFUSED:
            if pattern.search(v):
                raise ValueError(f"Custom CSS can't contain {pattern.pattern.replace(chr(92), '')}")
        return v


def check_config(data: dict) -> dict:
    """Validated overrides as a plain dict (None values dropped), or ThemeError."""
    try:
        cfg = ThemeConfig.model_validate(data or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ThemeError(f"{where}: {first['msg']}" if where else first["msg"]) from None
    return _drop_none(cfg.model_dump(exclude_none=True))


def _drop_none(value):
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    return value


# --------------------------------------------------------------- merging
def _merge(stock, over):
    if isinstance(stock, dict) and isinstance(over, dict):
        out = copy.deepcopy(stock)
        for k, v in over.items():
            out[k] = _merge(stock.get(k), v) if isinstance(v, dict) and isinstance(stock.get(k), dict) else copy.deepcopy(v)
        return out
    return copy.deepcopy(over) if over is not None else copy.deepcopy(stock)


def effective(base: str, config: dict) -> dict:
    """The base's stock values with the theme's overrides on top: what the editor shows and css() draws."""
    out = _merge(STOCK[base], config)
    if base == "neon":
        out["light"] = None
    return out


def strip_stock(base: str, config: dict) -> dict:
    """Only what differs from the base's stock: an untouched Save leaves a built-in without a stylesheet."""
    def diff(stock, value):
        if isinstance(stock, dict) and isinstance(value, dict):
            out = {}
            for k, v in value.items():
                d = diff(stock.get(k), v)
                if d is not None and d != {}:
                    out[k] = d
            return out
        return None if value == stock else value
    out = diff(STOCK[base], config) or {}
    out.pop("assets", None)   # the server owns these
    return out


# ------------------------------------------------------------------ rows
def _row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM themes WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ThemeError(f"There is no theme called {slug!r}.")
    return row


def _parse(row: sqlite3.Row) -> dict:
    try:
        config = json.loads(row["config"] or "{}")
    except ValueError:
        config = {}
    if row["builtin"] and not config:
        config = copy.deepcopy(PRESETS.get(row["slug"], {}))
    theme = {k: row[k] for k in row.keys()}
    theme["builtin"], theme["enabled"], theme["pickable"] = bool(row["builtin"]), bool(row["enabled"]), bool(row["pickable"])
    theme["config"] = config
    return theme


def list_themes(conn: sqlite3.Connection) -> list[dict]:
    return [_parse(r) for r in conn.execute("SELECT * FROM themes ORDER BY builtin DESC, id")]


def get(conn: sqlite3.Connection, slug: str) -> dict:
    return _parse(_row(conn, slug))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())[:40]


def _unique_slug(conn: sqlite3.Connection, name: str) -> str:
    base = slugify(name) or "theme"
    if base in RESERVED_SLUGS or base in BASES:
        base = base + "2"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM themes WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base[:38]}{n}"
        n += 1
    return slug


def _check_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ThemeError("A theme needs a name.")
    if len(name) > MAX_NAME:
        raise ThemeError(f"A theme's name is at most {MAX_NAME} characters.")
    return name


def create(conn: sqlite3.Connection, user_id: int | None, *, name: str, base: str, config: dict | None = None,
           from_slug: str | None = None, settings: Settings | None = None) -> dict:
    """A new theme from a base, or a copy of another theme (its config and, with settings, its files)."""
    name = _check_name(name)
    source = get(conn, from_slug) if from_slug else None
    if source:
        base = source["base"]
        config = copy.deepcopy(source["config"])
    if base not in BASES:
        raise ThemeError(f"The base is one of {', '.join(BASES)}.")
    stored = check_config(config or {})
    slug = _unique_slug(conn, name)
    conn.execute("INSERT INTO themes(slug, name, base, config, updated_by) VALUES (?, ?, ?, ?, ?)",
                 (slug, name, base, json.dumps(stored), user_id))
    if source and settings is not None:
        _copy_assets(settings, source, slug, stored)
        conn.execute("UPDATE themes SET config = ? WHERE slug = ?", (json.dumps(stored), slug))
    return get(conn, slug)


def _copy_assets(settings: Settings, source: dict, slug: str, config: dict) -> None:
    src_dir, dst_dir = theme_dir(settings, source["slug"]), theme_dir(settings, slug)
    assets = dict(config.get("assets") or {})
    for kind, name in list((source["config"].get("assets") or {}).items()):
        if name and (src_dir / name).is_file():
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / name).write_bytes((src_dir / name).read_bytes())
            assets[kind] = name
    if assets:
        config["assets"] = assets


def update(conn: sqlite3.Connection, user_id: int | None, slug: str, *, name: str | None = None,
           enabled: bool | None = None, pickable: bool | None = None, config: dict | None = None) -> dict:
    """Name, switches and overrides. The config sent is the editor's full picture; only what differs
    from the base's stock is kept. The theme's files (assets) stay as they are."""
    row = _row(conn, slug)
    current = _parse(row)
    sets, params = [], []
    if name is not None:
        sets.append("name = ?")
        params.append(_check_name(name))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if pickable is not None:
        sets.append("pickable = ?")
        params.append(1 if pickable else 0)
    if config is not None:
        stored = strip_stock(current["base"], check_config(config))
        for owned in ("assets", "texture_bundled"):   # the server's, not the editor's
            if current["config"].get(owned):
                stored[owned] = current["config"][owned]
        sets.append("config = ?")
        params.append(json.dumps(stored))
        sets.append("version = version + 1")
    if not sets:
        return current
    sets.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    sets.append("updated_by = ?")
    params.append(user_id)
    conn.execute(f"UPDATE themes SET {', '.join(sets)} WHERE slug = ?", (*params, slug))
    return get(conn, slug)


def reset(conn: sqlite3.Connection, user_id: int | None, slug: str) -> dict:
    """Back to stock (built-ins: the preset). The theme's files are kept but unused until picked again."""
    row = _row(conn, slug)
    if not row["builtin"]:
        raise ThemeError("Only the built-in themes have a stock look to go back to.")
    conn.execute("UPDATE themes SET config = '{}', version = version + 1, updated_by = ?, "
                 "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE slug = ?", (user_id, slug))
    return get(conn, slug)


def delete(conn: sqlite3.Connection, settings: Settings, slug: str) -> None:
    row = _row(conn, slug)
    if row["builtin"]:
        raise ThemeError("The built-in themes can be turned off, not deleted.")
    conn.execute("DELETE FROM themes WHERE slug = ?", (slug,))
    folder = theme_dir(settings, slug)
    if folder.is_dir():
        for p in folder.iterdir():
            p.unlink(missing_ok=True)
        folder.rmdir()


# -------------------------------------------------------------- settings
def load_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    cfg = copy.deepcopy(DEFAULT_SETTINGS)
    if row:
        try:
            stored = json.loads(row["value"])
            cfg = _merge(cfg, {k: v for k, v in stored.items() if k in cfg})
        except (ValueError, AttributeError):
            pass
    return cfg


def _store_settings(conn: sqlite3.Connection, cfg: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (SETTINGS_KEY, json.dumps(cfg)))


def save_settings(conn: sqlite3.Connection, *, default: str | None = None, enforce: str | None = None) -> dict:
    cfg = load_settings(conn)
    if default is not None:
        theme = get(conn, default)
        if not theme["enabled"]:
            raise ThemeError("The default theme has to be on.")
        cfg["default"] = default
    if enforce is not None:
        if enforce:
            theme = get(conn, enforce)
            if not theme["enabled"]:
                raise ThemeError("A theme everyone gets has to be on.")
        cfg["enforce"] = enforce
    _store_settings(conn, cfg)
    return cfg


def save_brand(conn: sqlite3.Connection, *, name: str | None = None, tagline: str | None = None,
               logo_height: float | None = None) -> dict:
    cfg = load_settings(conn)
    brand = cfg["brand"]
    if name is not None:
        name = " ".join(name.split())
        if len(name) > MAX_NAME:
            raise ThemeError(f"The name is at most {MAX_NAME} characters.")
        brand["name"] = name
    if tagline is not None:
        tagline = " ".join(tagline.split())
        if len(tagline) > 120:
            raise ThemeError("The tagline is at most 120 characters.")
        brand["tagline"] = tagline
    if logo_height is not None:
        if not 1.0 <= logo_height <= 4.0:
            raise ThemeError("The logo is between 1 and 4 rem tall.")
        brand["logo_height"] = round(logo_height, 2)
    cfg["version"] = cfg.get("version", 1) + 1
    _store_settings(conn, cfg)
    return cfg


# ----------------------------------------------------------------- files
def theme_dir(settings: Settings, slug: str) -> Path:
    return settings.theme_dir / slug


def brand_dir(settings: Settings) -> Path:
    return settings.theme_dir / "brand"


IMAGE_KINDS = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml",
               "woff2": "font/woff2", "woff": "font/woff"}
# kind -> (what it is, size limit, longest side kept, output format)
ASSET_RULES = {
    "brand": {"logo": ("image+svg", 2 << 20, 1024, "png"), "logo_dark": ("image+svg", 2 << 20, 1024, "png"),
              "mark": ("raster", 2 << 20, 1024, "png")},
    "theme": {"background": ("raster", 8 << 20, 2560, "jpg"), "texture": ("raster", 2 << 20, 1024, "png"),
              "font_heading": ("font", 1 << 20, 0, ""), "font_body": ("font", 1 << 20, 0, "")},
}
MARK_SIZES = (("mark-192.png", 192, False), ("mark-512.png", 512, False), ("mark-maskable-512.png", 512, True),
              ("mark-180.png", 180, False))
_SVG_REFUSED = (re.compile(r"<\s*script", re.I), re.compile(r"\son[a-z]+\s*=", re.I), re.compile(r"foreignObject", re.I),
                re.compile(r"href\s*=\s*[\"'](?!#)", re.I), re.compile(r"url\s*\(\s*[\"']?(?!#)", re.I))


def _read_limited(stream: BinaryIO, limit: int, what: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ThemeError(f"{what} is at most {limit >> 20} MB.")
    if not data:
        raise ThemeError("That file is empty.")
    return data


def _raster(data: bytes, max_side: int, fmt: str) -> bytes:
    """Re-encoded by Pillow (which also makes sure it is a picture), shrunk to max_side, metadata dropped."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.width > 12000 or im.height > 12000:
                raise ThemeError("That picture is too large (more than 12000 px on a side).")
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side))
            out = io.BytesIO()
            if fmt == "jpg":
                im.convert("RGB").save(out, "JPEG", quality=86, optimize=True)
            else:
                (im.convert("RGBA") if im.mode not in ("RGB", "RGBA", "P", "L") else im).save(out, "PNG", optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ThemeError("That isn't a picture the box can read (PNG, JPEG or WebP).") from None


def _svg(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ThemeError("That SVG isn't UTF-8 text.") from None
    if "<svg" not in text[:4096].lower():
        raise ThemeError("That file isn't an SVG.")
    for pattern in _SVG_REFUSED:
        if pattern.search(text):
            raise ThemeError("That SVG has scripts, event handlers or outside links in it; export it as a plain SVG.")
    return text.encode("utf-8")


def _font(data: bytes) -> tuple[bytes, str]:
    if data[:4] == b"wOF2":
        return data, "woff2"
    if data[:4] == b"wOFF":
        return data, "woff"
    raise ThemeError("Fonts are .woff2 (or .woff) files.")


def save_asset(settings: Settings, scope: str, kind: str, owner: str, stream: BinaryIO, *, page: str = "#ffffff") -> str:
    """Keep an uploaded file for the brand (owner 'brand') or a theme (owner = slug), checked and
    re-encoded per kind. Returns the file name to record in the config. page: the colour behind the
    maskable icon derived from a mark."""
    rules = ASSET_RULES.get(scope, {}).get(kind)
    if rules is None:
        raise ThemeError(f"There is no {kind!r} to upload here.")
    what, limit, max_side, fmt = rules
    data = _read_limited(stream, limit, "That file")
    folder = brand_dir(settings) if scope == "brand" else theme_dir(settings, owner)
    folder.mkdir(parents=True, exist_ok=True)
    if what == "font":
        data, ext = _font(data)
        name = f"{kind.replace('_', '-')}.{ext}"
    elif what == "image+svg" and b"<svg" in data[:2048].lower():
        data, name = _svg(data), f"{kind.replace('_', '-')}.svg"
    else:
        data, name = _raster(data, max_side, fmt), f"{kind.replace('_', '-')}.{fmt}"
    for old in folder.glob(f"{kind.replace('_', '-')}.*"):
        old.unlink(missing_ok=True)
    (folder / name).write_bytes(data)
    if scope == "brand" and kind == "mark":
        _derive_marks(folder, data, page)
    return name


def _derive_marks(folder: Path, data: bytes, page: str = "#ffffff") -> None:
    """The favicon and home-screen icons from the mark: squared on the page colour, maskable with padding."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA")
        side = max(im.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        for name, size, maskable in MARK_SIZES:
            canvas = Image.new("RGBA", (size, size), _rgb(page) + (255,) if maskable else (0, 0, 0, 0))
            inner = int(size * 0.6) if maskable else size
            icon = square.resize((inner, inner), Image.LANCZOS)
            canvas.alpha_composite(icon, ((size - inner) // 2, (size - inner) // 2))
            canvas.save(folder / name, "PNG", optimize=True)


def remove_asset(settings: Settings, scope: str, kind: str, owner: str) -> None:
    folder = brand_dir(settings) if scope == "brand" else theme_dir(settings, owner)
    for old in folder.glob(f"{kind.replace('_', '-')}.*"):
        old.unlink(missing_ok=True)
    if scope == "brand" and kind == "mark":
        for name, _, _ in MARK_SIZES:
            (folder / name).unlink(missing_ok=True)


def set_theme_asset(conn: sqlite3.Connection, slug: str, kind: str, name: str | None) -> dict:
    row = _row(conn, slug)
    config = json.loads(row["config"] or "{}")
    if row["builtin"] and not config:
        config = copy.deepcopy(PRESETS.get(slug, {}))
    assets = dict(config.get("assets") or {})
    if name:
        assets[kind] = name
    else:
        assets.pop(kind, None)
        if kind == "texture":
            config.pop("texture_bundled", None)   # removing the texture removes a preset's bundled one too
    config["assets"] = assets
    conn.execute("UPDATE themes SET config = ?, version = version + 1 WHERE slug = ?", (json.dumps(config), slug))
    return get(conn, slug)


def set_brand_asset(conn: sqlite3.Connection, kind: str, name: str | None) -> dict:
    cfg = load_settings(conn)
    cfg["brand"]["assets"][kind] = name
    cfg["version"] = cfg.get("version", 1) + 1
    _store_settings(conn, cfg)
    return cfg


def asset_path(settings: Settings, scope: str, owner: str, kind: str, assets: dict) -> tuple[Path, str] | None:
    name = (assets or {}).get(kind)
    if not name or "/" in name or "\\" in name:
        return None
    folder = brand_dir(settings) if scope == "brand" else theme_dir(settings, owner)
    path = folder / name
    if not path.is_file():
        return None
    return path, IMAGE_KINDS.get(path.suffix.lstrip("."), "application/octet-stream")


# ---------------------------------------------------------------- packs
def export_pack(settings: Settings, theme: dict) -> dict:
    assets = {}
    for kind in ASSET_RULES["theme"]:
        found = asset_path(settings, "theme", theme["slug"], kind, theme["config"].get("assets") or {})
        if found:
            path, mime = found
            assets[kind] = {"name": path.name, "mime": mime, "base64": base64.b64encode(path.read_bytes()).decode()}
    config = {k: v for k, v in theme["config"].items() if k != "assets"}
    return {"format": PACK_FORMAT, "name": theme["name"], "base": theme["base"], "config": config, "assets": assets}


def import_pack(conn: sqlite3.Connection, settings: Settings, user_id: int | None, data: Any,
                *, name: str | None = None) -> dict:
    if not isinstance(data, dict) or data.get("format") != PACK_FORMAT:
        raise ThemeError("That isn't a Mnemerynne theme pack (a .theme.json downloaded from Settings → Themes).")
    base = data.get("base")
    if base not in BASES:
        raise ThemeError(f"The pack's base is one of {', '.join(BASES)}.")
    theme = create(conn, user_id, name=name or str(data.get("name") or "Imported theme"), base=base,
                   config=data.get("config") if isinstance(data.get("config"), dict) else {})
    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    for kind, item in assets.items():
        if kind not in ASSET_RULES["theme"] or not isinstance(item, dict):
            continue
        try:
            raw = base64.b64decode(str(item.get("base64", "")), validate=True)
        except (ValueError, TypeError):
            raise ThemeError(f"The pack's {kind} isn't valid base64.") from None
        stored = save_asset(settings, "theme", kind, theme["slug"], io.BytesIO(raw))
        theme = set_theme_asset(conn, theme["slug"], kind, stored)
    return theme


# ------------------------------------------------------------------ css
def _rgb(color: str) -> tuple[int, int, int]:
    """The red, green, blue of a colour the validator accepted."""
    c = color.strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    nums = re.findall(r"\d+(?:\.\d+)?", c)
    return int(float(nums[0])), int(float(nums[1])), int(float(nums[2]))


def _dark(g: dict) -> bool:
    """Is a gradient dark on average (so white text reads on it)?"""
    lumas = []
    for s in g.get("stops", []):
        r, gg, b = _rgb(s["color"])
        lumas.append(0.2126 * r + 0.7152 * gg + 0.0722 * b)
    return bool(lumas) and sum(lumas) / len(lumas) < 150


def gradient_css(g: dict | None) -> str | None:
    if not g or not g.get("stops"):
        return None
    stops = ", ".join(f"{s['color']} {float(s['at']):g}%" for s in g["stops"])
    return f"linear-gradient({float(g.get('angle', 180)):g}deg, {stops})"


def _font_decl(key: str | None, slot: str, slug: str) -> str | None:
    if not key:
        return None
    if key in FONT_STACKS:
        return FONT_STACKS[key][1]
    if key in BUNDLED_FONTS:
        return f'"{BUNDLED_FONTS[key]["family"]}", {FONT_STACKS["system-sans"][1]}'
    if key == "upload":
        return f'"mn-{slot}-{slug}", {FONT_STACKS["system-sans"][1]}'
    return None


_SOLID = {"page": "none", "bar": "var(--glass)", "button": "var(--rose)"}   # what a place is without a gradient


def _palette_lines(p: dict, base: str, neon: dict | None, reset_places: tuple[str, ...] = ()) -> list[str]:
    lines = []
    for key, var in TOKENS:
        if p.get(key):
            lines.append(f"{var}: {p[key]};")
    for place, var in GRADIENT_PLACES:   # the dark palette goes back to solid where only the light one has a gradient
        if place in reset_places and not (p.get("gradients") or {}).get(place):
            lines.append(f"{var}: {_SOLID[place]};")
    if p.get("page"):
        r, g, b = _rgb(p["page"])
        tint = p.get("tint", 0.35)
        lines.append(f"--scene-tint: rgba({r}, {g}, {b}, {float(tint):g});")
    for place, var in GRADIENT_PLACES:
        g = (p.get("gradients") or {}).get(place)
        css = gradient_css(g)
        if css:
            lines.append(f"{var}: {css};")
            if place in ("bar", "button"):   # text that reads on the gradient: white on dark, the text colour on light
                lines.append(f"{var[:-3]}-fg: {'#ffffff' if _dark(g) else (p.get('text') or '#1b2330')};")
    if base == "neon":
        for key, var in NEON_TOKENS:
            if neon and neon.get(key):
                lines.append(f"{var}: {neon[key]};")
        glow = (neon or {}).get("bloom") or p.get("primary")
        if glow:
            r, g, b = _rgb(glow)
            fl = (neon or {}).get("fringe_l") or STOCK["neon"]["neon"]["fringe_l"]
            fr = (neon or {}).get("fringe_r") or STOCK["neon"]["neon"]["fringe_r"]
            lines.append(f"--glow: {r}, {g}, {b};")
            lines.append(f"--bloom: 0 0 0 1px rgba({r}, {g}, {b}, 0.35), -2px 0 0 {fl}, 2px 0 0 {fr}, "
                         f"0 0 26px rgba({r}, {g}, {b}, 0.28);")
            lines.append("--shadow: var(--bloom);")
    return lines


def css(theme: dict, settings: Settings | None = None, *, version: int | None = None) -> str:
    """The stylesheet for a theme's overrides (empty when there are none), scoped to its slug and loaded
    after the base's, so it wins. Values come straight from the validated config."""
    slug, base, config = theme["slug"], theme["base"], theme["config"]
    if not config:
        return ""
    v = version if version is not None else theme.get("version", 1)
    sel = f'html[data-look="{slug}"]'
    assets = config.get("assets") or {}
    out: list[str] = [f"/* {theme['name']} ({base} base), version {v}: generated by Mnemerynne, not for editing. */"]
    fonts = []
    for slot, kind in (("heading", "font_heading"), ("body", "font_body")):
        key = (config.get("fonts") or {}).get(slot)
        if key == "upload" and assets.get(kind):
            fmt = "woff2" if assets[kind].endswith(".woff2") else "woff"
            fonts.append(f'@font-face {{ font-family: "mn-{slot}-{slug}"; src: url(/theme/{slug}/font/{slot}?v={v}) '
                         f'format("{fmt}"); font-display: swap; }}')
        elif key in BUNDLED_FONTS:
            f = BUNDLED_FONTS[key]
            fonts.append(f'@font-face {{ font-family: "{f["family"]}"; src: url(/static/{f["file"]}) '
                         f'format("{f["format"]}"); font-display: swap; }}')
    out.extend(dict.fromkeys(fonts))

    main: list[str] = []
    neon = config.get("neon")
    if base == "neon":
        main.extend(_palette_lines(config.get("dark") or {}, base, neon))
    else:
        main.extend(_palette_lines(config.get("light") or {}, base, None))
    if config.get("radius") is not None:
        main.append(f"--radius: {float(config['radius']):g}px;")
    heading = _font_decl((config.get("fonts") or {}).get("heading"), "heading", slug)
    body = _font_decl((config.get("fonts") or {}).get("body"), "body", slug)
    if heading:
        main.append(f"--serif: {heading};")
    if body:
        main.append(f"--sans: {body};")
    if config.get("scene") == "image" and assets.get("background"):
        main.append(f"--brand-image: url(/theme/{slug}/background?v={v});")
    if assets.get("texture"):
        main.append(f"--brand-texture: url(/theme/{slug}/texture?v={v});")
    elif config.get("texture_bundled"):
        main.append(f"--brand-texture: url(/static/{config['texture_bundled']});")
    effects = config.get("effects") or {}
    if any(effects.get(e) for e in EFFECTS):
        main.append(f"--fx: {float(effects.get('intensity', 0.6)):g};")
    if effects.get("pulse"):
        main.append(f"--pulse-a: {effects.get('pulse_a') or '#0e000f'};")
        main.append(f"--pulse-b: {effects.get('pulse_b') or '#1a001c'};")
    if main:
        out.append(f"{sel} {{\n  " + "\n  ".join(main) + "\n}")
    if base != "neon" and config.get("dark"):
        light_places = tuple(pl for pl, _ in GRADIENT_PLACES if ((config.get("light") or {}).get("gradients") or {}).get(pl))
        dark = _palette_lines(config["dark"], base, None, light_places)
        if dark:
            out.append(f"@media (prefers-color-scheme: dark) {{\n  {sel} {{\n    " + "\n    ".join(dark) + "\n  }\n}")
    if config.get("custom_css"):
        out.append("/* Custom CSS from Settings → Themes */")
        out.append(config["custom_css"].strip())
    return "\n".join(out) + "\n"


def attrs(theme: dict) -> dict[str, str]:
    """Attributes on <html> the structural stylesheets read: scene, border, flat, texture, effects."""
    config = theme["config"]
    eff = effective(theme["base"], config)
    out: dict[str, str] = {}
    assets = config.get("assets") or {}
    scene = eff.get("scene") or "base"
    if scene == "image" and not assets.get("background"):
        scene = "none"
    if scene != "base":
        out["data-scene"] = scene
    if eff.get("border") and eff["border"] != "soft":
        out["data-border"] = eff["border"]
    if eff.get("glass") is False:
        out["data-flat"] = ""
    if assets.get("texture") or config.get("texture_bundled"):
        out["data-texture"] = ""
    effects = eff.get("effects") or {}
    on = [e for e in EFFECTS if effects.get(e)]
    if on:
        out["data-fx"] = " ".join(on)
    palettes = [p for p in (config.get("light"), config.get("dark")) if p]
    for place, _ in GRADIENT_PLACES:   # the hooks in theme-effects.css only fire where a gradient is set
        if any((p.get("gradients") or {}).get(place) for p in palettes):
            out[f"data-{place}-bg"] = ""
    return out


# ----------------------------------------------------------- the state
@dataclass
class Look:
    slug: str
    base: str
    version: int
    css: str
    attrs: dict[str, str]
    name: str
    enabled: bool
    pickable: bool
    page_light: str
    page_dark: str
    swatch: dict[str, str] = field(default_factory=dict)   # page, primary, text, surface: the Preferences card
    builtin: bool = False

    @property
    def css_url(self) -> str | None:
        return f"/theme/{self.slug}.css?v={self.version}" if self.css else None

    @property
    def dark_only(self) -> bool:
        return self.base == "neon"

    def as_json(self) -> dict:
        return {"slug": self.slug, "base": self.base, "name": self.name, "css_url": self.css_url, "attrs": self.attrs,
                "swatch": self.swatch, "dark_only": self.dark_only}


@dataclass
class Looks:
    """Everything a page needs, loaded once and refreshed after every write (web/theme.py)."""
    themes: dict[str, Look] = field(default_factory=dict)
    settings: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_SETTINGS))
    brand_assets: dict[str, str] = field(default_factory=dict)   # kind -> url

    @property
    def brand(self) -> dict:
        b = dict(self.settings["brand"])
        b["logo"] = self.brand_assets.get("logo")
        b["logo_dark"] = self.brand_assets.get("logo_dark")
        b["mark"] = self.brand_assets.get("mark")
        b["mark_version"] = self.settings.get("version", 1)
        return b

    def pickable(self) -> list[Look]:
        if self.settings.get("enforce"):
            return []
        return [t for t in self.themes.values() if t.enabled and t.pickable]

    def resolve(self, user_theme: str | None, cookie: str | None) -> Look:
        """Enforced › the person's choice (if it exists, is on and may be picked) › the cookie, signed out ›
        the default › neon."""
        enforce = self.settings.get("enforce")
        if enforce and enforce in self.themes and self.themes[enforce].enabled:
            return self.themes[enforce]
        for wanted in (user_theme, cookie if user_theme is None else None):
            if wanted and wanted in self.themes:
                t = self.themes[wanted]
                if t.enabled and t.pickable:
                    return t
        default = self.settings.get("default") or FALLBACK
        if default in self.themes and self.themes[default].enabled:
            return self.themes[default]
        return self.themes.get(FALLBACK) or next(iter(self.themes.values()))


def load_looks(conn: sqlite3.Connection, settings: Settings) -> Looks:
    looks = Looks(settings=load_settings(conn))
    for theme in list_themes(conn):
        eff = effective(theme["base"], theme["config"])
        shown = eff.get("light") or eff.get("dark") or {}
        looks.themes[theme["slug"]] = Look(
            slug=theme["slug"], base=theme["base"], version=theme["version"], css=css(theme, settings),
            attrs=attrs(theme), name=theme["name"], enabled=theme["enabled"], pickable=theme["pickable"],
            page_light=shown.get("page") or "#fbeef0", page_dark=((eff.get("dark") or {}).get("page") or "#07020d"),
            swatch={k: shown.get(k) or "" for k in ("page", "primary", "text", "surface", "highlight")},
            builtin=theme["builtin"])
    v = looks.settings.get("version", 1)
    for kind in ("logo", "logo_dark", "mark"):
        if asset_path(settings, "brand", "brand", kind, looks.settings["brand"]["assets"]):
            looks.brand_assets[kind] = f"/theme/brand/{kind.replace('_', '-')}?v={v}"
    return looks


def editor_view(theme: dict) -> dict:
    """A theme for the editor: the row, its overrides, and the full effective config to show."""
    return {**theme, "effective": effective(theme["base"], theme["config"]), "has_css": bool(theme["config"])}
