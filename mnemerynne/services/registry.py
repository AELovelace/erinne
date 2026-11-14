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
    paths: tuple[str, ...]               # the URL prefixes the web server sends to it (nginx `location`)
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
