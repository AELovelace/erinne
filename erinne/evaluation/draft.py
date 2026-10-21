"""Draft a gold set with the teacher model (Qwen3-8B), for staff to verify.

Answerable items: sample chunks across files, have the teacher write the
question a staff member would ask that the chunk answers.
Unanswerable items:
  * scope-excluded: a question from a library the asking user can't read
    (tests permission filtering end to end);
  * off-topic: plausible hotel questions the documents don't cover (the
    teacher is told the document titles; staff must confirm).
Everything is written with verified=false.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import sqlite3
from pathlib import Path

from erinne import permissions, users
from erinne.clients.llm import LLM
from erinne.evaluation import GoldItem, Support

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {"type": "boolean"},
        "question": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "kind": {"type": "string", "enum": ["factual", "procedural", "policy"]},
    },
    "required": ["usable", "question", "key_facts", "kind"],
}

OFFTOPIC_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}

STYLES = [
    "a short, direct question",
    "a question phrased the way a busy front-desk agent would type it, casual wording",
    "a 'how do I…' question",
    "a question that paraphrases the passage instead of reusing its exact terms",
    "a question with one small typo, as if typed quickly",
]

_QUESTION_PROMPT = """You are building an evaluation set for a hotel staff assistant that answers
questions from internal documents.

Document: {file}
Section: {section}
Passage:
\"\"\"
{text}
\"\"\"

Write {style} that a hotel front-desk or back-office employee might realistically ask,
which this passage fully answers. Do not mention "the passage", "the document" or its title.
List 1-4 short key facts from the passage that a correct answer must contain.
Set usable=false if the passage is a table of contents, index, legal boilerplate, a fragment,
or otherwise not something staff would ask about."""

_OFFTOPIC_PROMPT = """A hotel staff assistant can only answer from these internal documents:
{titles}

