"""Training data for the Erynne model, distilled from the teacher (Qwen3-8B).

Every example is produced through the production code paths: real retrieval
for a user's scope, `answer.build_messages`, the graph's condense prompt and
the memory prompt, so the student is trained on exactly what it will see.
The teacher's outputs are filtered (schema, citations, a judge pass) before
they are kept. Chunks that back gold evaluation items are excluded so the
evaluation stays honest.

Output: JSONL, one chat example per line:
    {"task": "answer|not_found|condense|memory",
     "messages": [{"role": "system"|"user"|"assistant", "content": ...}],
     "meta": {...}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sqlite3
import threading
from pathlib import Path

from mnemerynne import answer as answer_mod
from mnemerynne import graph as graph_mod
from mnemerynne import db, memory, retrieval
from mnemerynne import teacher as teacher_scope
from mnemerynne.clients.llm import LLM, BudgetExceeded
from mnemerynne.config import Settings
from mnemerynne.evaluation import GoldItem
from mnemerynne.evaluation.answer_eval import JUDGE_SCHEMA, _JUDGE_PROMPT
from mnemerynne.evaluation.draft import _OFFTOPIC_PROMPT, _QUESTION_PROMPT, OFFTOPIC_SCHEMA, QUESTION_SCHEMA, STYLES

log = logging.getLogger("mnemerynne.sft")
_INLINE_MARKER = re.compile(r"\[\d{1,2}\]")

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {"follow_up": {"type": "string"}, "standalone": {"type": "string"}},
    "required": ["follow_up", "standalone"],
}
_FOLLOWUP_PROMPT = """A hotel employee asked: "{question}"
and got this answer: "{answer}"

