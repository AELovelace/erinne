"""ONLYOFFICE Docs: Word, Excel and PowerPoint files viewed and edited in the browser, saved as versions.

How it fits together (help/admin/295-onlyoffice.md has the walkthrough):
  * The Document Server runs in a podman container on the box (deploy/linux/onlyoffice-setup.sh). Browsers reach it
    at onlyoffice_url (the edge's /onlyoffice/, only for signed-in people); it reaches us at onlyoffice_app_url.
  * /office/{id} (web/office.py) draws the page; its config is signed here with the shared JWT secret and names the
    file's document key, a download URL and a callback URL, both carrying a token of ours as well.
  * The Document Server fetches the file (download_token) and, when people save or close, POSTs the callback:
    status 1 = who has it open, 2 = everyone closed and it changed, 4 = closed unchanged, 6 = force-save (Ctrl+S).
    2 and 6 hand us a URL of the edited file; it's fetched and saved with editing.replace_from_path as the person
    who edited it (via 'onlyoffice'), so it lands in Versions and is indexed again like any edit.
  * One session per file (office_sessions): everyone who opens it while it lives shares its key and edits together,
    and editing.py refuses every other writer (Claude, the drive, the text editor) until it ends.
  * The version check always wins: if the file changed under the editor anyway, or the editor's key is stale, or the
    person can no longer edit there, nothing is overwritten. The edit becomes a copy next to the file ("Name (editor
    copy, alice 2026-11-26).xlsx"), or in their own inbox when they can't write there, and they're told.

Off until onlyoffice_url and onlyoffice_jwt_secret are set AND an admin turns it on (meta 'onlyoffice_on'); an
optional group (meta 'onlyoffice_group') limits who sees it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from itsdangerous import BadSignature, URLSafeTimedSerializer

from mnemerynne import editing, fs, group_refs, messages
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store

log = logging.getLogger(__name__)

ON_KEY = "onlyoffice_on"                  # meta: '1' when an admin turned the editor on
GROUP_KEY = "onlyoffice_group"            # meta: group_refs text; '' = everyone who can reach the file
LOCK_HOURS = editing.OFFICE_LOCK_HOURS    # a session with no word from the Document Server this long is dead
TOKEN_AGE = 4 * 3600                      # our download/callback tokens; the Document Server calls back hours later
CALLBACK_AGE = 7 * 24 * 3600              # a callback may come long after opening (an editor left open overnight)
JWT_AGE = 24 * 3600                       # tokens we sign for the browser config

# extension -> (documentType, may we edit it here). Only the OOXML formats are edited: saving .doc/.xls/.odt in
# ONLYOFFICE converts them, which would quietly change the file's format, so they're view-only.
KINDS: dict[str, tuple[str, bool]] = {
    ".docx": ("word", True), ".xlsx": ("cell", True), ".pptx": ("slide", True),
    ".doc": ("word", False), ".docm": ("word", False), ".dotx": ("word", False), ".odt": ("word", False),
    ".rtf": ("word", False),
    ".xls": ("cell", False), ".xlsm": ("cell", False), ".ods": ("cell", False), ".csv": ("cell", False),
    ".ppt": ("slide", False), ".pptm": ("slide", False), ".odp": ("slide", False),
}


class OfficeError(ValueError):
    """Refused; the message is safe to show to the person."""
    status = 400


class OfficeForbidden(OfficeError):
    status = 403


# ------------------------------------------------------------------ JWT (HS256)
# The Document Server speaks HS256 JWTs. They're a few lines with hmac, so no extra package on the box.
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")      # base64url without padding


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))           # padding put back before decoding


def jwt_encode(payload: dict, secret: str, *, ttl: int | None = JWT_AGE) -> str:
    """Sign `payload` (adds iat/exp when ttl is set)."""
    body = dict(payload)
    if ttl:
        now = int(time.time())
        body.setdefault("iat", now)
        body.setdefault("exp", now + ttl)
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    data = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), f"{head}.{data}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{data}.{_b64(sig)}"


def jwt_decode(token: str, secret: str) -> dict:
    """The payload of a token signed with `secret`; OfficeForbidden for anything else (wrong signature, another
    algorithm, expired, garbage)."""
    if not secret:
        raise OfficeForbidden("The Office editor isn't configured.")
    try:
        head_b, data_b, sig_b = (token or "").split(".")
        head = json.loads(_unb64(head_b))
        if head.get("alg") != "HS256":                                       # never 'none' or anything else
            raise ValueError("alg")
        want = hmac.new(secret.encode("utf-8"), f"{head_b}.{data_b}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(sig_b)):                     # constant-time signature check
            raise ValueError("sig")
        payload = json.loads(_unb64(data_b))
        if not isinstance(payload, dict):
            raise ValueError("payload")
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise OfficeForbidden("Bad or missing signature.") from None
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time() - 60:              # a minute of clock slack
        raise OfficeForbidden("The signature has expired.")
    return payload


def bearer(header: str | None) -> str | None:
    """The token in an 'Authorization: Bearer …' header, or None."""
    value = (header or "").strip()
    return value[7:].strip() if value.lower().startswith("bearer ") else None


# ------------------------------------------------------------------ settings
def configured(settings: Settings) -> bool:
    """Both halves of the env are there (onlyoffice-setup.sh writes them)."""
    return bool(settings.onlyoffice_url.strip() and settings.onlyoffice_jwt_secret.strip())


def is_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ON_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_on(conn: sqlite3.Connection, on: bool) -> None:
    if on:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, '1')", (ON_KEY,))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (ON_KEY,))


def group(conn: sqlite3.Connection) -> str:
    """The group allowed to use the editor (group_refs text); '' = everyone."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (GROUP_KEY,)).fetchone()
    return (row["value"] if row else "") or ""


