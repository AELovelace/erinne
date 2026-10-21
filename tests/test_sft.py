import asyncio
import io
import json
import re

from mnemerynne import graph as graph_mod
from mnemerynne import memory, permissions, users, worker
from mnemerynne.answer import ANSWER_SCHEMA
from mnemerynne.evaluation import GoldItem, Support
from mnemerynne.evaluation.answer_eval import JUDGE_SCHEMA
from mnemerynne.evaluation.draft import OFFTOPIC_SCHEMA, QUESTION_SCHEMA
from mnemerynne.ingest import store
from mnemerynne.sft import FOLLOWUP_SCHEMA, MESSAGES_SCHEMA, generate
from fakes import KeywordEmbedder
from pdfgen import make_pdf

TOPICS = ["audit", "pool", "checkout", "parking", "pets"]


class FakeTeacher:
    """Answers every prompt type the generator uses, deterministically."""

    async def chat_json(self, messages, schema, **kw):
        text = messages[-1]["content"]
        if schema is QUESTION_SCHEMA:
            topic = next((t for t in TOPICS if t in text.split("Passage:", 1)[1].lower()), "pool")
            return {"usable": True, "question": f"what is the {topic} rule", "key_facts": [topic], "kind": "factual"}
        if schema is ANSWER_SCHEMA:
            question = text.rsplit("Question:", 1)[1].lower()
            topic = next((t for t in TOPICS if t in question), None)
            sources = re.findall(r"\[(\d+)\] [^\n]*\n([^\n]*)", text)
            match = next((int(n) for n, body in sources if topic and topic in body.lower()), None)
            if match is None:
                return {"status": "not_found", "answer": "", "citations": []}
            return {"status": "answered", "answer": f"The {topic} rule is described here [{match}].",
                    "citations": [match]}
        if schema is JUDGE_SCHEMA:
            return {"unsupported_claims": [], "faithful": True, "facts_covered": []}
        if schema is OFFTOPIC_SCHEMA:
            return {"questions": ["what is the wifi password", "who won the game"]}
        if schema is FOLLOWUP_SCHEMA:
            return {"follow_up": "and on weekends?", "standalone": "What is the rule on weekends?"}
        if schema is MESSAGES_SCHEMA:
            return {"messages": ["I work nights at the Portland property", "where is the pool"]}
        if schema is memory.MEMORY_SCHEMA:
            message = text.rsplit('Message: """', 1)[1].lower()  # the prompt's own example mentions Portland
            return {"facts": ["Works nights at the Portland property."] if "portland" in message else []}
        raise AssertionError(f"unexpected schema {schema}")

    async def chat(self, messages, **kw):
        return "Happy to help! Here's a draft you can copy."


def test_generate_produces_every_task_from_production_prompts(conn, settings, tmp_path):
    embedder = KeywordEmbedder()
    alice = users.create_user(conn, "alice", "password123")
    pages = [[f"{t.title()} Rules", f"The {t} rule for staff is written on this page."] for t in TOPICS]
    store.add_file(conn, settings, store.receive(io.BytesIO(make_pdf(pages, [(f"{t.title()} Rules", i, 0)
                   for i, t in enumerate(TOPICS)])), "manual.pdf", settings),
                   permissions.private_collection_id(conn, alice), alice)
    while worker.run_one(conn, settings, embedder):
        pass
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    settings.context_chunks = 3
    # Chunks behind gold questions must not become training data.
    gold = [GoldItem("g1", "pets?", support=[Support("manual.pdf", pages=[5])])]
    router_messages = tmp_path / "router.jsonl"
    router_messages.write_text("".join(json.dumps(r) + "\n" for r in [
        {"text": "hi!", "label": "smalltalk"}, {"text": "write a welcome note", "label": "general"},
        {"text": "pool hours?", "label": "search"}]), encoding="utf-8")
    out = tmp_path / "sft.jsonl"

    # tokens >= 60 filter: make chunks long enough to be sampled.
    conn.execute("UPDATE chunks SET tokens = 80")
    counts = asyncio.run(generate(conn, settings, embedder, FakeTeacher(), gold, out, n_answer=3, n_near_miss=2,
                                  n_offtopic=1, n_condense=2, n_memory=2, n_general=5,
                                  router_messages=router_messages, progress=lambda *_: None))
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {"answer", "not_found", "condense", "memory", "general"} <= set(counts)
    for row in rows:
        assert row["messages"][-1]["role"] == "assistant"
    answers = [r for r in rows if r["task"] == "answer"]
    assert answers and all("pets" not in r["messages"][1]["content"].split("Question:")[1] for r in answers)
    first = answers[0]
    assert first["messages"][0]["content"].startswith("You are Erynne")       # production system prompt
    assert json.loads(first["messages"][-1]["content"])["citations"]
    nf = [r for r in rows if r["task"] == "not_found"]
    assert all(json.loads(r["messages"][-1]["content"])["status"] == "not_found" for r in nf)
    condense = next(r for r in rows if r["task"] == "condense")
    assert condense["messages"][0]["content"].startswith(graph_mod._CONDENSE_PROMPT.split("\n")[0])
    general = [r for r in rows if r["task"] == "general"]
    assert len(general) == 2 and all(r["meta"]["route"] in ("smalltalk", "general") for r in general)
    mem = [json.loads(r["messages"][-1]["content"]) for r in rows if r["task"] == "memory"]
    assert {"facts": ["Works nights at the Portland property."]} in mem and {"facts": []} in mem