Write a natural follow-up message they might send next that only makes sense with the conversation
(e.g. "and what about for group bookings?", "where do I find that screen?"), and the same follow-up
rewritten as a standalone question."""

MESSAGES_SCHEMA = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {"type": "string"}}},
    "required": ["messages"],
}
_MEMORY_MESSAGES_PROMPT = """Write {n} varied messages hotel staff might send to a document assistant.
About a third should mention a fact about the writer (their property, role, department, shift, or how
they like answers), the rest should be plain questions with no personal facts. A few should mention
things that must NOT be remembered (a password, a guest's name or room, a card number)."""


def _excluded_chunks(conn: sqlite3.Connection, gold: list[GoldItem]) -> set[int]:
    """Chunks overlapping any gold support (same file and page/anchor)."""
    out: set[int] = set()
    for item in gold:
        for s in item.support:
            for r in conn.execute(
                    "SELECT c.id, c.page_start, c.page_end, c.anchor FROM chunks c JOIN files f ON f.id = c.file_id "
                    "WHERE lower(f.display_name) = lower(?)", (s.file,)):
                if s.pages:
                    if any((r["page_start"] or 0) <= p <= (r["page_end"] or r["page_start"] or 0) for p in s.pages):
                        out.add(r["id"])
                elif s.anchors:
                    if r["anchor"] in s.anchors:
                        out.add(r["id"])
                else:
                    out.add(r["id"])
    return out


async def _safe(coro):
    """Run one example's teacher calls; a failure skips that example, not the run."""
    try:
        return await coro
    except BudgetExceeded:
        raise
    except Exception as exc:
        log.warning("Example skipped after a teacher/search error: %s: %s", type(exc).__name__, exc)
        return None


def _example(task: str, messages: list[dict], assistant: dict | str, **meta) -> dict:
    content = assistant if isinstance(assistant, str) else json.dumps(assistant, ensure_ascii=False)
    return {"task": task, "messages": [*messages, {"role": "assistant", "content": content}], "meta": meta}


class Generator:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, embedder, teacher: LLM,
                 excluded: set[int], seed: int = 11, concurrency: int = 2) -> None:
        self.conn, self.settings, self.embedder, self.teacher = conn, settings, embedder, teacher
        self.excluded = excluded
        self.rng = random.Random(seed)
        self.sem = asyncio.Semaphore(concurrency)
        # Everything for a local teacher; only allowed libraries for a remote one.
        self.all_collections = teacher_scope.allowed_collections(conn, settings, teacher)
        self._local = threading.local()

    def _thread_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = db.connect(self.settings.db_path)
        return conn

    async def _search(self, question: str) -> retrieval.SearchResult:
        """Search off the event loop (own connection per worker thread), so
        teacher calls keep flowing while retrieval runs."""
        return await asyncio.to_thread(lambda: retrieval.search(
            self._thread_conn(), self.settings, self.embedder, question, self.all_collections))

    async def _json(self, prompt: str | list[dict], schema: dict, **kw) -> dict:
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        async with self.sem:
            return await self.teacher.chat_json(messages, schema, **kw)

    def _chunks(self, n: int) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT c.*, f.display_name FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.tokens >= 60"
        ).fetchall()
        rows = [r for r in rows if r["id"] not in self.excluded and r["collection_id"] in self.all_collections]
        # Weighted sampling without replacement, weight 1/sqrt(chunks in file): the
        # 3,300-page manual still dominates, but small documents are well represented.
        per_file: dict[int, int] = {}
        for r in rows:
            per_file[r["file_id"]] = per_file.get(r["file_id"], 0) + 1
        keyed = sorted(rows, key=lambda r: self.rng.random() ** (per_file[r["file_id"]] ** 0.5), reverse=True)
        return keyed[: min(n, len(keyed))]

    async def _question(self, chunk_row) -> str | None:
        out = await self._json(_QUESTION_PROMPT.format(
            file=chunk_row["display_name"], section=chunk_row["section_path"] or "-",
            text=chunk_row["text"][:2500], style=self.rng.choice(STYLES)), QUESTION_SCHEMA,
            temperature=0.8, max_tokens=300)
        return out["question"].strip() if out.get("usable") and out.get("question", "").strip() else None

    async def _teacher_answer(self, messages) -> dict:
        return await self._json(messages, answer_mod.ANSWER_SCHEMA, temperature=0.2, max_tokens=900)

    async def _faithful(self, question: str, hits, parsed) -> bool:
        cited = [hits[i - 1] for i in parsed.citations]
        sources = "\n\n".join(answer_mod.format_source(i, h) for i, h in zip(parsed.citations, cited))
        verdict = await self._json(_JUDGE_PROMPT.format(sources=sources, question=question, answer=parsed.text,
                                                        facts="(none)"), JUDGE_SCHEMA, temperature=0.0,
                                   max_tokens=500)
        return bool(verdict.get("faithful")) and not verdict.get("unsupported_claims")

    # ------------------------------------------------------------------ tasks
    async def answer_example(self, chunk_row) -> dict | None:
        """Answerable: real retrieval; keep only faithful answers that cite the source chunk."""
        question = await self._question(chunk_row)
        if not question:
            return None
        hits = (await self._search(question)).hits
        source_idx = next((i for i, h in enumerate(hits, start=1) if h.chunk_id == chunk_row["id"]), None)
        if source_idx is None:
            return None  # retrieval missed it; the example would teach the wrong lesson
        messages = answer_mod.build_messages(question, hits)
        out = await self._teacher_answer(messages)
        parsed = answer_mod.parse_answer(json.dumps(out), len(hits))
        if parsed.status != "answered" or source_idx not in parsed.citations:
            return None
        if not _INLINE_MARKER.search(parsed.text):
            return None  # teach inline [n] citations, the stock model's weak spot
        if not await self._faithful(question, hits, parsed):
            return None
        target = {"status": "answered", "answer": parsed.text, "citations": parsed.citations}
        return _example("answer", messages, target, chunk_id=chunk_row["id"], file=chunk_row["display_name"])

    async def near_miss_example(self, chunk_row) -> dict | None:
        """Not found: the passage that answers is withheld from the sources."""
        question = await self._question(chunk_row)
        if not question:
            return None
        result = await self._search(question)
        hits = [h for h in result.candidates
                if not (h.file_name == chunk_row["display_name"] and h.section_path == chunk_row["section_path"])]
        hits = hits[: self.settings.context_chunks]
        if not hits:
            return None
        messages = answer_mod.build_messages(question, hits)
        out = await self._teacher_answer(messages)
        if out.get("status") != "not_found":
            return None  # the teacher found an answer elsewhere; ambiguous, skip
        target = {"status": "not_found", "answer": "", "citations": []}
        return _example("not_found", messages, target, kind="near-miss", chunk_id=chunk_row["id"])

    async def offtopic_examples(self, n: int) -> list[dict]:
        marks = ",".join(str(int(i)) for i in self.all_collections) or "NULL"
        titles = "\n".join(f"- {r[0]}" for r in self.conn.execute(
            f"SELECT DISTINCT display_name FROM files WHERE collection_id IN ({marks})"))
        out = await self._json(_OFFTOPIC_PROMPT.format(titles=titles, n=n), OFFTOPIC_SCHEMA,
                               temperature=0.9, max_tokens=2000)
        examples = []
        for question in out.get("questions", [])[:n]:
            hits = (await self._search(question)).candidates
            hits = hits[: self.settings.context_chunks]
            if not hits:
                continue
            messages = answer_mod.build_messages(question, hits)
            answer = await self._teacher_answer(messages)
            if answer.get("status") == "not_found":
                examples.append(_example("not_found", messages, {"status": "not_found", "answer": "",
                                                                  "citations": []}, kind="off-topic"))
        return examples

    async def condense_example(self, answer_ex: dict) -> dict | None:
        user = answer_ex["messages"][1]["content"]
        question = user.rsplit("Question:", 1)[1].strip()
        answer = json.loads(answer_ex["messages"][-1]["content"])["answer"]
        out = await self._json(_FOLLOWUP_PROMPT.format(question=question, answer=answer[:600]), FOLLOWUP_SCHEMA,
                               temperature=0.8, max_tokens=200)
        follow, standalone = out.get("follow_up", "").strip(), out.get("standalone", "").strip()
        if not follow or not standalone:
            return None
        history = f"User: {question}\n{answer_mod.ASSISTANT_NAME}: {answer[:400]}"
        prompt = graph_mod._CONDENSE_PROMPT.format(history=history, question=follow)
        return _example("condense", [{"role": "user", "content": prompt}], {"question": standalone})

    async def general_example(self, message: str, route: str) -> dict | None:
        """Small talk / general help: plain-text reply from the production general prompt."""
        messages = answer_mod.build_general_messages(message)
        async with self.sem:
            reply = (await self.teacher.chat(messages, temperature=0.6, max_tokens=700)).strip()
        if not reply or len(reply) > 3000 or "[1]" in reply or reply.lstrip().startswith("{"):
            return None
        return _example("general", messages, reply, route=route)

    async def memory_examples(self, n: int) -> list[dict]:
        out = await self._json(_MEMORY_MESSAGES_PROMPT.format(n=n), MESSAGES_SCHEMA, temperature=0.9,
                               max_tokens=2500)
        examples = []
        for message in out.get("messages", [])[:n]:
            prompt = memory._PROMPT.format(message=message[:2000])
            facts = (await self._json(prompt, memory.MEMORY_SCHEMA, temperature=0.0, max_tokens=200)).get("facts", [])
            safe = [f for f in facts if isinstance(f, str) and memory.is_safe(f)]
            examples.append(_example("memory", [{"role": "user", "content": prompt}], {"facts": safe}))
        return examples


