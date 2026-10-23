"""Admins: the internal APIs Erynne may query, their operations, credentials,
who may use them, and a live test. Credentials are write-only."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import apis, group_refs
from mnemerynne.db import transaction
from mnemerynne.ingest import markdown
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db

router = APIRouter(prefix="/api/admin/apis")


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _api(conn: sqlite3.Connection, api_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM apis WHERE id = ?", (api_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such API")
    return row


def _op(conn: sqlite3.Connection, op_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT o.*, a.name AS api_name FROM api_operations o JOIN apis a ON a.id = o.api_id "
                       "WHERE o.id = ?", (op_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such operation")
    return row


def _op_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "description": r["description"], "method": r["method"],
            "path": r["path"], "params": apis.param_specs(r), "body": r["body"],
            "example_questions": r["example_questions"], "cache_seconds": r["cache_seconds"],
            "enabled": bool(r["enabled"]), "action": bool(r["action"])}


def _check_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")) or " " in url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The address must start with https:// (or http://).")
    return url


# ----------------------------------------------------------------- templates
class TemplateUpload(BaseModel):
    template: str = Field(..., max_length=500_000)   # the file's text


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def upload_template(body: TemplateUpload, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Add an API template (or replace the uploaded one with the same name)."""
    try:
        t = apis.parse_template(body.template)
    except apis.ApiError as exc:
        raise _refuse(exc) from None
    if any(p["name"].lower() == t["name"].lower() for p in apis.PRESETS):
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{t['name']}” is a built-in preset; name the template "
                                                      "something else.")
    with transaction(conn):
        old = conn.execute("SELECT id FROM api_templates WHERE name = ?", (t["name"],)).fetchone()
        if old:
            conn.execute("UPDATE api_templates SET name = ?, template = ?, uploaded_by = ?, "
                         "uploaded_at = datetime('now') WHERE id = ?",
                         (t["name"], json.dumps(t), user["username"], old["id"]))
            template_id = old["id"]
        else:
            template_id = conn.execute("INSERT INTO api_templates(name, template, uploaded_by) VALUES (?, ?, ?)",
                                       (t["name"], json.dumps(t), user["username"])).lastrowid
        _log(conn, user, "api.template", t["name"],
             f"{'replaced' if old else 'uploaded'}, {len(t['operations'])} operation(s)")
    return {"key": f"upload-{template_id}", "name": t["name"], "operations": len(t["operations"]),
            "replaced": bool(old)}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Remove an uploaded template. APIs made from it stay as they are."""
    row = conn.execute("SELECT name FROM api_templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    with transaction(conn):
        conn.execute("DELETE FROM api_templates WHERE id = ?", (template_id,))
        _log(conn, user, "api.template", row["name"], "removed")
    return {"status": "deleted"}


# ---------------------------------------------------------------------- APIs
@router.get("")
async def list_apis(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    settings = request.app.state.settings
    rows = conn.execute(
        "SELECT a.*, (SELECT count(*) FROM api_operations o WHERE o.api_id = a.id) AS operations, "
        "(SELECT count(*) FROM api_calls c WHERE c.api_id = a.id AND c.at > datetime('now', '-1 day')) AS calls_24h "
        "FROM apis a ORDER BY a.name COLLATE NOCASE").fetchall()
    out = []
    for r in rows:
        last = conn.execute("SELECT at, ok, error, via FROM api_calls WHERE api_id = ? ORDER BY id DESC LIMIT 1",
                            (r["id"],)).fetchone()
        out.append({"id": r["id"], "name": r["name"], "base_url": r["base_url"], "auth_type": r["auth_type"],
                    "ad_group": group_refs.label(conn, r["ad_group"]), "claude_enabled": bool(r["claude_enabled"]),
                    "enabled": bool(r["enabled"]), "credential": apis.credential_status(settings, r),
                    "operations": r["operations"], "calls_24h": r["calls_24h"],
                    "last_call": dict(last) if last else None})
    directory = request.app.state.directory
    presets = [{"key": p["key"], "name": p["name"], "uploaded": False} for p in apis.PRESETS]
    presets += [{"key": p["key"], "name": p["name"], "uploaded": True, "template_id": p["template_id"]}
                for p in apis.uploaded_templates(conn)]
    return {"apis": out, "presets": presets,
            "groups": group_refs.choices(conn, await anyio.to_thread.run_sync(directory.groups)),
            "domain": settings.ad_domain,
            "secret_key_set": bool(settings.secret_key)}


class NewApi(BaseModel):
    preset: str | None = None
    name: str = Field("", max_length=80)
    base_url: str = Field("", max_length=500)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api(body: NewApi, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    p = apis.preset(body.preset, conn) if body.preset else None
    if body.preset and p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such preset")
    name = (body.name or (p["name"] if p else "")).strip()
    if not name:
        raise _refuse(ValueError("Give the API a name."))
    if conn.execute("SELECT 1 FROM apis WHERE name = ?", (name,)).fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already an API called “{name}”.")
    url = _check_url(body.base_url or (p["base_url"] if p else ""))
    with transaction(conn):
        api_id = conn.execute(
            "INSERT INTO apis(name, description, base_url, auth_type, auth_header, auth_user, tls_check_hostname) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, p["description"] if p else "", url, p["auth_type"] if p else "none",
             p.get("auth_header", "") if p else "", p.get("auth_user", "") if p else "",
             p.get("tls_check_hostname", 1) if p else 1)).lastrowid
        op_ids = _preset_operations(conn, api_id, p["operations"] if p else [])
        _log(conn, user, "api.create", name, f"preset {p['key']}" if p else "")
    await _index(request, conn, op_ids)
    return {"id": api_id, "name": name}


def _preset_operations(conn: sqlite3.Connection, api_id: int, operations: list[dict]) -> list[int]:
    """Add a preset's operations to an API (inside the caller's transaction), skipping names it has."""
    have = {r[0].lower() for r in conn.execute("SELECT name FROM api_operations WHERE api_id = ?", (api_id,))}
    op_ids = []
    for op in operations:
        if op["name"].lower() in have:
            continue
        params = op.get("params", [])
        action = bool(op.get("action", False))
        apis.check_specs(op["path"], params, op.get("method", "GET"), op.get("body", ""), action)
        op_ids.append(conn.execute(
            "INSERT INTO api_operations(api_id, name, description, example_questions, method, path, params, "
            "body, cache_seconds, action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (api_id, op["name"], op.get("description", ""), op.get("example_questions", ""),
             op.get("method", "GET"), op["path"], json.dumps(params), op.get("body", ""),
             0 if action else op.get("cache_seconds", 60), int(action))).lastrowid)
    return op_ids


class PresetBody(BaseModel):
    preset: str


@router.post("/{api_id}/preset-operations")
async def add_preset_operations(api_id: int, body: PresetBody, request: Request, user=Depends(admin_user),
                                conn: sqlite3.Connection = Depends(get_db)):
    """Bring an existing API up to date with a preset: its operations this API doesn't have yet."""
    a = _api(conn, api_id)
    p = apis.preset(body.preset, conn)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such preset")
    with transaction(conn):
        op_ids = _preset_operations(conn, api_id, p["operations"])
        if op_ids:
            _log(conn, user, "api.preset_operations", a["name"], f"{len(op_ids)} from {p['key']}")
    await _index(request, conn, op_ids)
    apis.clear_cache()
    return {"added": len(op_ids)}


