"""Section-aware chunking.

Segments are grouped into sections (same section path and anchor); chunks
never cross a section boundary but may span pages, recording page_start and
page_end. Pieces (paragraphs, or lines when a PDF has no blank lines) are
packed up to the target size with a small overlap between neighbours.
"""

from __future__ import annotations

import re
from itertools import groupby

from erinne.ingest import Chunk, Segment

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long(text: str, limit: int) -> list[str]:
    """Break text longer than `limit` chars at sentences, then at spaces."""
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > limit:  # a single huge "sentence": wrap at a space
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return [piece for piece in out if piece]


def _pieces(text: str, limit: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) == 1 and len(paragraphs[0]) > limit:
        # PDF text often has no blank lines; fall back to line breaks.
        paragraphs = [line.strip() for line in paragraphs[0].split("\n") if line.strip()]
    pieces = []
    for paragraph in paragraphs:
        pieces.extend(_split_long(paragraph, limit))
    return pieces


def _overlap_tail(current: list[tuple[str, int | None]], overlap: int) -> list[tuple[str, int | None]]:
    """The end of the previous chunk to repeat at the start of the next one:
    whole trailing pieces if they fit, else the last piece's final sentence(s)."""
    tail, tail_size = [], 0
    for item in reversed(current):
        if tail_size + len(item[0]) > overlap:
            break
        tail.insert(0, item)
        tail_size += len(item[0]) + 1
    if tail or not current or overlap <= 0:
        return tail
    text, page = current[-1]
    window = text[-overlap:]
    boundary = re.search(r"(?<=[.!?])\s+", window) or re.search(r"\s+", window)
    snippet = window[boundary.end():] if boundary else window
    return [(snippet, page)] if snippet.strip() else []


def chunk_segments(
    segments: list[Segment],
    target_tokens: int = 350,
    max_tokens: int = 512,
    overlap_tokens: int = 50,
) -> list[Chunk]:
    target, overlap = target_tokens * 4, overlap_tokens * 4
    limit = min(target, max_tokens * 4 - overlap)  # a piece plus the overlap fits max
    chunks: list[Chunk] = []

    for (path, anchor), group in groupby(segments, key=lambda s: (s.section_path, s.anchor)):
        pieces: list[tuple[str, int | None]] = []
        for seg in group:
            pieces.extend((piece, seg.page_no) for piece in _pieces(seg.text, limit))

        current: list[tuple[str, int | None]] = []
        size = 0
        fresh = False  # whether `current` holds anything beyond the carried-over overlap

        def emit():
            pages = [p for _, p in current if p is not None]
            chunks.append(Chunk(
                text="\n".join(t for t, _ in current),
                section_path=path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                anchor=anchor,
            ))

        for piece in pieces:
            if fresh and size + len(piece[0]) + 1 > target:
                emit()
                current = _overlap_tail(current, overlap)
                size = sum(len(t) + 1 for t, _ in current)
            current.append(piece)
            size += len(piece[0]) + 1
            fresh = True
        if fresh:
            emit()
    return chunks


def embed_text(title: str, chunk: Chunk) -> str:
    """What gets embedded: document title and section give the vector context
    (e.g. "OPERA Cloud > Night Audit > Running the audit")."""
    heading = f"{title} > {chunk.section_path}" if chunk.section_path else title
    return f"{heading}\n{chunk.text}"