async def generate(conn: sqlite3.Connection, settings: Settings, embedder, teacher: LLM, gold: list[GoldItem],
                   out_path: Path, n_answer: int = 2000, n_near_miss: int = 500, n_offtopic: int = 200,
                   n_condense: int = 400, n_memory: int = 300, n_general: int = 400,
                   router_messages: Path | None = None, seed: int = 11, concurrency: int = 2,
                   rounds: int = 4, progress=print) -> dict:
    """Generate in `rounds` passes, each producing an equal share of every task,
    so stopping early (budget, Ctrl+C) still leaves a balanced data set."""
    gen = Generator(conn, settings, embedder, teacher, _excluded_chunks(conn, gold), seed=seed,
                    concurrency=concurrency)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    batch_size = max(16, concurrency * 3)
    answers: list[dict] = []
    answer_pool = gen._chunks(n_answer * 3)
    near_pool = gen._chunks(n_near_miss * 3)
    general_pool: list[tuple[str, str]] = []
    if router_messages and router_messages.exists() and n_general:
        from mnemerynne.router_data import load as load_router_messages
        texts, labels = load_router_messages(router_messages)
        general_pool = [(t, label) for t, label in zip(texts, labels) if label != "search"]
        gen.rng.shuffle(general_pool)
        general_pool = general_pool[:n_general]

    def spend() -> str:
        spent = getattr(teacher, "spent_usd", None)
        return f" | ${spent:.2f}" if getattr(teacher, "is_remote", False) and spent is not None else ""

    def write(fh, examples):
        for ex in examples:
            if ex:
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                counts[ex["task"]] = counts.get(ex["task"], 0) + 1
        fh.flush()

    with out_path.open("a", encoding="utf-8") as fh:
        try:
            for r in range(1, rounds + 1):
                def goal(n: int) -> int:
                    return -(-n * r // rounds)  # ceil(n * r / rounds)

                while answer_pool and counts.get("answer", 0) < goal(n_answer):
                    batch, answer_pool[:] = answer_pool[:batch_size], answer_pool[batch_size:]
                    got = [e for e in await asyncio.gather(*(_safe(gen.answer_example(c)) for c in batch)) if e]
                    answers.extend(got)
                    write(fh, got)
                    progress(f"[round {r}/{rounds}] answer {counts.get('answer', 0)}/{n_answer}{spend()}")

                near = lambda: counts.get("near_miss_count", 0)  # noqa: E731
                while near_pool and near() < goal(n_near_miss):
                    batch, near_pool[:] = near_pool[:batch_size], near_pool[batch_size:]
                    got = [e for e in await asyncio.gather(*(_safe(gen.near_miss_example(c)) for c in batch)) if e]
                    write(fh, got)
                    counts["near_miss_count"] = near() + len(got)
                    progress(f"[round {r}/{rounds}] near-miss {near()}/{n_near_miss}{spend()}")

                offtopic = lambda: counts.get("offtopic_count", 0)  # noqa: E731
                tries = 0
                while offtopic() < goal(n_offtopic) and tries < 20:
                    tries += 1
                    got = await _safe(gen.offtopic_examples(25)) or []
                    write(fh, got)
                    counts["offtopic_count"] = offtopic() + len(got)
                    progress(f"[round {r}/{rounds}] off-topic {offtopic()}/{n_offtopic}{spend()}")

                done = counts.get("condense", 0)
                todo = answers[done: min(goal(n_condense), len(answers))]
                for i in range(0, len(todo), batch_size):
                    write(fh, await asyncio.gather(*(_safe(gen.condense_example(a)) for a in todo[i:i + batch_size])))
                    progress(f"[round {r}/{rounds}] condense {counts.get('condense', 0)}/{n_condense}{spend()}")

                tries = 0
                while counts.get("memory", 0) < goal(n_memory) and tries < max(5, n_memory // 20):
                    tries += 1
                    write(fh, await _safe(gen.memory_examples(30)) or [])
                    progress(f"[round {r}/{rounds}] memory {counts.get('memory', 0)}/{n_memory}{spend()}")

                lo = -(-len(general_pool) * (r - 1) // rounds)
                hi = -(-len(general_pool) * r // rounds)
                part = general_pool[lo:hi]
                for i in range(0, len(part), batch_size):
                    batch = part[i:i + batch_size]
                    write(fh, await asyncio.gather(*(_safe(gen.general_example(t, label)) for t, label in batch)))
                    progress(f"[round {r}/{rounds}] general {counts.get('general', 0)}/{len(general_pool)}{spend()}")
        except BudgetExceeded as exc:
            progress(f"Stopped: {exc} The data written so far is balanced across tasks and usable.")
    counts.pop("near_miss_count", None)
    counts.pop("offtopic_count", None)
    return counts