def set_group(conn: sqlite3.Connection, value: str) -> str:
    value = (value or "").strip()
    if value:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (GROUP_KEY, value))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (GROUP_KEY,))
    return value


def live(conn: sqlite3.Connection, settings: Settings) -> bool:
    """The editor is offered at all: configured and switched on."""
    return configured(settings) and is_on(conn)


def allowed(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory) -> bool:
    """This person may use the editor (the file's own rights are checked separately)."""
    if not live(conn, settings):
        return False
    value = group(conn)
    return not value or group_refs.in_any(conn, user, directory, value)


def kind_of(name: str) -> tuple[str, bool] | None:
    """(documentType, editable) for a file name, or None when ONLYOFFICE isn't used for it."""
    return KINDS.get(PurePosixPath(name or "").suffix.lower())


def mode_for(conn: sqlite3.Connection, access: fs.Access, row: sqlite3.Row) -> str | None:
    """'edit', 'view', or None (not an Office file). Callers have checked the person can read it."""
    found = kind_of(row["display_name"])
    if found is None:
        return None
    writable = found[1] and access.can_write_file(row) and fs.is_live_folder(conn, row["folder_id"])
    return "edit" if writable else "view"


def _base(url: str) -> str:
    return url.rstrip("/") + "/"


def api_js(settings: Settings) -> str:
    """The Document Server's editor script, which the page loads."""
    return _base(settings.onlyoffice_url) + "web-apps/apps/api/documents/api.js"


def app_url(settings: Settings, fallback: str = "") -> str:
    """Where the container calls us (download + callback): onlyoffice_app_url, else public_url, else `fallback`
    (the address the page was opened at): the Document Server needs absolute URLs."""
    return (settings.onlyoffice_app_url or settings.public_url or fallback or "").rstrip("/")


def internal_url(settings: Settings) -> str:
    """Where this server reaches the Document Server (health checks, fetching edited files)."""
    return _base(settings.onlyoffice_internal_url or settings.onlyoffice_url)


def ip_allowed(settings: Settings, host: str | None) -> bool:
    """The request came from where the container lives (onlyoffice_allowed_ips)."""
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return False
    for cidr in (settings.onlyoffice_allowed_ips or "").split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            log.warning("onlyoffice_allowed_ips has a bad entry: %s", cidr)
    return False


# ------------------------------------------------------------------ our own URL tokens
def _signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key or "dev-only", salt="office-v1")


def url_token(settings: Settings, file_id: int, doc_key: str, purpose: str) -> str:
    """Binds a download or callback URL to one file and one session key."""
    return _signer(settings).dumps({"f": file_id, "k": doc_key, "p": purpose})


def check_url_token(settings: Settings, token: str, file_id: int, purpose: str, max_age: int) -> str:
    """The session key the token was made for; OfficeForbidden if it isn't ours, for this file and purpose."""
    try:
        data = _signer(settings).loads(token or "", max_age=max_age)
    except BadSignature:
        raise OfficeForbidden("Bad or expired link.") from None
    if not isinstance(data, dict) or data.get("f") != file_id or data.get("p") != purpose:
        raise OfficeForbidden("Bad link.")
    return str(data.get("k") or "")


# ------------------------------------------------------------------ sessions
@dataclass
class Session:
    file_id: int
    doc_key: str
    base_sha256: str
    users: list[str]


