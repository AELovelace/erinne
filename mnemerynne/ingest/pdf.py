"""PDF text extraction with pypdfium2 (Apache-2.0/BSD; PyMuPDF is AGPL).

Produces Segments: each page's text split at the PDF's bookmarks, so a page
holding three subsections yields three segments with their own section paths.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from erinne.ingest import Segment

# Top-level sections that are navigation, not content; they flood keyword search.
_NAVIGATION_TITLES = {"contents", "table of contents", "index"}
_WS = re.compile(r"\s+")


class PdfError(ValueError):
    """The PDF can't be ingested (encrypted, damaged, too large)."""


@dataclass
class Bookmark:
    level: int
    title: str
    page_index: int  # 0-based


@dataclass
class PdfText:
    pages: list[str]          # raw text per page, 0-based
    bookmarks: list[Bookmark]


def open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message or "security" in message:
            raise PdfError("The PDF is password-protected; upload an unlocked copy.") from exc
        raise PdfError(f"The PDF could not be opened ({exc}).") from exc


def page_count(path: Path) -> int:
    pdf = open_pdf(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def read_pdf(path: Path) -> PdfText:
    pdf = open_pdf(path)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            textpage.close()
            page.close()
        bookmarks = []
        for item in pdf.get_toc():
            dest = item.get_dest()
            index = dest.get_index() if dest else None
            title = _WS.sub(" ", item.get_title()).strip()
            if index is not None and title and 0 <= index < len(pages):
                bookmarks.append(Bookmark(item.level, title, index))
        return PdfText(pages, bookmarks)
    finally:
        pdf.close()


def read_pages(path: Path, indexes: list[int]) -> dict[int, str]:
    """Text of selected pages (used to pick up OCR results)."""
    pdf = open_pdf(path)
    try:
        out = {}
        for index in indexes:
            page = pdf[index]
            textpage = page.get_textpage()
            out[index] = textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            textpage.close()
            page.close()
        return out
    finally:
        pdf.close()


def strip_repeated_lines(pages: list[str], min_pages: int = 4, ratio: float = 0.5, edge: int = 8) -> list[str]:
    """Remove running headers/footers.

    Only the first/last `edge` lines of a page are candidates, and digits are
    ignored when comparing ("Page 3 of 10" repeats); a line repeated on most
    pages in those positions is dropped there. Body text is never touched,
    even if it differs from other pages only by a number.
    """
    if len(pages) < min_pages:
        return pages

    def norm(line: str) -> str:
        return re.sub(r"\d+", "#", _WS.sub(" ", line).strip().lower())

    def edges(lines: list[str]) -> set[int]:
        idx = [i for i, line in enumerate(lines) if line.strip()]
        n = max(1, min(edge, len(idx) // 5))  # short pages: only the very first/last line
        return set(idx[:n] + idx[-n:])

    split = [text.split("\n") for text in pages]
    counts = Counter()
    for lines in split:
        counts.update({norm(lines[i]) for i in edges(lines)})
    repeated = {line for line, n in counts.items() if n >= ratio * len(pages) and len(line) < 120}
    if not repeated:
        return pages
    out = []
    for lines in split:
        drop = {i for i in edges(lines) if norm(lines[i]) in repeated}
        out.append("\n".join(line for i, line in enumerate(lines) if i not in drop))
    return out


def _find_title(text: str, title: str, start: int) -> int:
    """Position of a bookmark title in page text (whitespace/case-insensitive), or -1."""
    words = [re.escape(w) for w in title.split()]
    if not words:
        return -1
    match = re.compile(r"\s+".join(words), re.IGNORECASE).search(text, start)
    return match.start() if match else -1


def segments(doc: PdfText, scanned_ocr: set[int] = frozenset()) -> list[Segment]:
    """Split pages at bookmark boundaries and attach section paths.

    Pages before the first bookmark get an empty section path. Navigation
    sections (Contents, Index) are dropped.
    """
    pages = strip_repeated_lines(doc.pages)
    by_page: dict[int, list[Bookmark]] = {}
    for bm in doc.bookmarks:
        by_page.setdefault(bm.page_index, []).append(bm)

    stack: list[str] = []          # current bookmark path, one title per level
    current_path = ""
    out: list[Segment] = []

    def emit(text: str, page_index: int, path: str) -> None:
        text = text.strip()
        if not text:
            return
        top = path.split(" > ", 1)[0].strip().lower()
        if top in _NAVIGATION_TITLES:
            return
        out.append(Segment(text=text, section_path=path, page_no=page_index + 1, ocr=page_index in scanned_ocr))

    for index, text in enumerate(pages):
        cursor = 0
        for bm in by_page.get(index, []):
            pos = _find_title(text, bm.title, cursor)
            if pos < 0:
                pos = cursor  # title not found in text: section starts here
            emit(text[cursor:pos], index, current_path)
            del stack[bm.level:]
            stack.append(bm.title)
            current_path = " > ".join(stack)
            cursor = pos
        emit(text[cursor:], index, current_path)
    return out
