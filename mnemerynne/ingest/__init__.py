"""Turning uploaded files into searchable chunks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    """A run of document text that belongs to one place: a page within one PDF
    bookmark section, or one Markdown heading section."""

    text: str
    section_path: str = ""
    page_no: int | None = None  # 1-based, PDFs only
    anchor: str | None = None   # heading slug, Markdown only
    ocr: bool = False


@dataclass(frozen=True)
class Chunk:
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    anchor: str | None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    # ~4 characters per token for English prose; good enough for sizing.
    return max(1, len(text) // 4)