def _row_session(row: sqlite3.Row | None) -> Session | None:
    if row is None:
        return None
    try:
        users = [str(u) for u in json.loads(row["users_json"] or "[]")]
    except (ValueError, TypeError):
        users = []
    return Session(row["file_id"], row["doc_key"], row["base_sha256"], users)


def session(conn: sqlite3.Connection, file_id: int) -> Session | None:
    """The file's session while it's alive (heard from within LOCK_HOURS)."""
    row = conn.execute("SELECT * FROM office_sessions WHERE file_id = ? AND last_status_at > datetime('now', ?)",
                       (file_id, f"-{LOCK_HOURS} hours")).fetchone()
    return _row_session(row)


def open_session(conn: sqlite3.Connection, file_id: int, current_sha: str, user_id: int) -> Session:
    """The session someone joins when they open the file: the live one (they edit together), or a new key when there
    is none, it died, or nobody had really opened it and the file changed since."""
    with transaction(conn):
        found = session(conn, file_id)
        if found and (found.users or found.base_sha256 == current_sha):
            return found                                                       # join it: same key, co-editing
        key = f"{file_id}-{current_sha[:20]}-{secrets.token_hex(3)}"           # new: stale keys can never match it
        conn.execute("INSERT OR REPLACE INTO office_sessions(file_id, doc_key, base_sha256, opened_by) "
                     "VALUES (?, ?, ?, ?)", (file_id, key, current_sha, user_id))
        return Session(file_id, key, current_sha, [])


def end_session(conn: sqlite3.Connection, file_id: int, doc_key: str | None = None) -> None:
    """Forget a file's session (only that key's, when given: a newer session stays)."""
    if doc_key is None:
        conn.execute("DELETE FROM office_sessions WHERE file_id = ?", (file_id,))
    else:
        conn.execute("DELETE FROM office_sessions WHERE file_id = ? AND doc_key = ?", (file_id, doc_key))


def open_sessions(conn: sqlite3.Connection) -> list[dict]:
    """Every live session, for Settings (who has what open)."""
    rows = conn.execute(
        "SELECT s.*, f.display_name, u.username AS opener FROM office_sessions s JOIN files f ON f.id = s.file_id "
        "LEFT JOIN users u ON u.id = s.opened_by WHERE s.last_status_at > datetime('now', ?) ORDER BY s.opened_at",
        (f"-{LOCK_HOURS} hours",)).fetchall()
    out = []
    for r in rows:
        s = _row_session(r)
        out.append({"file_id": r["file_id"], "name": r["display_name"], "opened_by": r["opener"],
                    "opened_at": r["opened_at"], "last_heard": r["last_status_at"],
                    "editing": _usernames(conn, s.users)})
    return out