async def _index(request: Request, conn: sqlite3.Connection, op_ids: list[int]) -> None:
    """Embed example questions for routing (the embedder is a separate server)."""
    embedder = request.app.state.embedder

    def run():
        with transaction(conn):
            for op_id in op_ids:
                apis.index_examples(conn, embedder, op_id)
    try:
        await anyio.to_thread.run_sync(run)
    except Exception as exc:  # embedder down: the API works, routing waits for the next save
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Saved, but the example questions couldn't be indexed ({exc}); save again later.") from exc


@router.get("/{api_id}")
def api_detail(api_id: int, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    a = _api(conn, api_id)
    settings = request.app.state.settings
    ops = conn.execute("SELECT * FROM api_operations WHERE api_id = ? ORDER BY id", (api_id,)).fetchall()
    calls = conn.execute(
        "SELECT c.id, c.at, c.via, c.operation_name, c.params, c.ok, c.cached, c.status_code, c.ms, c.error, "
        "u.username FROM api_calls c LEFT JOIN users u ON u.id = c.user_id WHERE c.api_id = ? "
        "ORDER BY c.id DESC LIMIT 30", (api_id,)).fetchall()
    spec_ops = apis.openapi_operations(a["spec"]) if a["spec"] else []
    return {
        "id": a["id"], "name": a["name"], "description": a["description"],
        "description_html": markdown.render_html(a["description"]) if a["description"] else "",
        "base_url": a["base_url"], "auth_type": a["auth_type"], "auth_header": a["auth_header"],
        "auth_user": a["auth_user"], "credential": apis.credential_status(settings, a),
        "secret_changed_at": a["secret_changed_at"], "ca_pem": a["ca_pem"],
        "tls_check_hostname": bool(a["tls_check_hostname"]), "ad_group": group_refs.label(conn, a["ad_group"]),
        "claude_enabled": bool(a["claude_enabled"]), "enabled": bool(a["enabled"]),
        "spec": a["spec"], "spec_html": "" if spec_ops or not a["spec"] else markdown.render_html(a["spec"]),
        "spec_operations": spec_ops,
        "operations": [_op_json(o) for o in ops], "calls": [dict(c) for c in calls],
    }


class ApiPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=20000)
    base_url: str | None = Field(None, max_length=500)
    auth_type: Literal["none", "bearer", "header", "basic"] | None = None
    auth_header: str | None = Field(None, max_length=80)
    auth_user: str | None = Field(None, max_length=200)
    ca_pem: str | None = Field(None, max_length=20000)
    tls_check_hostname: bool | None = None
    ad_group: str | None = Field(None, max_length=2000)   # ';'-separated, AD or "Tenant / Group"
    claude_enabled: bool | None = None
    enabled: bool | None = None
    spec: str | None = Field(None, max_length=2_000_000)


