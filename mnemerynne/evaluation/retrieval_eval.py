"""Retrieval metrics over a gold set.

For answerable items: rank of the first supporting chunk among all fused
candidates (recall@k, MRR), and whether one reached the model's context.
For unanswerable items: whether the not-found gate (no keyword hit and no
close vector) rejected the question before any model call.
"""

from __future__ import annotations

import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass

from mnemerynne import retrieval, users
from mnemerynne.config import Settings
from mnemerynne.evaluation import GoldItem

KS = (1, 3, 5, 10, 20)


@dataclass
class ItemResult:
    id: str
    answerable: bool
    rank: int | None            # 1-based rank of first supporting candidate
    in_context: bool            # a supporting chunk was in the model's context
    gated: bool                 # search returned no context (not-found shortcut)
    latency_ms: float
    best_distance: float | None  # closest vector (for calibrating the gate)
    lexical_hits: int
    top: list[str]              # top candidates, for reading failures


def _allowed(conn: sqlite3.Connection, item: GoldItem) -> set[int]:
    if item.as_user:
        user = users.get_user(conn, item.as_user)
        if user is None:
            raise ValueError(f"Gold item {item.id}: unknown user {item.as_user!r}")
        from mnemerynne import permissions
        return permissions.readable_collection_ids(conn, user["id"])
    return {r[0] for r in conn.execute("SELECT id FROM collections")}


def evaluate(conn: sqlite3.Connection, settings: Settings, embedder, items: list[GoldItem],
             reranker=None) -> dict:
    results: list[ItemResult] = []
    for item in items:
        question = item.question
        started = time.perf_counter()
        result = retrieval.search(conn, settings, embedder, question, _allowed(conn, item), reranker)
        latency = (time.perf_counter() - started) * 1000
        rank = next((i for i, h in enumerate(result.candidates, start=1) if item.supported_by(h)), None)
        results.append(ItemResult(
            id=item.id,
            answerable=item.answerable,
            rank=rank,
            in_context=any(item.supported_by(h) for h in result.hits),
            gated=not result.hits,
            latency_ms=round(latency, 1),
            best_distance=None if result.best_distance is None else round(result.best_distance, 4),
            lexical_hits=result.lexical_hits,
            top=[f"{h.file_name} {h.location} [{h.section_path[-60:]}]" for h in result.candidates[:3]],
        ))
    return {"summary": summarize(results), "items": [asdict(r) for r in results]}


def summarize(results: list[ItemResult]) -> dict:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    summary: dict = {"items": len(results), "answerable": len(answerable), "unanswerable": len(unanswerable)}
    if answerable:
        for k in KS:
            summary[f"recall@{k}"] = round(sum(1 for r in answerable if r.rank and r.rank <= k) / len(answerable), 3)
        summary["mrr"] = round(sum(1 / r.rank for r in answerable if r.rank) / len(answerable), 3)
        summary["context_recall"] = round(sum(r.in_context for r in answerable) / len(answerable), 3)
        summary["wrongly_gated"] = round(sum(r.gated for r in answerable) / len(answerable), 3)
    if unanswerable:
        summary["gate_rejects_unanswerable"] = round(sum(r.gated for r in unanswerable) / len(unanswerable), 3)
    latencies = [r.latency_ms for r in results]
    if latencies:
        summary["latency_p50_ms"] = round(statistics.median(latencies), 1)
        summary["latency_p95_ms"] = round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1)
    return summary
