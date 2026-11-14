"""The services: what each serves, where, and how many processes (docs/plans/services-rewrite.md).

One row per service. The factory builds a service's app from its row, the CLI picks its port and processes
from it, and the deploy tools (nginx front door, systemd) follow the same rows, so they can't disagree.
A phase of the rewrite adds a service by adding its row here (and its routers and hooks).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PROFILES = ("nas", "ryzen")      # the NAS today (i7-2600, 8 GB); the 5900X box (32 GB) later
DEFAULT_PROFILE = "nas"


@dataclass(frozen=True)
class Service:
    name: str
    serves: str                          # what it is, in a sentence (logs, health, docs)
    routers: tuple[str, ...]             # "module:attribute" of each APIRouter it mounts, imported when built
    paths: tuple[str, ...]               # the URL prefixes the web server sends to it (nginx `location`; "~ ^/..." a regex)
    port: int                            # on MNEMERYNNE_HOST
    workers: dict[str, int] = field(default_factory=lambda: {"nas": 1, "ryzen": 1})   # processes per profile
    single: bool = False                 # keeps live state in memory: exactly one process, whatever the profile
    hooks: tuple[str, ...] = ()          # start-up pieces from services/hooks.py (in order)
    # Only works with Valkey (MNEMERYNNE_VALKEY_URL): what it does has to reach the other processes at once (Rooms'
    # live events, Erynne's answers). Without Valkey, front-door.sh leaves it off and its paths with the web app.
    needs_valkey: bool = False
    # Paths under its own that stay with the web app (nginx sends them there while the service runs): another
    # router's pages under the same prefix. "= /exact/path" for one path only.
    web_paths: tuple[str, ...] = ()
    websockets: bool = False             # its paths carry websockets: nginx passes the upgrade on (Remote's LAN apps)

    def processes(self, profile: str | None = None) -> int:
        """How many processes to run: one for a single-process service, else the profile's count."""
        if self.single:
            return 1
        return max(1, self.workers.get(profile or current_profile(), 1))