def _usernames(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    names = []
    for uid in ids:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone() if uid.isdigit() else None
        names.append(row["username"] if row else uid)
    return names


# ------------------------------------------------------------------ the editor config
def editor_config(settings: Settings, row: sqlite3.Row, user: sqlite3.Row, mode: str, sess: Session,
                  *, lang: str = "en", back_url: str = "", fallback_base: str = "") -> dict:
    """The DocsAPI.DocEditor config for this person, signed (`token`) so the Document Server trusts it."""
    doc_type, _ = kind_of(row["display_name"])
    ext = PurePosixPath(row["display_name"]).suffix.lower().lstrip(".")
    base = app_url(settings, fallback_base)
    file_id = row["id"]
    edit = mode == "edit"
    config = {
        "documentType": doc_type,
        "document": {
            "fileType": ext,
            "key": sess.doc_key,                                               # same key = same shared document
            "title": row["display_name"],
            "url": f"{base}/api/office/ds/file/{file_id}?t={url_token(settings, file_id, sess.doc_key, 'dl')}",
            "permissions": {"edit": edit, "download": True, "print": True, "review": edit, "comment": edit,
                            "fillForms": edit, "modifyFilter": edit, "chat": edit},
        },
        "editorConfig": {
            "mode": mode,
            "lang": lang,
            "user": {"id": str(user["id"]), "name": user["username"]},
            "customization": {"forcesave": edit, "autosave": True, "compactHeader": True,
                              "goback": {"url": back_url, "blank": False} if back_url else False},
        },
    }
    if edit:                                                                     # viewers never save
        config["editorConfig"]["callbackUrl"] = (
            f"{base}/api/office/ds/callback/{file_id}?t={url_token(settings, file_id, sess.doc_key, 'cb')}")
    config["token"] = jwt_encode(config, settings.onlyoffice_jwt_secret)
    return config


# ------------------------------------------------------------------ the save callback
@dataclass
class Callback:
    """A verified callback body (from inside its JWT, never the unsigned outer body)."""
    status: int
    key: str
    url: str
    users: list[str]
    forcesave_type: int | None


def verify_callback(settings: Settings, body: dict, auth_header: str | None) -> Callback:
    """The callback's signed content. ONLYOFFICE puts the JWT in the body ('token') and/or the Authorization header
    (whose payload wraps the body as 'payload'). Unsigned → OfficeForbidden."""
    secret = settings.onlyoffice_jwt_secret
    if isinstance(body.get("token"), str):
        data = jwt_decode(body["token"], secret)
    elif (tok := bearer(auth_header)) is not None:
        data = jwt_decode(tok, secret)
        data = data.get("payload", data) if isinstance(data.get("payload"), dict) else data
    else:
        raise OfficeForbidden("Unsigned callback.")
    try:
        status = int(data.get("status"))
    except (TypeError, ValueError):
        raise OfficeError("No status.") from None
    users = [str(u) for u in (data.get("users") or []) if str(u).strip()]
    ft = data.get("forcesavetype")
    return Callback(status, str(data.get("key") or ""), str(data.get("url") or ""), users,
                    int(ft) if isinstance(ft, (int, float, str)) and str(ft).isdigit() else None)


def fetch_url(settings: Settings, url: str) -> str:
    """The edited file's URL rewritten to where this server reaches the Document Server, refused unless it points
    there (a forged callback must never make us fetch something else). The Document Server builds the URL from the
    public address (the edge's /onlyoffice/, which needs a signed-in browser), so that prefix is swapped for the
    internal one."""
    public, internal = _base(settings.onlyoffice_url), internal_url(settings)
    if url.startswith(public):
        url = internal + url[len(public):]                                     # public path → loopback port
    parts = urlsplit(url)
    hosts = {h.strip().lower() for h in (settings.onlyoffice_fetch_hosts or "").split(",") if h.strip()}
    hosts |= {urlsplit(internal).netloc.lower(), urlsplit(public).netloc.lower()}
    if parts.scheme not in ("http", "https") or parts.netloc.lower() not in hosts:
        raise OfficeForbidden("The edited file isn't on the Document Server.")
    return urlunsplit(parts)


def download(settings: Settings, url: str) -> tuple[Path, str]:
    """Fetch the edited file to a temp file, hashing it and holding it to max_file_bytes. Returns (path, sha256);
    the caller owns the file. Tests replace this."""
    import httpx

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    path = settings.tmp_dir / f"office-{secrets.token_hex(8)}.part"
    h, size = hashlib.sha256(), 0
    try:
        with httpx.stream("GET", url, timeout=120, follow_redirects=False) as resp, open(path, "xb") as fh:
            if resp.status_code != 200:
                raise OfficeError(f"The Document Server answered {resp.status_code} for the edited file.")
            for chunk in resp.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > settings.max_file_bytes:
                    raise OfficeError("The edited file is bigger than the upload limit.")
                h.update(chunk)
                fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)                                           # never leave half a file
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise OfficeError("The Document Server sent an empty file.")
    return path, h.hexdigest()


@dataclass
class Outcome:
    """What a callback did, for the reply and the log."""
    result: str                       # 'noted' | 'saved' | 'copy' | 'ignored'
    detail: str = ""
    file_id: int | None = None        # the copy's id when result == 'copy'


def note_status(conn: sqlite3.Connection, file_id: int, cb: Callback) -> Outcome:
    """Status 1 (someone joined or left) and 4 (closed, nothing changed): bookkeeping only."""
    with transaction(conn):
        if cb.status == 4:
            end_session(conn, file_id, cb.key)                                 # closed unchanged: lock off
            return Outcome("noted", "closed without changes")
        if cb.status == 1:
            conn.execute("UPDATE office_sessions SET users_json = ?, last_status_at = datetime('now') "
                         "WHERE file_id = ? AND doc_key = ?", (json.dumps(cb.users), file_id, cb.key))
            return Outcome("noted", "editing")
    return Outcome("ignored", f"status {cb.status}")


def copy_name(display_name: str, username: str, when: datetime | None = None) -> str:
    """'Budget.xlsx' → 'Budget (editor copy, alice 2026-11-26).xlsx'."""
    p = PurePosixPath(display_name)
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"{p.stem} (editor copy, {username} {day}){p.suffix}"


def _spare(src: Path) -> Path:
    """A second name for the fetched edit (a hard link: no copy; a copy where links can't be made), so it survives
    replace_from_path deleting `src` when the save is refused."""
    spare = src.with_name(src.name + ".spare")
    try:
        os.link(src, spare)
    except OSError:
        shutil.copyfile(src, spare)
    return spare


