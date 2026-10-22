"""The answering flow, orchestrated with LangGraph.

    load_memories -> condense -> retrieve -> (nothing found? -> not_found)
                                           -> generate -> finish

Only `condense` (follow-ups only) and `generate` call the model; the rest is
plain code. Conversation state is checkpointed per thread (conversation id)
in SQLite; long-term memories live in the LangGraph store, namespaced per user.
Tokens stream out of `generate` through LangGraph's custom stream channel.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import operator
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Annotated, Any, TypedDict

# LangSmith must never be enabled: traces would carry confidential documents
# off the machine. Force it off even if someone sets it in the environment.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langgraph.config import get_store, get_stream_writer  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from mnemerynne import answer as answer_mod  # noqa: E402
from mnemerynne import db, retrieval  # noqa: E402
from mnemerynne.clients.llm import LLM, ThinkFilter  # noqa: E402
from mnemerynne.config import Settings  # noqa: E402

log = logging.getLogger("mnemerynne.graph")

HISTORY_TURNS = 4          # earlier turns the condense step sees
MAX_MEMORIES = 10


def memory_namespace(user_id: int) -> tuple[str, ...]:
    return ("user", str(user_id), "memories")


class State(TypedDict, total=False):
    user_id: int
    scope: dict | None   # "search in": {"collection_id", "folders"}, checked by the web layer
    question: str
    standalone: str
    route: str
    memories: list[str]
    hits: list[dict]
    answer: dict
    turns: Annotated[list[dict], operator.add]   # conversation memory, appended per turn


# Route label (from mnemerynne.router) -> graph node that handles it. Add a flow by
# training the router with a new label and registering its node here. Labels
# without a node fall back to document search.
FLOWS: dict[str, str] = {
    "search": "retrieve",
    "smalltalk": "general",
    "general": "general",
}


CONDENSE_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}

_CONDENSE_PROMPT = """Rewrite the user's latest message as a standalone question that can be understood
without the conversation. Keep their wording where possible; resolve "it", "that", "what about…".
If it is already standalone, return it unchanged.

Conversation so far:
{history}