@router.patch("/{api_id}")
async def update_api(api_id: int, body: ApiPatch, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    a = _api(conn, api_id)
    changes = body.model_dump(exclude_none=True)
    if "base_url" in changes:
        changes["base_url"] = _check_url(changes["base_url"])
    if "ad_group" in changes:
        known = await anyio.to_thread.run_sync(request.app.state.directory.groups)
        try:
            changes["ad_group"] = group_refs.resolve(conn, changes["ad_group"], known)
        except group_refs.GroupRefError as exc:
            raise _refuse(ValueError(str(exc))) from None
    if changes.get("ca_pem"):
        try:
            import ssl
            ssl.create_default_context(cadata=changes["ca_pem"])
        except (ssl.SSLError, ValueError) as exc:
            raise _refuse(ValueError("That isn't a PEM certificate.")) from exc
    if "auth_header" in changes and changes["auth_header"] and \
            not all(c.isalnum() or c == "-" for c in changes["auth_header"]):
        raise _refuse(ValueError("A header name has only letters, digits and dashes."))
    if not changes:
        return {"status": "unchanged"}
    for key in ("tls_check_hostname", "claude_enabled", "enabled"):
        if key in changes:
            changes[key] = int(changes[key])
    with transaction(conn):
        sets = ", ".join(f"{k} = ?" for k in changes)
        try:
            conn.execute(f"UPDATE apis SET {sets}, updated_at = datetime('now') WHERE id = ?",
                         (*changes.values(), api_id))
        except sqlite3.IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "There is already an API with that name.") from None
        for key, label in (("ad_group", "api.group"), ("claude_enabled", "api.claude"), ("enabled", "api.enabled"),
                           ("base_url", "api.address"), ("ca_pem", "api.certificate")):
            if key in changes and changes[key] != a[key]:
                _log(conn, user, label, a["name"], "" if key == "ca_pem" else f"{a[key]} → {changes[key]}")
    apis.clear_cache()
    return {"status": "saved"}


class Credential(BaseModel):
    secret: str = Field("", max_length=4000)


