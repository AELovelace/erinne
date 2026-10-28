"""Themes (mnemerynne/theme.py): the public stylesheet and files every page loads (/theme/…), and the
admin's side of Settings → Themes (/api/admin/themes, /api/admin/brand). Pages read app.state.looks,
loaded once and rebuilt after every write here, so rendering never touches the database for a theme."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from mnemerynne import db, theme
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db, get_settings

router = APIRouter()         # public
admin_router = APIRouter()   # admins

_IMMUTABLE = "public, max-age=31536000, immutable"   # every URL carries ?v=<version>; a change makes a new URL


def looks_for(app) -> theme.Looks:
    """The themes as pages see them, loaded on first use (after the database is migrated)."""
    looks = getattr(app.state, "looks", None)
    if looks is None:
        conn = db.connect(app.state.settings.db_path)
        try:
            looks = theme.load_looks(conn, app.state.settings)
        finally:
            conn.close()
        app.state.looks = looks
    return looks


def _refresh(request: Request, conn: sqlite3.Connection) -> theme.Looks:
    request.app.state.looks = theme.load_looks(conn, request.app.state.settings)
    return request.app.state.looks


def _refuse(exc: theme.ThemeError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


# ---------------------------------------------------------------- public
@router.get("/theme/brand.css", include_in_schema=False)
def brand_css(request: Request):
    brand = looks_for(request.app).brand
    text = f":root {{ --brand-logo-h: {float(brand.get('logo_height') or 2):g}rem; }}\n"
    return Response(text, media_type="text/css; charset=utf-8",
                    headers={"Cache-Control": _IMMUTABLE, "X-Content-Type-Options": "nosniff"})


def _file(path, mime: str):
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": _IMMUTABLE}
    if mime == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return FileResponse(path, media_type=mime, headers=headers)


@router.get("/theme/brand/mark-{size}.png", include_in_schema=False)
def brand_mark(size: str, request: Request, settings: Settings = Depends(get_settings)):
    name = f"mark-{size}.png"
    if name not in {n for n, _, _ in theme.MARK_SIZES}:
        raise _not_found()
    path = theme.brand_dir(settings) / name
    if not path.is_file():
        raise _not_found()
    return _file(path, "image/png")


@router.get("/theme/brand/{kind}", include_in_schema=False)
def brand_file(kind: str, request: Request, settings: Settings = Depends(get_settings)):
    kind = kind.replace("-", "_")
    if kind not in theme.ASSET_RULES["brand"]:
        raise _not_found()
    found = theme.asset_path(settings, "brand", "brand", kind, looks_for(request.app).settings["brand"]["assets"])
    if found is None:
        raise _not_found()
    return _file(*found)


@router.get("/theme/{slug}.css", include_in_schema=False)
def theme_css(slug: str, request: Request):
    look = looks_for(request.app).themes.get(slug)
    if look is None or not look.css:
        raise _not_found()
    return Response(look.css, media_type="text/css; charset=utf-8",
                    headers={"Cache-Control": _IMMUTABLE, "X-Content-Type-Options": "nosniff"})


@router.get("/theme/{slug}/font/{slot}", include_in_schema=False)
def theme_font(slug: str, slot: str, settings: Settings = Depends(get_settings),
               conn: sqlite3.Connection = Depends(get_db)):
    if slot not in ("heading", "body"):
        raise _not_found()
    return _theme_file(conn, settings, slug, f"font_{slot}")


@router.get("/theme/{slug}/{kind}", include_in_schema=False)
def theme_file(slug: str, kind: str, settings: Settings = Depends(get_settings),
               conn: sqlite3.Connection = Depends(get_db)):
    if kind not in ("background", "texture"):
        raise _not_found()
    return _theme_file(conn, settings, slug, kind)


def _theme_file(conn, settings, slug, kind):
    try:
        t = theme.get(conn, slug)
    except theme.ThemeError:
        raise _not_found() from None
    found = theme.asset_path(settings, "theme", slug, kind, t["config"].get("assets") or {})
    if found is None:
        raise _not_found()
    return _file(*found)


# ----------------------------------------------------------------- admin
def _status(request: Request, conn: sqlite3.Connection) -> dict:
    looks = _refresh(request, conn)
    return {
        "themes": [theme.editor_view(t) for t in theme.list_themes(conn)],
        "settings": {"default": looks.settings["default"], "enforce": looks.settings["enforce"]},
        "brand": looks.brand,
        "bases": list(theme.BASES),
        "fonts": [{"key": k, "name": v[0], "bundled": False} for k, v in theme.FONT_STACKS.items()]
                 + [{"key": k, "name": v["name"], "bundled": True} for k, v in theme.BUNDLED_FONTS.items()],
        "tokens": [k for k, _ in theme.TOKENS],
        "effects": list(theme.EFFECTS),
        "stock": theme.STOCK,
        "pack_format": theme.PACK_FORMAT,
    }


@admin_router.get("/api/admin/themes")
def list_all(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _status(request, conn)


class NewThemeBody(BaseModel):
    name: str = Field(min_length=1, max_length=theme.MAX_NAME)
    base: str = "sakura"
    from_slug: str | None = Field(None, alias="from", max_length=40)
    model_config = {"populate_by_name": True}


@admin_router.post("/api/admin/themes", status_code=status.HTTP_201_CREATED)
def create_theme(body: NewThemeBody, request: Request, user=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        with transaction(conn):
            made = theme.create(conn, user["id"], name=body.name, base=body.base, from_slug=body.from_slug,
                                settings=settings)
            _log(conn, user, "settings.theme", made["slug"], f"new theme ({made['base']} base)"
                 + (f" copied from {body.from_slug}" if body.from_slug else ""))
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return {**_status(request, conn), "slug": made["slug"]}


class ThemeSettingsBody(BaseModel):
    default: str | None = Field(None, max_length=40)
    enforce: str | None = Field(None, max_length=40)


@admin_router.put("/api/admin/themes/settings")
def set_settings(body: ThemeSettingsBody, request: Request, user=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            cfg = theme.save_settings(conn, default=body.default, enforce=body.enforce)
            if body.default is not None:
                _log(conn, user, "settings.theme", "default", cfg["default"])
            if body.enforce is not None:
                _log(conn, user, "settings.theme", "enforce", cfg["enforce"] or "people choose")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


class PreviewBody(BaseModel):
    slug: str | None = Field(None, max_length=40)
    base: str | None = None
    config: dict = Field(default_factory=dict)


@admin_router.post("/api/admin/themes/preview")
def preview(body: PreviewBody, request: Request, user=Depends(admin_user),
            conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """The stylesheet and attributes a draft would get, without saving anything."""
    existing = None
    if body.slug:
        try:
            existing = theme.get(conn, body.slug)
        except theme.ThemeError:
            existing = None
    base = existing["base"] if existing else (body.base or "sakura")
    if base not in theme.BASES:
        raise _refuse(theme.ThemeError(f"The base is one of {', '.join(theme.BASES)}."))
    try:
        config = theme.strip_stock(base, theme.check_config(body.config))
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    if existing:
        for owned in ("assets", "texture_bundled"):
            if existing["config"].get(owned):
                config[owned] = existing["config"][owned]
    draft = {"slug": body.slug or "preview", "base": base, "config": config,
             "name": existing["name"] if existing else "Preview", "version": existing["version"] if existing else 0}
    return {"css": theme.css(draft, settings), "attrs": theme.attrs(draft), "base": base}


async def _upload_json(request: Request, file: UploadFile | None) -> Any:
    if file is not None:
        raw = await file.read(theme.MAX_PACK_BYTES + 1)
    else:
        raw = await request.body()
    if len(raw) > theme.MAX_PACK_BYTES:
        raise _refuse(theme.ThemeError(f"A theme pack is at most {theme.MAX_PACK_BYTES >> 20} MB."))
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _refuse(theme.ThemeError("That file isn't JSON.")) from None


@admin_router.post("/api/admin/themes/import", status_code=status.HTTP_201_CREATED)
async def import_theme(request: Request, file: UploadFile | None = File(None), user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A theme pack (the .theme.json from Download) becomes a new theme, files included."""
    data = await _upload_json(request, file)
    try:
        with transaction(conn):
            made = theme.import_pack(conn, settings, user["id"], data)
            _log(conn, user, "settings.theme", made["slug"], "imported from a theme pack")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return {**_status(request, conn), "slug": made["slug"]}


