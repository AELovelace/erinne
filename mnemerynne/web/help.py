"""The built-in help wiki: Markdown pages shipped with Mnemerynne (mnemerynne/help),
rendered for the signed-in person.

Two sections: `user` (using Erynne, for everyone) and `admin` (administration),
which only admins can open; everyone else gets the same 404 as for a page that
doesn't exist. Pages are files named NN-slug.md: NN orders them, the slug is
the address (/help/user/files), and the first "# heading" is the title. Links
between pages are written as plain relative links (`files.md`,
`../admin/share.md`, with #anchors), rewritten to /help addresses when rendered.
The page index is read once per process; the page text on every request, so an
edit shows after a reload while writing.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from mnemerynne.ingest import markdown
from mnemerynne.web.deps import get_db, is_admin, page_user

router = APIRouter()

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
SECTIONS = {"user": "Using Erynne", "admin": "Administration"}
_PAGE_NAME = re.compile(r"^(\d+)-([a-z0-9][a-z0-9-]*)\.md$")
_PAGE_LINK = re.compile(r"^(?:\.\./(user|admin)/)?([a-z0-9][a-z0-9-]*)\.md(#[^#]*)?$")
# The pages are ours, not uploaded content, but they still get a tight policy:
# no inline scripts, nothing remote. base.html's parallax.js and the data: favicon are allowed.
_CSP = ("default-src 'none'; style-src 'self'; img-src 'self' data:; script-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


@dataclass(frozen=True)
class Page:
    section: str
    slug: str
    order: int
    title: str
    path: Path

    @property
    def url(self) -> str:
        return f"/help/{self.section}/{self.slug}"


def _title(text: str, slug: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return slug.replace("-", " ").capitalize()


@lru_cache
def load_help() -> dict[str, list[Page]]:
    """Every page by section, in reading order."""
    out: dict[str, list[Page]] = {}
    for section in SECTIONS:
        pages = []
        for path in (HELP_DIR / section).glob("*.md"):
            m = _PAGE_NAME.match(path.name)
            if m:
                pages.append(Page(section, m.group(2), int(m.group(1)),
                                  _title(path.read_text(encoding="utf-8"), m.group(2)), path))
        pages.sort(key=lambda p: (p.order, p.slug))
        out[section] = pages
    return out


def page_for(section: str, slug: str) -> Page | None:
    return next((p for p in load_help().get(section, []) if p.slug == slug), None)


def link_rewriter(section: str) -> Callable[[str], str]:
    """`other.md`, `../admin/other.md` and `#anchor` forms become /help addresses; anything else stays."""
    def rewrite(url: str) -> str:
        m = _PAGE_LINK.match(url.strip())
        if m is None:
            return url
        target_section = m.group(1) or section
        if page_for(target_section, m.group(2)) is None:
            return url
        return f"/help/{target_section}/{m.group(2)}{m.group(3) or ''}"
    return rewrite


def render(page: Page) -> str:
    return markdown.render_html(page.path.read_text(encoding="utf-8"), link_rewriter=link_rewriter(page.section))


@router.get("/help")
def help_home(user=Depends(page_user)):
    first = load_help()["user"][0]
    return RedirectResponse(first.url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/help/{section}/{slug}", response_class=HTMLResponse)
def help_page(request: Request, section: str, slug: str, user=Depends(page_user),
              conn: sqlite3.Connection = Depends(get_db)):
    admin = is_admin(request, conn, user)
    page = page_for(section, slug)
    # Admin pages don't exist for other people: no hint of what they'd cover.
    if page is None or (section == "admin" and not admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such help page")
    pages = load_help()
    sections = [(key, label, pages[key][0].url) for key, label in SECTIONS.items()
                if pages[key] and (key == "user" or admin)]
    response = request.app.state.templates.TemplateResponse(request, "help.html", {
        "user": user, "is_admin": admin, "page": page, "pages": pages[section], "sections": sections,
        "section_label": SECTIONS[section], "content": render(page)})
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