Latest message: {question}"""


# Per-run node timings, logged to graph_runs (our local replacement for LangSmith).
_trace: contextvars.ContextVar[list | None] = contextvars.ContextVar("mnemerynne_trace", default=None)


def _timed(name):
    def wrap(fn):
        async def node(state, *args, **kwargs):
            started = time.perf_counter()
            try:
                return await fn(state, *args, **kwargs)
            finally:
                trace = _trace.get()
                if trace is not None:
                    trace.append((name, round((time.perf_counter() - started) * 1000)))
        node.__name__ = name
        return node
    return wrap


@dataclass
class Deps:
    settings: Settings
    llm: LLM
    embedder: Any
    reranker: Any = None
    router: Any = None   # mnemerynne.router.Router; None routes everything to search


def build_graph(deps: Deps, checkpointer=None, store=None):
    settings = deps.settings

    @_timed("load_memories")
    async def load_memories(state: State) -> dict:
        store_ = get_store()
        items = await store_.asearch(memory_namespace(state["user_id"]), limit=MAX_MEMORIES)
        return {"memories": [i.value["text"] for i in items]}

    @_timed("condense")
    async def condense(state: State) -> dict:
        turns = state.get("turns") or []
        question = state["question"]
        if not turns:
            return {"standalone": question}
        history = "\n".join(f"User: {t['question']}\n{answer_mod.ASSISTANT_NAME}: {t['answer'][:400]}"
                            for t in turns[-HISTORY_TURNS:])
        try:
            out = await deps.llm.chat_json(
                [{"role": "user", "content": _CONDENSE_PROMPT.format(history=history, question=question)}],
                CONDENSE_SCHEMA, temperature=0.0, max_tokens=120)
            standalone = str(out.get("question") or "").strip() or question
        except ValueError as exc:   # BadModelOutput: rewriting only helps search, so carry on
            log.warning("Couldn't rewrite a follow-up; searching with it as asked. %s", exc)
            standalone = question
        return {"standalone": standalone}

    @_timed("retrieve")
    async def retrieve(state: State) -> dict:
        def run() -> retrieval.SearchResult:
            conn = db.connect(settings.db_path)
            try:
                return retrieval.search_for_user(conn, settings, deps.embedder, state["user_id"],
                                                 state["standalone"], deps.reranker, state.get("scope"))
            finally:
                conn.close()

        result = await asyncio.to_thread(run)
        hits = [asdict(h) for h in result.hits]
        get_stream_writer()({"type": "sources", "sources": [_source_view(n, h) for n, h in
                                                          enumerate(result.hits, start=1)]})
        return {"hits": hits}

    @_timed("generate")
    async def generate(state: State) -> dict:
        hits = [retrieval.Hit(**h) for h in state["hits"]]
        messages = answer_mod.build_messages(state["standalone"], hits, state.get("memories"))
        writer = get_stream_writer()
        streamer = answer_mod.AnswerStreamer()
        async for delta in deps.llm.stream(messages, temperature=0.1, max_tokens=900,
                                           json_schema=answer_mod.ANSWER_SCHEMA):
            text = streamer.feed(delta)
            if text:
                writer({"type": "delta", "text": text})
        parsed = answer_mod.parse_answer(streamer.buffer, len(hits))
        if parsed.status == "not_found":
            # Anything streamed was an answer that didn't pass the checks: clear it.
            writer({"type": "reset"})
            return {"answer": {"status": "not_found", "text": await sorry(state["question"], writer),
                               "citations": []}}
        return {"answer": {"status": parsed.status, "text": parsed.text, "citations": parsed.citations}}

    async def sorry(question: str, writer) -> str:
        """A short "couldn't find it" in the model's own words, streamed; the
        fixed NOT_FOUND_TEXT if the model fails, says nothing or rambles."""
        visible, parts = ThinkFilter(), []
        try:
            async with asyncio.timeout(30):
                async for delta in deps.llm.stream(answer_mod.build_sorry_messages(question),
                                                   temperature=0.8, max_tokens=80):
                    if shown := visible.feed(delta):
                        parts.append(shown)
                        writer({"type": "delta", "text": shown})
            if shown := visible.close():
                parts.append(shown)
                writer({"type": "delta", "text": shown})
        except Exception as exc:   # model down or slow: never fail the answer over an apology
            log.warning("Couldn't write the not-found reply: %s", exc)
        text = " ".join("".join(parts).split())
        if text and len(text) <= answer_mod.SORRY_MAX_CHARS:
            return text
        if parts:
            writer({"type": "reset"})
        writer({"type": "delta", "text": answer_mod.NOT_FOUND_TEXT})
        return answer_mod.NOT_FOUND_TEXT

    @_timed("not_found")
    async def not_found(state: State) -> dict:
        text = await sorry(state["question"], get_stream_writer())
        return {"answer": {"status": "not_found", "text": text, "citations": []}}

    @_timed("route")
    async def route_message(state: State) -> dict:
        """Pick the flow from the question's embedding (no model call)."""
        if deps.router is None:
            return {"route": "search"}
        vector = await asyncio.to_thread(deps.embedder.embed_query, state["standalone"])
        decision = deps.router.decide(vector)
        get_stream_writer()({"type": "route", "route": decision.route, "probabilities": decision.probabilities})
        return {"route": decision.route}

    @_timed("general")
    async def general(state: State) -> dict:
        """Small talk and general help: plain-text reply, no documents, no citations."""
        messages = answer_mod.build_general_messages(state["question"], state.get("turns"), state.get("memories"))
        writer = get_stream_writer()
        writer({"type": "sources", "sources": []})
        parts = []
        # Reasoning models may think first; people only see the reply.
        visible = ThinkFilter()
        async for delta in deps.llm.stream(messages, temperature=0.6, max_tokens=900):
            if shown := visible.feed(delta):
                parts.append(shown)
                writer({"type": "delta", "text": shown})
        if shown := visible.close():
            parts.append(shown)
            writer({"type": "delta", "text": shown})
        text = "".join(parts).strip()
        return {"answer": {"status": "answered", "text": text, "citations": []}}

    async def finish(state: State) -> dict:
        a = state["answer"]
        return {"turns": [{"question": state["question"], "standalone": state.get("standalone", ""),
                           "answer": a["text"], "status": a["status"], "route": state.get("route", "search")}]}

    def after_route(state: State) -> str:
        return FLOWS.get(state.get("route", "search"), "retrieve")

    def after_retrieve(state: State) -> str:
        return "generate" if state.get("hits") else "not_found"

    graph = StateGraph(State)
    graph.add_node("load_memories", load_memories)
    graph.add_node("condense", condense)
    graph.add_node("route", route_message)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("not_found", not_found)
    graph.add_node("general", general)
    graph.add_node("finish", finish)
    graph.add_edge(START, "load_memories")
    graph.add_edge("load_memories", "condense")
    graph.add_edge("condense", "route")
    graph.add_conditional_edges("route", after_route, sorted(set(FLOWS.values())))
    graph.add_conditional_edges("retrieve", after_retrieve, ["generate", "not_found"])
    graph.add_edge("general", "finish")
    graph.add_edge("generate", "finish")
    graph.add_edge("not_found", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer, store=store)


def _source_view(n: int, hit: retrieval.Hit) -> dict:
    return {"n": n, "file": hit.file_name, "location": hit.location, "section": hit.section_path,
            "url": hit.url, "chunk_id": hit.chunk_id, "file_id": hit.file_id,
            "page_start": hit.page_start, "anchor": hit.anchor}


async def run_turn(graph, user_id: int, conversation_id: str, question: str, scope: dict | None = None):
    """Run one question through the graph, yielding stream events, then a final
    {"type": "final", ...} event with the answer, cited sources and timings."""
    trace: list = []
    token = _trace.set(trace)
    started = time.perf_counter()
    sources: list[dict] = []
    try:
        config = {"configurable": {"thread_id": conversation_id}}
        async for event in graph.astream({"user_id": user_id, "scope": scope, "question": question, "hits": [],
                                          "answer": {}, "route": "search"}, config, stream_mode="custom"):
            if event.get("type") == "sources":
                sources = event["sources"]
            yield event
        state = (await graph.aget_state(config)).values
    finally:
        _trace.reset(token)
    a = state.get("answer") or {}
    cited = set(a.get("citations") or [])
    yield {"type": "final", "status": a.get("status", "not_found"), "answer": a.get("text", ""),
           "citations": a.get("citations", []), "standalone": state.get("standalone", question),
           "route": state.get("route", "search"),
           "sources": [{**s, "cited": s["n"] in cited} for s in sources],
           "latency_ms": round((time.perf_counter() - started) * 1000), "trace": trace}


def log_trace(conn: sqlite3.Connection, conversation_id: str, trace: list) -> None:
    conn.executemany("INSERT INTO graph_runs(conversation_id, node, duration_ms) VALUES (?, ?, ?)",
                     [(conversation_id, node, ms) for node, ms in trace])


def user_can_read_conversation(conn: sqlite3.Connection, user_id: int, conversation_id: str) -> bool:
    row = conn.execute("SELECT user_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return row is not None and row[0] == user_id
