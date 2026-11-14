"""Claude's MCP endpoint (/mcp) as a router, for the mcp service (docs/plans/services-rewrite.md, Phase 8).

The endpoint is built at start-up from the settings (mcp_server.build_server and McpEndpoint, in the mcp hook), so
this route only hands each request to the one on app.state. The all-in-one web app adds the same endpoint itself
(web/app.py create_app)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


class _Endpoint:
    """An ASGI app passing the request to app.state.mcp_endpoint (stateless, JSON: any process may answer it)."""

    async def __call__(self, scope, receive, send) -> None:
        await scope["app"].state.mcp_endpoint(scope, receive, send)


router.add_route("/mcp", _Endpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False)
