"""Labelled messages for training the router, written by the teacher model.

Each route gets varied batches, deliberately including hard cases: terse or
casual document questions, chat that mentions document words, and writing
requests about hotel topics. A share of the search examples are written from
real chunks so they look like questions about these documents.

Output: JSONL lines {"text": ..., "label": ...} (in data/, git-ignored).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from pathlib import Path

from erinne import teacher as teacher_scope
from erinne.clients.llm import LLM
from erinne.evaluation.draft import _QUESTION_PROMPT, QUESTION_SCHEMA, STYLES

log = logging.getLogger("erinne.router_data")

LIST_SCHEMA = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {"type": "string"}}},
    "required": ["messages"],
}

ROUTE_BRIEFS: dict[str, list[str]] = {
    "search": [
        "questions about how to do something in the hotel's property management system (reservations, "
        "billing, folios, end of day, reports, profiles, rates)",
        "questions about brand standards, hotel policies, emergency procedures or OTA (Expedia, Booking.com) rules",
        "very short or terse lookups typed in a hurry, e.g. 'pool hours?', 'ota cancel policy', 'reinstate res'",
        "casually or politely phrased lookups, e.g. 'hey can you remind me how we handle no-shows'",
        "questions mentioning 'the manual', 'our policy' or 'the guide'",
    ],
    "smalltalk": [
        "greetings and goodbyes (hi, good morning, see you tomorrow)",
        "thanks and reactions to a previous answer, including ones that mention its topic, e.g. "
        "'thanks, that night audit answer was perfect'",
        "questions about the assistant itself: who are you, what can you do, how do I upload files",
        "chit-chat, jokes, venting about a long shift, compliments",
    ],
    "general": [
        "requests to write or rewrite text: emails or messages to guests, apology letters, shift handover "
        "notes, social media replies, making text more polite or shorter",
        "requests to summarise, translate or proofread text the person pastes in (include the text)",
        "brainstorming and general advice not tied to internal documents: phrasing a refusal kindly, "
        "ideas for a staff appreciation note, how to calm an upset guest in general",
        "general knowledge or simple calculations: convert currencies roughly, count nights between dates, "
        "explain a common English word",
    ],
}

_BATCH_PROMPT = """Write {n} different messages that hotel front-desk or back-office staff might type into
an internal assistant. All of them must be: {brief}.
Vary length, tone and wording; include a few with typos or no punctuation. One message per item."""


async def _batch(llm: LLM, brief: str, n: int, sem: asyncio.Semaphore) -> list[str]:
    try:
        async with sem:
            out = await llm.chat_json([{"role": "user", "content": _BATCH_PROMPT.format(n=n, brief=brief)}],
                                      LIST_SCHEMA, temperature=1.0, max_tokens=1800)
    except Exception as exc:  # one slow or failed call must not end the run
        log.warning("Teacher batch failed (%s); skipping it.", type(exc).__name__)
        return []
    return [m.strip() for m in out.get("messages", []) if isinstance(m, str) and 2 <= len(m.strip()) <= 1500]


async def _from_chunk(llm: LLM, row, rng: random.Random, sem: asyncio.Semaphore) -> str | None:
    try:
        async with sem:
            out = await llm.chat_json([{"role": "user", "content": _QUESTION_PROMPT.format(
                file=row["display_name"], section=row["section_path"] or "-", text=row["text"][:2000],
                style=rng.choice(STYLES))}], QUESTION_SCHEMA, temperature=0.9, max_tokens=250)
    except Exception as exc:
        log.warning("Teacher call failed (%s); skipping it.", type(exc).__name__)
        return None
    q = (out.get("question") or "").strip()
    return q if out.get("usable") and q else None


async def generate(conn: sqlite3.Connection, llm: LLM, out_path: Path, per_route: int = 400, seed: int = 3,
                   concurrency: int = 2, progress=print, settings=None) -> dict[str, int]:
    rng = random.Random(seed)
    sem = asyncio.Semaphore(concurrency)
    found: dict[str, set[str]] = {label: set() for label in ROUTE_BRIEFS}

    # A quarter of search examples written from real chunks.
    # Only chunks the teacher may see (all for a local teacher; allowed libraries for a remote one).
    if getattr(llm, "is_remote", False) and settings is None:
        raise teacher_scope.TeacherScopeError("A remote teacher needs settings to know which libraries it may see.")
    allowed = teacher_scope.allowed_collections(conn, settings, llm) if settings else None
    rows = [r for r in conn.execute("SELECT c.text, c.section_path, c.collection_id, f.display_name "
                                    "FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.tokens >= 60")
            if allowed is None or r["collection_id"] in allowed]
    sample = rng.sample(rows, min(len(rows), per_route // 4))
    for start in range(0, len(sample), 8):
        for q in await asyncio.gather(*(_from_chunk(llm, r, rng, sem) for r in sample[start:start + 8])):
            if q:
                found["search"].add(q)
    progress(f"search from chunks: {len(found['search'])}")

    for label, briefs in ROUTE_BRIEFS.items():
        attempts = 0
        while len(found[label]) < per_route and attempts < 40:
            attempts += 1
            batches = await asyncio.gather(*(_batch(llm, rng.choice(briefs), 15, sem) for _ in range(2)))
            for messages in batches:
                found[label].update(messages)
            progress(f"{label}: {len(found[label])}/{per_route}")

    # A message the teacher produced for two routes is ambiguous: drop it.
    seen: dict[str, int] = {}
    for messages in found.values():
        for m in messages:
            seen[m.lower()] = seen.get(m.lower(), 0) + 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as fh:
        for label, messages in found.items():
            for m in sorted(messages)[: per_route]:
                if seen[m.lower()] == 1:
                    fh.write(json.dumps({"text": m, "label": label}, ensure_ascii=False) + "\n")
                    counts[label] = counts.get(label, 0) + 1
    return counts


def load(path: Path) -> tuple[list[str], list[str]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r["text"] for r in rows], [r["label"] for r in rows]
