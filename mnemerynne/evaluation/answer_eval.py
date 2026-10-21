"""End-to-end answer evaluation: retrieval -> production prompt -> model -> checks.

Uses the same prompt builder and parser as production. A judge model (the
teacher) scores each answered item for faithfulness to the sources it cited
and coverage of the gold key facts.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass, field

from erinne import answer as answer_mod
from erinne import retrieval
from erinne.clients.llm import LLM
from erinne.config import Settings
from erinne.evaluation import GoldItem
from erinne.evaluation.retrieval_eval import _allowed

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "faithful": {"type": "boolean"},
        "facts_covered": {"type": "array", "items": {"type": "boolean"}},
    },
    "required": ["unsupported_claims", "faithful", "facts_covered"],
}

_JUDGE_PROMPT = """You are grading an assistant's answer for hotel staff.

Sources the assistant was given:
{sources}

Question: {question}

Answer to grade:
\"\"\"{answer}\"\"\"

1. List every claim in the answer that is NOT stated in the sources (unsupported_claims).
   Paraphrases of the sources count as supported. Ignore citation markers like [1].
2. faithful = true if there are no unsupported claims.
3. For each key fact below, in order, say whether the answer conveys it (facts_covered):
{facts}"""


@dataclass
class AnswerItem:
    id: str
    answerable: bool
    status: str
    valid_json: bool
    citations: list[int]
    cited_supporting: int        # cited sources that match the gold support
    faithful: bool | None
    unsupported_claims: list[str] = field(default_factory=list)
    facts_covered: float | None = None
    ttft_ms: float | None = None
    total_ms: float = 0.0
    answer: str = ""


async def _generate(llm: LLM, messages) -> tuple[str, float | None, float]:
    started = time.perf_counter()
    first = None
    parts = []
    async for delta in llm.stream(messages, temperature=0.1, max_tokens=900, json_schema=answer_mod.ANSWER_SCHEMA):
        if first is None:
            first = (time.perf_counter() - started) * 1000
        parts.append(delta)
    return "".join(parts), first, (time.perf_counter() - started) * 1000


def restrict_items(conn: sqlite3.Connection, items: list[GoldItem], collections: set[int]) -> list[GoldItem]:
    """Keep answerable items whose supporting files all live in `collections`
    (plus every unanswerable item; its search is restricted separately)."""
    def file_ok(name: str) -> bool:
        rows = conn.execute("SELECT collection_id FROM files WHERE lower(display_name) = lower(?)", (name,))
        ids = {r[0] for r in rows}
        return bool(ids) and ids <= collections
    return [i for i in items if not i.answerable or all(file_ok(s.file) for s in i.support)]


async def evaluate_answers(conn: sqlite3.Connection, settings: Settings, embedder, generator: LLM,
                           judge: LLM | None, items: list[GoldItem], reranker=None, progress=print,
                           restrict_to: set[int] | None = None) -> dict:
    """restrict_to: only these collections are ever searched (and so only their
    text reaches the generator). Required for remote generators."""
    if getattr(generator, "is_remote", False) and restrict_to is None:
        raise ValueError("A remote generator needs restrict_to (the libraries its prompts may contain).")
    if restrict_to is not None:
        items = restrict_items(conn, items, restrict_to)
    results: list[AnswerItem] = []
    for n, item in enumerate(items, start=1):
        allowed = _allowed(conn, item)
        if restrict_to is not None:
            allowed &= restrict_to
        result = retrieval.search(conn, settings, embedder, item.question, allowed, reranker)
        if not result.hits:
            results.append(AnswerItem(item.id, item.answerable, "not_found", True, [], 0, None,
                                      answer=answer_mod.NOT_FOUND_TEXT))
            continue
        messages = answer_mod.build_messages(item.question, result.hits)
        raw, ttft, total = await _generate(generator, messages)
        try:
            json.loads(raw)
            valid = True
        except json.JSONDecodeError:
            valid = False
        parsed = answer_mod.parse_answer(raw, len(result.hits))
        cited_hits = [result.hits[i - 1] for i in parsed.citations]
        record = AnswerItem(item.id, item.answerable, parsed.status, valid, parsed.citations,
                            sum(1 for h in cited_hits if item.supported_by(h)), None,
                            ttft_ms=round(ttft or total, 1), total_ms=round(total, 1), answer=parsed.text)
        if judge is not None and parsed.status == "answered":
            sources = "\n\n".join(answer_mod.format_source(i, h) for i, h in
                                  zip(parsed.citations, cited_hits))
            facts = "\n".join(f"- {f}" for f in item.key_facts) or "(none)"
            verdict = await judge.chat_json([{"role": "user", "content": _JUDGE_PROMPT.format(
                sources=sources, question=item.question, answer=parsed.text, facts=facts)}],
                JUDGE_SCHEMA, temperature=0.0, max_tokens=600)
            record.faithful = bool(verdict.get("faithful"))
            record.unsupported_claims = verdict.get("unsupported_claims", [])[:5]
            covered = verdict.get("facts_covered") or []
            if item.key_facts:
                record.facts_covered = round(sum(bool(c) for c in covered[: len(item.key_facts)])
                                             / len(item.key_facts), 3)
        results.append(record)
        if n % 10 == 0:
            progress(f"  {n}/{len(items)} answered")
    return {"summary": summarize(results), "items": [asdict(r) for r in results]}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def summarize(results: list[AnswerItem]) -> dict:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    answered = [r for r in results if r.status == "answered"]
    judged = [r for r in answered if r.faithful is not None]
    covered = [r.facts_covered for r in answered if r.answerable and r.facts_covered is not None]
    cited = sum(len(r.citations) for r in answered if r.answerable)
    said_not_found = [r for r in results if r.status == "not_found"]
    ttft = [r.ttft_ms for r in results if r.ttft_ms is not None]
    totals = [r.total_ms for r in results if r.total_ms]
    return {
        "items": len(results),
        "answer_rate": _ratio(sum(r.status == "answered" for r in answerable), len(answerable)),
        "refusal_recall": _ratio(sum(r.status == "not_found" for r in unanswerable), len(unanswerable)),
        "refusal_precision": _ratio(sum(not r.answerable for r in said_not_found), len(said_not_found)),
        "citation_precision": _ratio(sum(r.cited_supporting for r in answered if r.answerable), cited),
        "faithfulness": _ratio(sum(r.faithful for r in judged), len(judged)),
        "key_fact_coverage": round(statistics.mean(covered), 3) if covered else None,
        "valid_json": _ratio(sum(r.valid_json for r in results), len(results)),
        "errors": sum(r.status == "error" for r in results),
        "ttft_p50_ms": round(statistics.median(ttft), 1) if ttft else None,
        "total_p50_ms": round(statistics.median(totals), 1) if totals else None,
        "total_p95_ms": round(sorted(totals)[int(0.95 * (len(totals) - 1))], 1) if totals else None,
    }
