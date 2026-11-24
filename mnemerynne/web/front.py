"""What every Mnemerynne process puts in front of its routes, and every page's templates: shared by the all-in-one
web app (web/app.py) and each service (services/factory.py).

Kept apart from web/app.py so a service imports only this and its own routers, not the whole web app: Ask's
LangGraph and agent and Claude's MCP SDK cost a process about 100 MB it doesn't need (docs/plans/services-rewrite.md,
Phase 11).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from mnemerynne import (automations as automations_mod, media as media_mod, messages as messages_mod, remote,
                        rooms as rooms_mod, sync as sync_mod, theme as theme_mod, users)
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.config import Settings
from mnemerynne.media import screens as media_screens
from mnemerynne.web import deps, security, sendfile, theme as theme_pages
from mnemerynne.web.deps import LoginRequired, is_admin

log = logging.getLogger("mnemerynne.web")

_HERE = Path(__file__).parent
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Called by Claude's servers with OAuth credentials, never with the session cookie.
_OAUTH_API = {"/oauth/token", "/oauth/register", "/oauth/revoke"}


def _safe_return(after: str) -> bool:
    """A place sign-in may send someone back to: the page on this site they were opening (a link from
    Claude, Claude's consent page, a remote app); never another site (open redirect), the sign-in pages
    themselves, or anything with a scheme, a backslash or control characters."""
    if not after.startswith("/") or urlsplit(after).path in ("/login", "/logout"):
        return False
    parts = urlsplit(after)
    return not (after.startswith("//") or "\\" in after or parts.scheme or parts.netloc
                or any(ord(c) < 32 or c == "\x7f" for c in after))


def _cross_site(request: Request) -> bool:
    """True for a state-changing request another site's page made the browser send.

    Browsers label requests with Sec-Fetch-Site and Origin; requests without
    either (curl, scripts) can't ride on someone's session cookie anyway.
    Hostnames only: nginx forwards Host without the port.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    if origin:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return (urlsplit(origin).hostname or "") != host
    return False



@dataclass
class Services:
    """Model clients; tests pass fakes."""
    llm: Any
    embedder: Any
    reranker: Any = None
    router: Any = None
    directory: Any = None   # the AD domain (mnemerynne.directory); None = from settings
    share_checker: Any = None   # share permission checks (share.Checker); None = the real POSIX ACLs
    agent_llm: Any = None   # the agent's model client; None = the balancer nodes, then MNEMERYNNE_LLM_URL
    claude_llm: Any = None  # Claude's client in chat (tests); None = the Anthropic API with each person's key
    balancer: Any = None    # balancer.Balancer (tests pass one with a fake transport); None = a real one
    bfgguf_transport: Any = None   # the HTTP transport under BFGguf's clients (tests); None = the network



def _guard(app: FastAPI, settings: Settings, secret_key: str) -> None:
    """What every Mnemerynne process puts in front of its routes (create_app and create_role_app): the sign-in
    cookie, the cross-site check, the request limits and headers, the backup host's fence, the slow-request log,
    and the answers for "sign in first" and "the database is busy". Kept in one place so a role's process can't
    drift from the web app's."""
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="mnemerynne_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=security.https_only(settings),
    )

    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):
        # The session cookie is SameSite=lax, which still lets other sites'
        # top-level form POSTs through; refuse anything cross-site that changes data.
        if request.method not in _SAFE_METHODS and _cross_site(request) and request.url.path not in _OAUTH_API:
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)

    # Behind the front door with MNEMERYNNE_ACCEL: files go out through nginx (send_file), not Python.
    app.add_middleware(sendfile.Accel, settings=settings)

    # Added last, so they run first: refused requests get the headers too.
    app.add_middleware(security.GamesShed)       # games can't queue without bound or take every worker thread
    app.add_middleware(security.PublicRateLimit)
    app.add_middleware(security.SecurityHeaders, settings=settings)
    # The backup host (MNEMERYNNE_BACKUP_URL) answers only the backup app's API (sync.py).
    app.add_middleware(sync_mod.BackupHost, host=sync_mod.backup_host(settings))
    app.add_middleware(security.SlowRequestLog)  # outermost: times every request ("Slow request" in the journal)

    @app.exception_handler(LoginRequired)
    async def _redirect_to_login(request: Request, exc: LoginRequired):
        # Back to this page after signing in: a link opened from Claude Desktop lands where it pointed.
        if request.method == "GET":
            request.session["after_login"] = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(sqlite3.OperationalError)
    async def _database_busy(request: Request, exc: sqlite3.OperationalError):
        """A write that waited out busy_timeout for the lock: a short JSON 503 "try again", not a plain-text 500.

        Pages read a JSON refusal's status. The plain-text 500 had none, and Lumi Pet's party feed took a failure
        without a status as "the party is over": it reloaded at once, over and over, and every reload waited on the
        same lock. A storm that fed itself, until the app ran out of memory. Any other database error is a bug,
        and goes on to the usual 500 with its traceback."""
        text = str(exc).lower()
        if "locked" not in text and "busy" not in text:
            raise exc                                  # not a lock: the normal error path (logged with its traceback)
        log.warning("Database busy: %s %s (%s)", request.method, request.url.path, exc)   # one line, no traceback
        return JSONResponse({"detail": "The server is busy for a moment. Try again shortly."},
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Retry-After": "2"})



def make_templates(app: FastAPI) -> Jinja2Templates:
    """The pages' templates with every page's top bar (the look, the badge, Rooms, Media, Remote; on a TV its
    screen): the web app's, and a service's that serves pages of its own (the media service, services/hooks.py)."""
    settings = app.state.settings

    def _tv(request: Request, conn: sqlite3.Connection) -> dict | None:
        """This browser is a screen (the TV, by its key): its underscan and text size on every page, and on a
        screen box the small bar and the remote (base.html). A built-in game frame inherits text size only."""
        framed = request.headers.get("sec-fetch-dest") == "iframe"
        if framed and not request.url.path.startswith(("/media/games/", "/media/retro")):   # Retro: the TV's game layer too
            return None
        screen = media_screens.by_token(conn, request.cookies.get(media_screens.COOKIE))
        if screen is None:
            return None
        return {"id": screen["id"], "name": screen["name"],
                "safe_x": 0 if framed else screen["safe_x"], "safe_y": 0 if framed else screen["safe_y"],
                "ui_scale": screen["ui_scale"], "box": bool(screen["is_box"]) and not framed, "framed": framed}

    def _nav_context(request: Request) -> dict:
        """What every page's top bar needs beyond its own context: the badge on the Messages button
        (unread notes, and with Rooms on, mentions and unread DMs), whether Rooms and Remote are there;
        and on a screen, the screen (tv)."""
        session = request.scope.get("session", {}) if "session" in request.scope else {}
        user_id = session.get("user_id")
        # The look (theme) and the brand: every page, signed in or not (the sign-in page follows the cookie).
        looks = theme_pages.looks_for(app)
        cookie = request.cookies.get(theme_mod.THEME_COOKIE)
        out = {"look": looks.resolve(None, cookie), "brand": looks.brand,
               "product_name": looks.brand.get("name") or ASSISTANT_NAME}
        if not user_id and not request.cookies.get(media_screens.COOKIE):
            return out
        conn = app.state.db_pool.get()
        try:
            out["tv"] = _tv(request, conn)
            user = users.get_user_by_id(conn, user_id) if user_id else None
            if user is None:
                return out
            out["look"] = looks.resolve(user["theme"] or None, cookie)
            admin = is_admin(request, conn, user)
            rooms_pool = deps.feature_pool(app, "rooms")   # Rooms' unread counts live in rooms.db (Phase 3)
            rooms_conn = rooms_pool.get()
            try:
                unread_rooms = rooms_mod.unread_summary(rooms_conn, user, app.state.directory)
            finally:
                rooms_pool.put(rooms_conn)
            # Proposals tab badge: the person's own proposals still waiting (the same list /proposals shows first).
            nav_proposals = conn.execute("SELECT COUNT(*) FROM agent_proposals WHERE user_id = ? AND status = 'pending' "
                                         "AND (expires_at IS NULL OR expires_at > datetime('now'))",   # not ones past their time
                                         (user_id,)).fetchone()[0]
            return out | {"nav_unread": messages_mod.unread_count(conn, user_id) + unread_rooms["mentions"] + unread_rooms["dms"],
                    "nav_proposals": nav_proposals,
                    "has_rooms": rooms_mod.enabled(conn), "has_media": media_mod.enabled(conn) and media_mod.may_use(conn, user, app.state.directory),
                    "has_screens": media_mod.enabled(conn) and media_screens.may_use(conn, user, app.state.directory),
                    "message_max_chars": messages_mod.MAX_CHARS,
                    "has_remote": bool(remote.apps_for(conn, user, app.state.directory, admin)),
                    "has_automations": automations_mod.may_use(conn, settings, user, app.state.directory),
                    # Signed in on a screen box's TV (its top bar): back to the TV page, which signs out.
                    "kiosk_back": bool(session.get("kiosk_screen"))}
        except sqlite3.Error:
            return out
        finally:
            app.state.db_pool.put(conn)

    templates = Jinja2Templates(directory=_HERE / "templates", context_processors=[_nav_context])
    templates.env.globals["assistant_name"] = ASSISTANT_NAME

    def static(name: str) -> str:
        # The file's mtime in the URL: browsers fetch the new copy after an update
        # instead of reusing a cached one.
        path = _HERE / "static" / name
        version = int(path.stat().st_mtime) if path.exists() else 0
        return f"/static/{name}?v={version}"

    templates.env.globals["static"] = static
    return templates