class ThemeBody(BaseModel):
    name: str | None = Field(None, max_length=theme.MAX_NAME)
    enabled: bool | None = None
    pickable: bool | None = None
    config: dict | None = None


@admin_router.put("/api/admin/themes/{slug}")
def update_theme(slug: str, body: ThemeBody, request: Request, user=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            before = theme.get(conn, slug)
            after = theme.update(conn, user["id"], slug, name=body.name, enabled=body.enabled, pickable=body.pickable,
                                 config=body.config)
            what = []
            if body.name is not None and body.name != before["name"]:
                what.append(f"renamed to {after['name']}")
            if body.enabled is not None and body.enabled != before["enabled"]:
                what.append("on" if body.enabled else "off")
            if body.pickable is not None and body.pickable != before["pickable"]:
                what.append("people may pick it" if body.pickable else "hidden from Preferences")
            if body.config is not None:
                what.append("look changed")
            if what:
                _log(conn, user, "settings.theme", slug, ", ".join(what))
            looks = theme.load_looks(conn, request.app.state.settings)
            if not after["enabled"] and (looks.settings["default"] == slug or looks.settings["enforce"] == slug):
                raise theme.ThemeError("Pick another default (or enforced) theme before turning this one off.")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


@admin_router.post("/api/admin/themes/{slug}/reset")
def reset_theme(slug: str, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            theme.reset(conn, user["id"], slug)
            _log(conn, user, "settings.theme", slug, "reset to stock")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


class DuplicateBody(BaseModel):
    name: str | None = Field(None, max_length=theme.MAX_NAME)


@admin_router.post("/api/admin/themes/{slug}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_theme(slug: str, body: DuplicateBody | None = None, request: Request = None, user=Depends(admin_user),
                    conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        with transaction(conn):
            source = theme.get(conn, slug)
            made = theme.create(conn, user["id"], name=(body.name if body and body.name else f"{source['name']} copy"),
                                base=source["base"], from_slug=slug, settings=settings)
            _log(conn, user, "settings.theme", made["slug"], f"copied from {slug}")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return {**_status(request, conn), "slug": made["slug"]}


@admin_router.delete("/api/admin/themes/{slug}")
def delete_theme(slug: str, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    try:
        with transaction(conn):
            looks = looks_for(request.app)
            if looks.settings["default"] == slug or looks.settings["enforce"] == slug:
                raise theme.ThemeError("Pick another default (or enforced) theme before deleting this one.")
            theme.delete(conn, settings, slug)
            _log(conn, user, "settings.theme", slug, "deleted")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


@admin_router.get("/api/admin/themes/{slug}/pack")
def download_pack(slug: str, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    try:
        pack = theme.export_pack(settings, theme.get(conn, slug))
    except theme.ThemeError:
        raise _not_found() from None
    return JSONResponse(pack, headers={"Content-Disposition": f'attachment; filename="{slug}.theme.json"',
                                       "Cache-Control": "no-store"})


@admin_router.post("/api/admin/themes/{slug}/assets/{kind}")
async def upload_theme_asset(slug: str, kind: str, request: Request, file: UploadFile = File(...),
                             user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                             settings: Settings = Depends(get_settings)):
    try:
        theme.get(conn, slug)
        name = await anyio.to_thread.run_sync(lambda: theme.save_asset(settings, "theme", kind, slug, file.file))
        with transaction(conn):
            theme.set_theme_asset(conn, slug, kind, name)
            _log(conn, user, "settings.theme", slug, f"{kind.replace('_', ' ')} uploaded")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


@admin_router.delete("/api/admin/themes/{slug}/assets/{kind}")
def remove_theme_asset(slug: str, kind: str, request: Request, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    if kind not in theme.ASSET_RULES["theme"]:
        raise _not_found()
    try:
        with transaction(conn):
            theme.set_theme_asset(conn, slug, kind, None)
            theme.remove_asset(settings, "theme", kind, slug)
            _log(conn, user, "settings.theme", slug, f"{kind.replace('_', ' ')} removed")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


class BrandBody(BaseModel):
    name: str | None = Field(None, max_length=theme.MAX_NAME)
    tagline: str | None = Field(None, max_length=120)
    logo_height: float | None = Field(None, ge=1, le=4)


@admin_router.put("/api/admin/brand")
def set_brand(body: BrandBody, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            theme.save_brand(conn, name=body.name, tagline=body.tagline, logo_height=body.logo_height)
            _log(conn, user, "settings.brand", "brand", ", ".join(k for k, v in body.model_dump().items() if v is not None))
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


@admin_router.post("/api/admin/brand/assets/{kind}")
async def upload_brand_asset(kind: str, request: Request, file: UploadFile = File(...), user=Depends(admin_user),
                             conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    looks = looks_for(request.app)
    default = looks.themes.get(looks.settings["default"])
    page = default.page_light if default else "#ffffff"
    try:
        name = await anyio.to_thread.run_sync(
            lambda: theme.save_asset(settings, "brand", kind, "brand", file.file, page=page))
        with transaction(conn):
            theme.set_brand_asset(conn, kind, name)
            _log(conn, user, "settings.brand", kind.replace("_", " "), "uploaded")
    except theme.ThemeError as exc:
        raise _refuse(exc) from None
    return _status(request, conn)


@admin_router.delete("/api/admin/brand/assets/{kind}")
def remove_brand_asset(kind: str, request: Request, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    if kind not in theme.ASSET_RULES["brand"]:
        raise _not_found()
    with transaction(conn):
        theme.set_brand_asset(conn, kind, None)
        theme.remove_asset(settings, "brand", kind, "brand")
        _log(conn, user, "settings.brand", kind.replace("_", " "), "removed")
    return _status(request, conn)