@router.put("/{api_id}/credential")
def set_credential(api_id: int, body: Credential, request: Request, user=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    """Replace (or, empty, clear) the credential. It is never sent back."""
    a = _api(conn, api_id)
    try:
        blob = apis.encrypt_secret(request.app.state.settings, body.secret.strip()) if body.secret.strip() else None
    except apis.ApiError as exc:
        raise _refuse(exc) from None
    with transaction(conn):
        conn.execute("UPDATE apis SET secret_enc = ?, secret_changed_at = CASE WHEN ? IS NULL THEN NULL "
                     "ELSE datetime('now') END WHERE id = ?", (blob, blob, api_id))
        _log(conn, user, "api.credential", a["name"], "replaced" if blob else "cleared")
    apis.clear_cache()
    return {"credential": "set" if blob else "missing"}


@router.post("/{api_id}/fetch-certificate")
async def fetch_certificate(api_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """The certificate the server presents, for the admin to check and trust."""
    a = _api(conn, api_id)
    try:
        pem, fingerprint = await anyio.to_thread.run_sync(apis.fetch_certificate, a["base_url"])
    except apis.ApiError as exc:
        raise _refuse(exc) from None
    return {"pem": pem, "fingerprint": fingerprint}


@router.delete("/{api_id}")
def delete_api(api_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    a = _api(conn, api_id)
    with transaction(conn):
        conn.execute("DELETE FROM apis WHERE id = ?", (api_id,))   # operations and examples cascade
        _log(conn, user, "api.delete", a["name"])
    apis.clear_cache()
    return {"status": "deleted"}


# ---------------------------------------------------------------- operations
class Operation(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=2000)
    example_questions: str = Field("", max_length=4000)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = Field(..., min_length=1, max_length=500)
    params: list[dict] = Field(default_factory=list, max_length=30)
    body: str = Field("", max_length=20000)
    cache_seconds: int = Field(60, ge=0, le=86400)
    enabled: bool = True
    action: bool = False    # changes data: proposed by Erynne or Claude, run only when the person applies it


def _save_op(conn, api_id: int, op_id: int | None, body: Operation) -> int:
    try:
        apis.check_specs(body.path, body.params, body.method, body.body, body.action)
    except apis.ApiError as exc:
        raise _refuse(exc) from None
    values = (body.name.strip(), body.description, body.example_questions, body.method, body.path,
              json.dumps(body.params), body.body if body.method in apis.BODY_METHODS else "",
              0 if body.action else body.cache_seconds, int(body.enabled), int(body.action))
    try:
        if op_id is None:
            return conn.execute(
                "INSERT INTO api_operations(name, description, example_questions, method, path, params, body, "
                "cache_seconds, enabled, action, api_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, api_id)).lastrowid
        conn.execute("UPDATE api_operations SET name = ?, description = ?, example_questions = ?, method = ?, "
                     "path = ?, params = ?, body = ?, cache_seconds = ?, enabled = ?, action = ? WHERE id = ?",
                     (*values, op_id))
        return op_id
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already an operation called “{body.name}”.") from None


@router.post("/{api_id}/operations", status_code=status.HTTP_201_CREATED)
async def add_operation(api_id: int, body: Operation, request: Request, user=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    a = _api(conn, api_id)
    with transaction(conn):
        op_id = _save_op(conn, api_id, None, body)
        _log(conn, user, "api.operation", a["name"], f"added {body.method} {body.path}")
    await _index(request, conn, [op_id])
    return {"id": op_id}


@router.put("/operations/{op_id}")
async def update_operation(op_id: int, body: Operation, request: Request, user=Depends(admin_user),
                           conn: sqlite3.Connection = Depends(get_db)):
    op = _op(conn, op_id)
    with transaction(conn):
        _save_op(conn, op["api_id"], op_id, body)
        _log(conn, user, "api.operation", op["api_name"], f"changed {body.name}: {body.method} {body.path}")
    apis.clear_cache()
    await _index(request, conn, [op_id])
    return {"id": op_id}


@router.delete("/operations/{op_id}")
def delete_operation(op_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    op = _op(conn, op_id)
    with transaction(conn):
        conn.execute("DELETE FROM api_operations WHERE id = ?", (op_id,))
        _log(conn, user, "api.operation", op["api_name"], f"removed {op['name']}")
    return {"status": "deleted"}


class TestCall(BaseModel):
    params: dict = Field(default_factory=dict)
    really_change: bool = False   # an action's test really does it: the page asks first


@router.post("/operations/{op_id}/test")
async def test_operation(op_id: int, body: TestCall, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    """Call it now (never from the cache) and show the response and its fields."""
    op = _op(conn, op_id)
    if op["action"] and not body.really_change:
        raise _refuse(ValueError(f"“{op['name']}” changes data in {op['api_name']}: testing it really does it."))
    settings = request.app.state.settings
    try:
        result = await anyio.to_thread.run_sync(
            lambda: apis.call(conn, settings, op_id, body.params, user_id=user["id"], via="test"))
    except apis.ApiError as exc:
        raise _refuse(exc) from None
    data = result.data
    return {"ok": result.ok, "status": result.status_code, "error": result.error, "call_id": result.call_id,
            "truncated": result.truncated,
            "response": (json.dumps(data, indent=1, ensure_ascii=False) if not isinstance(data, str)
                         else data or "")[:apis.EXCERPT_CHARS] if result.ok else "",
            "fields": [{"path": p, "type": t} for p, t in apis.infer_shape(data)] if result.ok else []}
