"""Hotel/PMS vocabulary bridging for search.

Staff say "night audit"; the OPERA manual says "End of Day". Search runs the
question as typed plus variants with such terms swapped, and fuses the
results, so documents using either word are found.

Defaults cover common hotel jargon. Add your own in <data_dir>/synonyms.txt,
one group per line, terms separated by "=":
    night audit = end of day
    # comments and blank lines are ignored
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DEFAULT_GROUPS: list[list[str]] = [
    ["night audit", "end of day"],
    ["check-out", "checkout", "departure"],
    ["check-in", "arrival"],
    ["ota", "online travel agency", "third-party booking site"],
    ["folio", "guest bill"],
    ["no-show", "no show"],
    ["comp", "complimentary"],
    ["walk-in", "walk in"],
    ["pms", "property management system"],
    ["rate parity", "price parity"],
]

MAX_VARIANTS = 3


def load_groups(path: Path | None) -> list[list[str]]:
    groups = [list(g) for g in DEFAULT_GROUPS]
    if path is not None and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            terms = [t.strip().lower() for t in line.split("=") if t.strip()]
            if len(terms) >= 2:
                groups.append(terms)
    return groups


@lru_cache(maxsize=8)
def _compiled(groups: tuple[tuple[str, ...], ...]):
    return [[(term, re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)) for term in group]
            for group in groups]


def variants(question: str, groups: list[list[str]], limit: int = MAX_VARIANTS) -> list[str]:
    """Other phrasings of `question` with known jargon swapped (excluding the original)."""
    out: list[str] = []
    for group in _compiled(tuple(tuple(g) for g in groups)):
        present = [(term, pattern) for term, pattern in group if pattern.search(question)]
        if not present:
            continue
        term, pattern = present[0]
        for other, _ in group:
            if other != term and not any(other == t for t, _ in present):
                candidate = pattern.sub(other, question)
                if candidate.lower() != question.lower() and candidate not in out:
                    out.append(candidate)
            if len(out) >= limit:
                return out
    return out
