"""Which Mnemerynne this is, for the models that connect to it.

Someone with the work box and the home box both connected to Claude (or
ChatGPT) sees two connectors that introduce themselves the same way. The
brand name under Access → Settings → Themes → Branding is the identity: the
person reads it on the sign-in page, and the connector carries the same word
in its title, its instructions, the `instance` field of listing results and
the `about` tool. Without a brand name the host of MNEMERYNNE_PUBLIC_URL
stands in; without that, it is just Mnemerynne.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from mnemerynne import theme
from mnemerynne.config import Settings

PRODUCT = "Mnemerynne"


@dataclass(frozen=True)
class Instance:
    name: str = ""              # the brand name, "" when none is set
    host: str = ""              # from public_url, "" when unset
    url: str = ""
    tagline: str = ""
    mark_url: str | None = None  # absolute address of the 192 px brand mark, when one is uploaded

    @property
    def label(self) -> str:
        """What to call this box: the brand name, else the host, else the product."""
        return self.name or self.host or PRODUCT

    @property
    def title(self) -> str:
        """The MCP server's title, as connector lists show it."""
        return PRODUCT if self.label == PRODUCT else f"{PRODUCT} · {self.label}"

    def as_json(self) -> dict:
        out = {"name": self.label}
        if self.host:
            out["host"] = self.host
            out["url"] = self.url
        if self.tagline:
            out["tagline"] = self.tagline
        return out

    def intro(self) -> str:
        """The opening of the server's instructions."""
        where = f" at {self.host}" if self.host else ""
        if self.name:
            opening = f"This is {self.name}'s Mnemerynne{where}"
        elif self.host:
            opening = f"This is the Mnemerynne{where}"
        else:
            opening = "This is Mnemerynne"
        if self.tagline:
            opening += f" (“{self.tagline}”)"
        opening += "."
        if self.label != PRODUCT:
            opening += (f" When the person has more than one Mnemerynne connected, call this one “{self.label}” "
                        f"and say which one an answer came from.")
        return opening

    def instructions(self, body: str) -> str:
        return f"{self.intro()} It holds this organisation's documents: {body}"

    def icons(self) -> list:
        if not self.mark_url:
            return []
        from mcp.types import Icon
        return [Icon(src=self.mark_url, mimeType="image/png", sizes=["192x192"])]


def from_settings(settings: Settings) -> Instance:
    """Before the database is open: the host alone."""
    url = (settings.public_url or "").rstrip("/")
    return Instance(host=urlsplit(url).hostname or "", url=url)


def load(conn: sqlite3.Connection, settings: Settings) -> Instance:
    base = from_settings(settings)
    cfg = theme.load_settings(conn)
    brand = cfg["brand"]
    mark = None
    if base.url and theme.asset_path(settings, "brand", "brand", "mark", brand["assets"]):
        mark = f"{base.url}/theme/brand/mark-192.png?v={cfg.get('version', 1)}"
    return Instance(name=(brand.get("name") or "").strip(), host=base.host, url=base.url,
                    tagline=(brand.get("tagline") or "").strip(), mark_url=mark)
