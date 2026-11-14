"""File bytes sent by nginx instead of Python (docs/plans/services-rewrite.md, Phase 1: X-Accel-Redirect).

Every route that answers with a file calls `send_file(path, ...)` (the same arguments as Starlette's
FileResponse) after it has checked who may see the file. Normally that *is* a FileResponse. Behind the box's
nginx front door with MNEMERYNNE_ACCEL on, it is an empty answer with `X-Accel-Redirect: /_accel/<kind>/<root>/
<path>`, and nginx sends the file itself: sendfile, Range and all, with no Python thread held for the whole film.

Both have to agree before a request is handed to nginx:
- the setting (MNEMERYNNE_ACCEL=1, set by `front-door.sh accel on`), and
- the request came through that nginx, which sets `X-Mnemerynne-Accel: 1` on everything it passes on (and so
  overwrites anything a client sent). A request straight to the app's port keeps getting a FileResponse.

nginx keeps only a few of the app's headers after the hand-over (Content-Type, Content-Disposition,
Cache-Control, Expires, Set-Cookie), so the security headers are nginx's job there: `<kind>` says which set.
"plain" is SecurityHeaders' default, "sandbox" is for files people uploaded ("default-src 'none'; sandbox"). A
file whose route set any other policy, or one outside the folders nginx knows (`roots`), stays a FileResponse.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import quote

from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mnemerynne.config import Settings

STATIC_DIR = Path(__file__).resolve().parent / "static"   # what app.py mounts at /static
HEADER = "x-mnemerynne-accel"                              # set by the front door's nginx on every request
PREFIX = "/_accel"                                         # nginx's internal locations
SANDBOX_CSP = "default-src 'none'; sandbox"                # uploaded files shown inline
KINDS = ("plain", "sandbox")
KEEP = ("content-type", "content-disposition", "cache-control", "expires")   # what nginx passes on anyway

_ROOTS: ContextVar[dict[str, Path] | None] = ContextVar("mnemerynne_accel_roots", default=None)


def roots(settings: Settings) -> dict[str, Path]:
    """The folders nginx may send files from, by key (the same keys the nginx config uses).

    data: blobs, versions, theme files and (by default) the media cache; media: the media cache when it's set
    outside the data folder; share: the SMB share (libraries, Media's files); static: the app's own files."""
    found = {"static": STATIC_DIR, "data": Path(settings.data_dir)}
    media = Path(settings.media_dir)
    if not _inside(media.resolve(), Path(settings.data_dir).resolve()):
        found["media"] = media
    if settings.share_root:
        found["share"] = Path(settings.share_root)
    return {key: Path(os.path.realpath(path)) for key, path in found.items()}


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def accel_path(path: str | os.PathLike, kind: str, known: dict[str, Path]) -> str | None:
    """The internal address nginx sends `path` from, or None when it isn't under one of the known folders.
    The real path is used (links followed), as nginx's alias points at each folder's real path."""
    real = Path(os.path.realpath(path))
    for key, root in sorted(known.items(), key=lambda item: -len(str(item[1]))):   # the deepest folder first
        if _inside(real, root) and real != root:
            rel = real.relative_to(root).as_posix()
            return f"{PREFIX}/{kind}/{key}/{quote(rel, safe='/')}"
    return None


def send_file(path: str | os.PathLike, **kwargs) -> Response:
    """A FileResponse, or, behind the accel front door, the same headers and an X-Accel-Redirect to `path`."""
    response = FileResponse(path, **kwargs)
    known = _ROOTS.get()
    if known is None:
        return response
    csp = response.headers.get("content-security-policy")
    kind = "plain" if csp is None else "sandbox" if csp == SANDBOX_CSP else None
    target = accel_path(path, kind, known) if kind else None
    if target is None:
        return response
    headers = {name: value for name, value in response.headers.items() if name in KEEP}
    headers["X-Accel-Redirect"] = target
    return Response(status_code=200, headers=headers)


class Accel:
    """Marks a request that came through the accel front door, so send_file hands its files to nginx.
    Pure ASGI: the mark is a context variable, seen by every route (thread-pool ones too) under this request."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.roots = roots(settings) if settings.accel else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.roots is None or scope["type"] != "http" or (HEADER.encode(), b"1") not in scope["headers"]:
            await self.app(scope, receive, send)
            return
        token = _ROOTS.set(self.roots)
        try:
            await self.app(scope, receive, send)
        finally:
            _ROOTS.reset(token)
