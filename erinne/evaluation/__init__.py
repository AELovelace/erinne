"""Evaluation harness: gold questions and metrics for retrieval and answers.

Gold sets live in eval/gold/*.jsonl (git-ignored: they quote confidential
documents). One JSON object per line:

    {"id": "q001",
     "question": "How do I run the night audit?",
     "as_user": "rob",                  # optional; omitted = search everything
     "answerable": true,
     "support": [{"file": "OracleManual255.pdf", "pages": [1973]}],
                                         # or {"file": "notes.md", "anchors": ["night-audit"]}
                                         # or {"file": "x.pdf"} (any part of the file)
     "key_facts": ["End of Day sequence", "..."],
     "tags": ["procedural"],
     "split": "dev",                    # dev | test
     "verified": false}                 # true once a staff member checked it
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Support:
    file: str
    pages: list[int] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)

    def matches(self, hit) -> bool:
        if hit.file_name.lower() != self.file.lower():
            return False
        if self.pages:
            start = hit.page_start or 0
            end = hit.page_end or start
            return any(start <= p <= end for p in self.pages)
        if self.anchors:
            return hit.anchor in self.anchors
        return True


@dataclass
class GoldItem:
    id: str
    question: str
    answerable: bool = True
    support: list[Support] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)
    as_user: str | None = None
    tags: list[str] = field(default_factory=list)
    split: str = "dev"
    verified: bool = False
    history: list[dict] = field(default_factory=list)  # earlier turns for follow-up items

    def supported_by(self, hit) -> bool:
        return any(s.matches(hit) for s in self.support)


def load_gold(paths: list[Path], split: str | None = None, verified_only: bool = False) -> list[GoldItem]:
    items: list[GoldItem] = []
    seen: set[str] = set()
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                raw["support"] = [Support(**s) for s in raw.get("support", [])]
                item = GoldItem(**raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{lineno}: invalid gold item: {exc}") from exc
            if item.id in seen:
                raise ValueError(f"{path}:{lineno}: duplicate id {item.id!r}")
            if item.answerable and not item.support:
                raise ValueError(f"{path}:{lineno}: answerable item {item.id!r} has no support")
            seen.add(item.id)
            if (split is None or item.split == split) and (item.verified or not verified_only):
                items.append(item)
    return items


def dump_gold(items: list[GoldItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            raw = {**item.__dict__, "support": [s.__dict__ for s in item.support]}
            fh.write(json.dumps(raw, ensure_ascii=False) + "\n")