def _inbox(conn: sqlite3.Connection, user_id: int) -> int | None:
    row = conn.execute("SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = ?", (user_id,)).fetchone()
    return row["id"] if row else None


def save_edit(conn: sqlite3.Connection, settings: Settings, file_id: int, cb: Callback, src: Path, sha: str,
              *, may_edit) -> Outcome:
    """Status 2/6: store the fetched edit (`src`, consumed) as a new version of the file, as the person who made it.
    `may_edit(user_row, file_row) -> bool` re-checks their rights now (with the group gate). Whatever stops a clean
    save (stale key, changed file, lost rights) turns the edit into a copy; nothing is overwritten or lost."""
    try:
        row = fs.file_row(conn, file_id)
        editor_id = next((u for u in cb.users if u.isdigit()), None)           # the last person who edited
        user = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1",
                            (int(editor_id),)).fetchone() if editor_id else None
        if row is None or user is None:
            log.warning("onlyoffice: save for file %s by %s dropped (file or user gone)", file_id, editor_id)
            with transaction(conn):
                end_session(conn, file_id, cb.key)
            return Outcome("ignored", "file or user gone")
        sess = session(conn, file_id)
        names = ", ".join(_usernames(conn, cb.users)) or user["username"]
        reason = None
        if sess is None or sess.doc_key != cb.key:
            reason = "its editing session had already ended (the file was changed or restored meanwhile)"
        elif row["deleted_at"] is not None:
            reason = "the file was moved to the bin meanwhile"
        elif not may_edit(user, row):
            reason = "you can't change that file any more"
        if reason is None:
            spare = _spare(src)                                                # replace_from_path consumes src
            try:
                new_sha = editing.replace_from_path(
                    conn, settings, file_id, src, sha, expected=sess.base_sha256, actor_id=user["id"],
                    via="onlyoffice", summary=f"Edited in ONLYOFFICE ({names})",
                    edited_via=f"onlyoffice:{user['username']}")
            except editing.EditConflict as exc:
                spare.replace(src)                                             # the edit is back for the copy
                if "changed since" in str(exc):                                # the version check won
                    reason = "it changed while you were editing (someone else saved it)"
                elif "open in Word or Excel" in str(exc):                      # a ~$ lock on the share
                    reason = "it's open in Word or Excel on the share right now"
                else:
                    raise                                                      # share unavailable: worth a retry
            except BaseException:
                spare.replace(src)                                             # transient (share down): retried
                raise
            else:
                spare.unlink(missing_ok=True)
                with transaction(conn):
                    if cb.status == 6:                                         # force-save: they're still editing
                        conn.execute("UPDATE office_sessions SET base_sha256 = ?, last_status_at = datetime('now') "
                                     "WHERE file_id = ? AND doc_key = ?", (new_sha, file_id, cb.key))
                    else:
                        end_session(conn, file_id, cb.key)                     # everyone closed: lock off
                return Outcome("saved", new_sha)
        return _save_copy(conn, settings, row, user, src, cb, reason)
    finally:
        src.unlink(missing_ok=True)


def _save_copy(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row, user: sqlite3.Row, src: Path,
               cb: Callback, reason: str) -> Outcome:
    """Keep a refused edit as a new file: beside the original when they may write there, else in their inbox."""
    name = copy_name(row["display_name"], user["username"])
    access = fs.Access.of(conn, user["id"])
    beside = row["deleted_at"] is None and access.can_write(row["collection_id"], row["folder_id"])
    collection_id, folder_id = (row["collection_id"], row["folder_id"]) if beside else (_inbox(conn, user["id"]), None)
    if collection_id is None:
        log.error("onlyoffice: no place for %s's copy of file %s", user["username"], row["id"])
        return Outcome("ignored", "no inbox")
    received = store.received_from(src, name, settings, staged=False)           # hashed and sniffed like an upload
    new_id, _ = store.add_file(conn, settings, received, collection_id, user["id"], folder_id=folder_id)  # moves src
    with transaction(conn):
        end_session(conn, row["id"], cb.key)                                   # this session's edits went elsewhere
        where = "next to it" if beside else "in your inbox"
        messages.system(conn, user["id"],
                        f"Your ONLYOFFICE edit of “{row['display_name']}” couldn't be saved over it because {reason}. "
                        f"Nothing was lost: it's saved as “{name}” {where}.",
                        f"/files/{new_id}/view")
    log.info("onlyoffice: edit of file %s by %s kept as copy %s (%s)", row["id"], user["username"], new_id, reason)
    return Outcome("copy", reason, new_id)
