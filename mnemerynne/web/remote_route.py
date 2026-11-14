"""The LAN apps' proxy (/remote/<slug>/...) as a router, for the remote service (docs/plans/services-rewrite.md,
Phase 10). The all-in-one web app mounts remote.RemoteProxy at /remote itself (web/app.py); here each request and
websocket under /remote/ goes to the one the remote hook built (app.state.remote_proxy), with /remote as its root
path, as a mount would give it. The Remote page (/remote) and its list (/api/remote) stay with the web app."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mnemerynne import remote

router = APIRouter()
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class _Proxy:
    """An ASGI app handing the request (or websocket) to app.state.remote_proxy, mounted-style."""

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and not scope.get("path_params", {}).get("rest"):
            # /remote/ itself: the Remote page is the web app's (nginx sends it there); the proxy would send the
            # browser back to /remote, which comes here again.
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        inner = dict(scope, root_path=(scope.get("root_path") or "") + remote.PREFIX)
        await scope["app"].state.remote_proxy(inner, receive, send)


router.add_route(remote.PREFIX + "/{rest:path}", _Proxy(), methods=_METHODS, include_in_schema=False)
router.add_websocket_route(remote.PREFIX + "/{rest:path}", _Proxy())
