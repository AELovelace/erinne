"""Markdown extraction and safe rendering (markdown-it-py, MIT).

Extraction and rendering share one heading/anchor pass, so a citation's
`#anchor` always matches an `id` in the rendered view.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from markdown_it import MarkdownIt

from erinne.ingest import Segment

_FRONT_MATTER = re.compile(r"\A(?:---|\+\+\+)[ \t]*\n.*?\n(?:---|\.\.\.|\+\+\+)[ \t]*(?:\n|\Z)", re.DOTALL)
_ALLOWED_LINK = re.compile(r"^(?:https?:|mailto:|#|/|\./|\.\./|[^:]*$)", re.IGNORECASE)


class MarkdownError(ValueError):
    pass


def decode(data: bytes) -> str:
    """UTF-8 (BOM allowed) text, or MarkdownError for binary/other encodings."""
    if b"\x00" in data:
        raise MarkdownError("This doesn't look like a text file (it contains binary data).")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MarkdownError("Markdown files must be UTF-8 encoded.") from exc


def strip_front_matter(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _FRONT_MATTER.sub("", text, count=1)


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    md.validateLink = lambda url: bool(_ALLOWED_LINK.match(url.strip()))
    return md


def slugify(title: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped, spaces -> '-'."""
    text = unicodedata.normalize("NFKC", title).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-") or "section"


class _Slugger:
    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def __call__(self, title: str) -> str:
        base = slugify(title)
        n = self.seen.get(base, 0)
        self.seen[base] = n + 1
        return base if n == 0 else f"{base}-{n}"


@dataclass
class _Heading:
    level: int
    title: str
    anchor: str
    line_start: int  # first line of the heading
    line_end: int    # first line after the heading


def _headings(tokens) -> list[_Heading]:
    slug = _Slugger()
    found = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            inline = tokens[i + 1]
            title = "".join(
                child.content for child in (inline.children or []) if child.type in ("text", "code_inline")
            ).strip()
            anchor = slug(title)
            tok.attrSet("id", anchor)
            found.append(_Heading(int(tok.tag[1]), title, anchor, tok.map[0], tok.map[1]))
    return found


def segments(text: str) -> list[Segment]:
    """One segment per heading section (plus any text before the first heading)."""
    body = strip_front_matter(text)
    lines = body.split("\n")
    headings = _headings(_parser().parse(body))
    out: list[Segment] = []

    first = headings[0].line_start if headings else len(lines)
    preamble = "\n".join(lines[:first]).strip()
    if preamble:
        out.append(Segment(text=preamble))

    stack: list[tuple[int, str]] = []
    for i, h in enumerate(headings):
        while stack and stack[-1][0] >= h.level:
            stack.pop()
        stack.append((h.level, h.title))
        end = headings[i + 1].line_start if i + 1 < len(headings) else len(lines)
        section = "\n".join(lines[h.line_end:end]).strip()
        if section:
            path = " > ".join(title for _, title in stack)
            out.append(Segment(text=section, section_path=path, anchor=h.anchor))
    return out


def render_html(text: str) -> str:
    """HTML for the file view. Raw HTML in the source is escaped, not rendered."""
    md = _parser()
    body = strip_front_matter(text)
    tokens = md.parse(body)
    _headings(tokens)  # sets id attributes, identical to the anchors in segments()
    return md.renderer.render(tokens, md.options, {})