SERVICES: dict[str, Service] = {s.name: s for s in (
    Service(
        name="sync",
        serves="the desktop backup app's API (/api/sync/): keeps nothing in memory, so it may run as several processes",
        routers=("mnemerynne.web.sync:router",),
        paths=("/api/sync/",),
        port=38201,
        workers={"nas": 3, "ryzen": 6},
    ),
    Service(
        name="games",
        serves="the built-in games' API (/api/media/games): its live parties and tables live in this one process",
        routers=("mnemerynne.web.games:router",),
        paths=("/api/media/games",),
        port=38202,
        single=True,
        hooks=("balancer",),               # Lumi's live tales reach the answer model through the balancer nodes
    ),
    Service(
        name="rooms",
        serves="Rooms' API and live stream (/api/rooms): rooms.db; new messages, typing and presence travel by Valkey",
        routers=("mnemerynne.web.rooms:router",),
        paths=("/api/rooms",),
        port=38206,
        workers={"nas": 1, "ryzen": 2},
        hooks=("rooms",),                  # its live bus; Erynne's answers stay with the web app (rooms:ask)
        needs_valkey=True,
    ),
    Service(
        name="media",
        serves="Media (/media/, /api/media/, its admin and signage): the TV and speaker hub, pairing codes and "
               "transcoding sessions live in this one process, so every path that touches them comes here",
        routers=("mnemerynne.web.media:router", "mnemerynne.web.media:admin_router", "mnemerynne.web.kiosk:router",
                 "mnemerynne.web.soundcloud:router"),
        paths=("/api/media/", "/media/", "/api/admin/media", "/api/admin/signage", "/api/admin/settings/media"),
        port=38207,
        single=True,                       # the hub, the pairings and the ffmpeg sessions are this process's memory
        hooks=("pages", "media"),          # its pages draw the web app's templates; its own transcoding sessions
        web_paths=("/media/games/", "= /media/panel/manifest.webmanifest"),
    ),
    Service(
        name="files",
        serves="Files (/api/files, /api/browse, folders, moves, the bin, versions, comments, downloads and zips), the "
               "editor and the wiki: core's files and folders; keeps nothing in memory, so it runs as several processes",
        routers=("mnemerynne.web.files:router", "mnemerynne.web.editor:router", "mnemerynne.web.wiki:router"),
        paths=("/api/files", "/api/folders", "/api/items/", "/api/browse", "/api/collections/", "/api/locations",
               "/api/downloads", "/api/properties", "/api/reembed", "/api/search-names", "/api/trash",
               "/api/markdown/", "/api/wiki", "/api/admin/settings/wiki-library", "/files/", "/edit/", "/wiki"),
        port=38203,
        workers={"nas": 3, "ryzen": 6},
        hooks=("pages",),                  # its viewer, editor and wiki pages draw the web app's templates
    ),
    Service(
        name="assistant",
        serves="Ask (/api/ask and its answers' events, conversations, ratings, memories, the tool list): "
               "assistant.db and LangGraph's state; an answer's events live in the process writing it, so one process",
        routers=("mnemerynne.web.chat:router", "mnemerynne.web.memories:router"),
        paths=("/api/ask", "/api/runs/", "/api/conversations", "~ ^/api/messages/[0-9]+/feedback$", "/api/memories",
               "/api/me/tools"),
        port=38204,
        single=True,                       # runs (an answer's events) are this process's memory
        hooks=("balancer", "rooms", "assistant"),   # the model's nodes, Rooms' bus for Erynne's answers, then Ask
    ),
    Service(
        name="mcp",
        serves="Claude's MCP endpoint (/mcp) and its fetch links (/c/): stateless JSON, so any of its processes "
               "answers; each tool call is logged in logs.db",
        routers=("mnemerynne.web.mcp_route:router", "mnemerynne.web.claude_links:router"),
        paths=("/mcp", "/c/"),
        port=38205,
        workers={"nas": 2, "ryzen": 4},
        hooks=("mcp",),
    ),
    Service(
        name="automations",
        serves="Automator's scheduler and API (/api/automations, its admin): automations.db; runs started elsewhere "
               "are queued for it, so exactly one process ticks",
        routers=("mnemerynne.web.automations:router", "mnemerynne.web.automations:admin_router"),
        paths=("/api/automations", "/api/admin/automations"),
        port=38208,
        single=True,                       # one scheduler: two would both run what's due
        hooks=("balancer", "rooms", "automations"),   # the model's nodes, Rooms' bus for reports, then the scheduler
    ),
    Service(
        name="remote",
        serves="the LAN apps' proxy (/remote/<app>/...: pages, their requests and websockets); the Remote page and "
               "its list stay with the web app",
        routers=("mnemerynne.web.remote_route:router",),
        paths=("/remote/",),
        port=38209,
        workers={"nas": 1, "ryzen": 2},
        hooks=("remote",),
        websockets=True,
    ),
    Service(
        name="share",
        serves="no pages: walks the share's permissions for every process and watches the share for changes "
               "(share-rights.db, share:rights); the others stop walking while it runs (MNEMERYNNE_SHARE_WALKS)",
        routers=(),
        paths=(),
        port=38211,                        # its health only
        single=True,                       # one watcher, one walker
        hooks=("share",),
    ),
)}


def get(name: str) -> Service:
    """The service called `name`, or a ValueError listing the ones there are."""
    try:
        return SERVICES[name]
    except KeyError:
        raise ValueError(f"No service {name!r}; the services are: {', '.join(SERVICES)}") from None


def current_profile() -> str:
    """MNEMERYNNE_PROFILE (nas | ryzen): how many processes each service runs on this box."""
    profile = os.environ.get("MNEMERYNNE_PROFILE", DEFAULT_PROFILE).strip().lower()
    return profile if profile in PROFILES else DEFAULT_PROFILE
