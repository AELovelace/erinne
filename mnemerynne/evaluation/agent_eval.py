"""Answer evaluation of the agent path: what people get in chat.

Every question in chat goes through the tool-using agent (agent.Agent.run),
not the cited-JSON flow answer_eval.py measures. This runs each gold question
through Agent.run as a real person (their permissions decide what's
searchable), with the same model path as the web app (the balancer nodes, then
MNEMERYNNE_LLM_URL), and scores the result with the same judge.

Read-only: the run never gets the agent group's reach (no API actions, no
mail), and every tool that drafts a change is switched off, so nothing is
proposed. Tool calls are still logged in agent_calls (conversation id
"eval:<item id>"), and read-only API operations really are called.

Refusals: the agent has no not_found status. An answer with no citations that
says it couldn't find the answer (NOT_FOUND_RE) counts as not_found; one with
no citations that doesn't counts as answered, and shows in uncited_answer_rate.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

from mnemerynne import agent as agent_mod
from mnemerynne import answer as answer_mod
from mnemerynne import permissions, retrieval, users
from mnemerynne.evaluation import GoldItem
from mnemerynne.evaluation.answer_eval import _JUDGE_PROMPT, JUDGE_SCHEMA, _ratio

# Tools that draft changes: switched off for every evaluated question.
WRITE_TOOLS = agent_mod.FILE_WRITE_TOOLS | agent_mod.MAIL_WRITE_TOOLS
# Labels reports with the agent's instructions, so eval-gate only compares like with like.
PROMPT_VERSION = "agent-" + hashlib.sha256(agent_mod.SYSTEM_PROMPT.encode()).hexdigest()[:8]
NOT_FOUND_RE = re.compile(
    r"couldn['’]?t find|could not find|can['’]?t find|cannot find|wasn['’]?t able to find|unable to find"
    r"|didn['’]?t find|did not find|found nothing|nothing (?:in|about)|no information"
    r"|(?:isn['’]?t|is not|aren['’]?t|are not) (?:in|covered)|(?:don['’]?t|do not|doesn['’]?t|does not) cover",
    re.IGNORECASE)


class ReadOnlyAgent(agent_mod.Agent):
    """The agent as an ordinary member of staff: never the agent group's extra reach (API actions,
    mail), whoever is evaluated. Counts the model calls each question makes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_calls = 0

    def _gate(self, user_id: int):
        gate = super()._gate(user_id)
        if gate is None:
            return None
        cfg, username = gate
        return {**cfg, "full": False}, username

    def _client_for(self, cfg: dict) -> Any:
        return _Counting(super()._client_for(cfg), self)