Write {n} realistic questions hotel front-desk or back-office staff might ask that these documents
almost certainly do NOT answer (e.g. local restaurant tips, a specific guest's booking, equipment
not mentioned, HR questions, today's occupancy). Vary topics and phrasing."""


def _split_for(file_name: str, section: str, test_ratio: float) -> str:
    top = section.split(" > ")[0]
    digest = hashlib.sha256(f"{file_name}|{top}".encode()).digest()
    return "test" if digest[0] / 255 < test_ratio else "dev"


def _sample_chunks(conn: sqlite3.Connection, n: int, rng: random.Random) -> list[sqlite3.Row]:
    """Spread samples across files: at least a few per file, the rest by size."""
    rows = conn.execute(
        "SELECT c.*, f.display_name, f.collection_id AS coll FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.tokens >= 60"
    ).fetchall()
    by_file: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        by_file.setdefault(r["file_id"], []).append(r)
    picked: list[sqlite3.Row] = []
    for chunks in by_file.values():
        picked.extend(rng.sample(chunks, min(2, len(chunks))))
    rest = [r for r in rows if r not in picked]
    picked.extend(rng.sample(rest, max(0, min(len(rest), n - len(picked)))))
    rng.shuffle(picked)
    return picked[:n]


async def draft(conn: sqlite3.Connection, llm: LLM, n_answerable: int = 150, n_offtopic: int = 30,
                n_scope: int = 20, as_user: str = "rob", outsider: str | None = "test",
                test_ratio: float = 0.25, seed: int = 7, concurrency: int = 2, progress=print,
                teacher_allowed: set[int] | None = None) -> list[GoldItem]:
    """teacher_allowed: collections whose text the teacher may see (None = all; set it for remote teachers)."""
    rng = random.Random(seed)
    user = users.get_user(conn, as_user)
    if user is None:
        raise ValueError(f"Unknown user {as_user!r}")
    readable = permissions.readable_collection_ids(conn, user["id"])
    outsider_row = users.get_user(conn, outsider) if outsider else None
    outsider_readable = (permissions.readable_collection_ids(conn, outsider_row["id"])
                         if outsider_row else set())
    if getattr(llm, "is_remote", False) and teacher_allowed is None:
        raise ValueError("A remote teacher needs teacher_allowed (libraries it may see).")
    visible = readable if teacher_allowed is None else readable & teacher_allowed

    chunks = [c for c in _sample_chunks(conn, n_answerable * 2, rng) if c["coll"] in visible]
    sem = asyncio.Semaphore(concurrency)
    items: list[GoldItem] = []

    async def make(chunk_row) -> GoldItem | None:
        style = rng.choice(STYLES)
        prompt = _QUESTION_PROMPT.format(file=chunk_row["display_name"], section=chunk_row["section_path"] or "-",
                                         text=chunk_row["text"][:2500], style=style)
        async with sem:
            out = await llm.chat_json([{"role": "user", "content": prompt}], QUESTION_SCHEMA,
                                      temperature=0.7, max_tokens=300)
        if not out.get("usable") or not out.get("question", "").strip():
            return None
        if chunk_row["page_start"]:
            support = Support(chunk_row["display_name"],
                              pages=list(range(chunk_row["page_start"], (chunk_row["page_end"] or chunk_row["page_start"]) + 1)))
        elif chunk_row["anchor"]:
            support = Support(chunk_row["display_name"], anchors=[chunk_row["anchor"]])
        else:
            support = Support(chunk_row["display_name"])
        return GoldItem(id="", question=out["question"].strip(), answerable=True, support=[support],
                        key_facts=[f for f in out.get("key_facts", []) if f.strip()], as_user=as_user,
                        tags=[out.get("kind", "factual"), "drafted"],
                        split=_split_for(chunk_row["display_name"], chunk_row["section_path"], test_ratio))

    # Small batches so we stop as soon as enough usable questions exist.
    queue = list(chunks)
    tried = 0
    while queue and len(items) < n_answerable:
        batch, queue = queue[: concurrency * 4], queue[concurrency * 4:]
        for item in await asyncio.gather(*(make(c) for c in batch)):
            if item and len(items) < n_answerable:
                items.append(item)
        tried += len(batch)
        progress(f"  drafted from {tried} chunks, {len(items)}/{n_answerable} kept")

    # Scope-excluded: answerable for `as_user`, but asked by someone without access.
    if outsider_row:
        excluded = [i for i in items if i.answerable and not _file_readable(conn, i, outsider_readable)]
        for i in rng.sample(excluded, min(n_scope, len(excluded))):
            items.append(GoldItem(id="", question=i.question, answerable=False, as_user=outsider,
                                  tags=["scope-excluded", "drafted"], split=i.split))

    # Off-topic questions.
    marks = ",".join(str(int(i)) for i in (teacher_allowed if teacher_allowed is not None else readable)) or "NULL"
    titles = "\n".join(f"- {r[0]}" for r in conn.execute(
        f"SELECT DISTINCT display_name FROM files WHERE collection_id IN ({marks}) ORDER BY 1"))
    out = await llm.chat_json([{"role": "user", "content": _OFFTOPIC_PROMPT.format(titles=titles, n=n_offtopic)}],
                              OFFTOPIC_SCHEMA, temperature=0.8, max_tokens=1500)
    for q in out.get("questions", [])[:n_offtopic]:
        if q.strip():
            items.append(GoldItem(id="", question=q.strip(), answerable=False, as_user=as_user,
                                  tags=["off-topic", "drafted"], split="dev" if rng.random() > test_ratio else "test"))

    for n, item in enumerate(items, start=1):
        item.id = f"q{n:04d}"
    return items


def _file_readable(conn: sqlite3.Connection, item: GoldItem, readable: set[int]) -> bool:
    names = [s.file for s in item.support]
    marks = ",".join("?" * len(names))
    rows = conn.execute(f"SELECT collection_id FROM files WHERE display_name IN ({marks})", names).fetchall()
    return any(r[0] in readable for r in rows)