class _Counting:
    """Passes everything to the agent's model client, counting chat_tools calls (model turns)."""

    def __init__(self, inner: Any, owner: ReadOnlyAgent):
        self._inner, self._owner = inner, owner

    async def chat_tools(self, *args, **kwargs):
        self._owner.model_calls += 1
        return await self._inner.chat_tools(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@dataclass
class AgentItem:
    id: str
    answerable: bool
    status: str                  # answered | not_found | fallback | error
    citations: list[int]
    cited_supporting: int        # cited sources that match the gold support
    faithful: bool | None
    user: str = ""
    unsupported_claims: list[str] = field(default_factory=list)
    facts_covered: float | None = None
    tool_calls: list[str] = field(default_factory=list)
    model_calls: int = 0
    total_ms: float = 0.0
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    error: str | None = None


def _readable_files(conn, collections: set[int]) -> set[str]:
    if not collections:
        return set()
    marks = ",".join("?" * len(collections))
    rows = conn.execute(f"SELECT lower(display_name) FROM files WHERE deleted_at IS NULL "
                        f"AND collection_id IN ({marks})", sorted(collections))
    return {r[0] for r in rows}


def _cited_hits(conn, sources: list[dict], citations: list[int]) -> list[tuple[int, Any]]:
    """(n, hit-like) for each cited source: chunks as retrieval Hits (with their text), PDF pages as a
    page reference without text; API results, mail and SharePoint as None (no file behind them)."""
    by_n = {s["n"]: s for s in sources}
    chunks = retrieval._load_hits(conn, [by_n[n]["chunk_id"] for n in citations
                                         if n in by_n and by_n[n].get("chunk_id")])
    out = []
    for n in citations:
        src = by_n.get(n)
        if src is None:
            continue
        if src.get("chunk_id") in chunks:
            out.append((n, chunks[src["chunk_id"]]))
        elif src.get("file_id") and src.get("page_start"):
            out.append((n, SimpleNamespace(file_name=src["file"], page_start=src["page_start"],
                                           page_end=src["page_start"], anchor=None, text=None)))
        else:
            out.append((n, None))
    return out


def _judge_sources(n: int, src: dict, hit: Any) -> str:
    if hit is not None and getattr(hit, "text", None):
        return answer_mod.format_source(n, hit)
    where = " · ".join(p for p in (src.get("file"), src.get("location")) if p)
    return f"[{n}] {where}\n(Text not available to the grader: a page image or live result.)"


async def evaluate_agent(conn, agent: ReadOnlyAgent, judge, items: list[GoldItem], username: str | None = None,
                         progress=print) -> dict:
    """username: run every question as this person; otherwise as each item's as_user. Answerable items
    whose supporting files that person can't read are skipped (counted in the summary)."""
    results: list[AgentItem] = []
    skipped: dict[str, list[str]] = {"no_user": [], "support_not_readable": []}
    readable_cache: dict[int, set[str]] = {}
    for n, item in enumerate(items, start=1):
        name = username or item.as_user
        user = users.get_user(conn, name) if name else None
        if user is None or not user["active"]:
            skipped["no_user"].append(item.id)
            continue
        if user["id"] not in readable_cache:
            readable_cache[user["id"]] = _readable_files(conn, permissions.readable_collection_ids(conn, user["id"]))
        if item.answerable and not all(s.file.lower() in readable_cache[user["id"]] for s in item.support):
            skipped["support_not_readable"].append(item.id)
            continue
        results.append(await _one(conn, agent, judge, item, user))
        if n % 10 == 0:
            progress(f"  {n}/{len(items)} answered")
    summary = summarize(results)
    summary["skipped_no_user"] = len(skipped["no_user"])
    summary["skipped_support_not_readable"] = len(skipped["support_not_readable"])
    return {"summary": summary, "skipped": skipped, "items": [asdict(r) for r in results]}


async def _one(conn, agent: ReadOnlyAgent, judge, item: GoldItem, user) -> AgentItem:
    events: list[dict] = []
    state = {"user_id": user["id"], "question": item.question, "memories": [],
             "turns": [t for t in item.history if "question" in t and "answer" in t],
             "disabled_tools": sorted(WRITE_TOOLS)}
    agent.model_calls = 0
    started = time.perf_counter()
    try:
        result = await agent.run(state, events.append, f"eval:{item.id}")
    except Exception as exc:   # report it and carry on with the next question
        return AgentItem(item.id, item.answerable, "error", [], 0, None, user=user["username"],
                         model_calls=agent.model_calls, total_ms=round((time.perf_counter() - started) * 1000, 1),
                         error=f"{type(exc).__name__}: {exc}")
    total = round((time.perf_counter() - started) * 1000, 1)
    tools = [e["name"] for e in events if e.get("type") == "tool" and e.get("state") == "running"]
    if result is None:   # the agent model didn't answer; chat would have used the normal flow
        return AgentItem(item.id, item.answerable, "fallback", [], 0, None, user=user["username"],
                         tool_calls=tools, model_calls=agent.model_calls, total_ms=total)
    answer = result["answer"]
    sources = next((e["sources"] for e in reversed(events) if e.get("type") == "sources"), [])
    citations = list(answer.get("citations") or [])
    text = answer.get("text") or ""
    status = "not_found" if not citations and NOT_FOUND_RE.search(text) else "answered"
    cited = _cited_hits(conn, sources, citations)
    record = AgentItem(item.id, item.answerable, status, citations,
                       sum(1 for _, h in cited if h is not None and item.supported_by(h)), None,
                       user=user["username"], tool_calls=tools, model_calls=agent.model_calls, total_ms=total,
                       answer=text, sources=sources)
    if judge is not None and status == "answered":
        by_n = {s["n"]: s for s in sources}
        graded = "\n\n".join(_judge_sources(n, by_n[n], h) for n, h in cited) or "(none: the answer cites nothing)"
        facts = "\n".join(f"- {f}" for f in item.key_facts) or "(none)"
        verdict = await judge.chat_json([{"role": "user", "content": _JUDGE_PROMPT.format(
            sources=graded, question=item.question, answer=text, facts=facts)}],
            JUDGE_SCHEMA, temperature=0.0, max_tokens=600)
        record.faithful = bool(verdict.get("faithful"))
        record.unsupported_claims = verdict.get("unsupported_claims", [])[:5]
        covered = verdict.get("facts_covered") or []
        if item.key_facts:
            record.facts_covered = round(sum(bool(c) for c in covered[: len(item.key_facts)])
                                         / len(item.key_facts), 3)
    return record


def _p95(values: list[float]) -> float | None:
    return round(sorted(values)[int(0.95 * (len(values) - 1))], 1) if values else None


def summarize(results: list[AgentItem]) -> dict:
    """The same quality metrics as answer_eval.summarize (so eval-gate can compare agent reports),
    plus the agent's own: tool calls, model turns, fallbacks and uncited answers."""
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    answered = [r for r in results if r.status == "answered"]
    judged = [r for r in answered if r.faithful is not None]
    covered = [r.facts_covered for r in answered if r.answerable and r.facts_covered is not None]
    cited = sum(len(r.citations) for r in answered if r.answerable)
    said_not_found = [r for r in results if r.status == "not_found"]
    ran = [r for r in results if r.status not in ("fallback", "error")]
    totals = [r.total_ms for r in ran if r.total_ms]
    calls = [len(r.tool_calls) for r in ran]
    return {
        "path": "agent",
        "items": len(results),
        "answer_rate": _ratio(sum(r.status == "answered" for r in answerable), len(answerable)),
        "refusal_recall": _ratio(sum(r.status == "not_found" for r in unanswerable), len(unanswerable)),
        "refusal_precision": _ratio(sum(not r.answerable for r in said_not_found), len(said_not_found)),
        "citation_precision": _ratio(sum(r.cited_supporting for r in answered if r.answerable), cited),
        "faithfulness": _ratio(sum(r.faithful for r in judged), len(judged)),
        "key_fact_coverage": round(statistics.mean(covered), 3) if covered else None,
        "uncited_answer_rate": _ratio(sum(not r.citations for r in answered), len(answered)),
        "fallback_rate": _ratio(sum(r.status == "fallback" for r in results), len(results)),
        "errors": sum(r.status == "error" for r in results),
        "tool_calls_mean": round(statistics.mean(calls), 2) if calls else None,
        "tool_calls_p95": _p95([float(c) for c in calls]),
        "model_calls_mean": round(statistics.mean(r.model_calls for r in ran), 2) if ran else None,
        "total_p50_ms": round(statistics.median(totals), 1) if totals else None,
        "total_p95_ms": _p95(totals),
    }
