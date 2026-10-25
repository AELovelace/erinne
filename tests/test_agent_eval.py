"""eval-answers --agent: the chat agent's answers, scored like the cited-JSON path (agent_eval.py)."""

import asyncio
import json

import httpx

from fakes import FakeDirectory
from mnemerynne import accounts
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.evaluation import GoldItem, Support
from mnemerynne.evaluation.agent_eval import NOT_FOUND_RE, WRITE_TOOLS, ReadOnlyAgent, evaluate_agent
from mnemerynne.evaluation.gate import compare
from test_retrieval import embedder, world  # noqa: F401  (fixtures)

GROUP = "Erynne Agents"


class ScriptedModel:
    """The agent model: searches for parking and cites it, admits it can't find wifi, and answers the
    pool question without citing anything."""

    def __init__(self):
        self.offered = []      # tool names offered on each call
        self.systems = []
        self.fail = False

    async def reachable(self):
        return True

    async def chat_tools(self, messages, tools, **kwargs):
        if self.fail:
            raise httpx.ConnectError("model down")
        self.offered.append({t["function"]["name"] for t in tools})
        self.systems.append(messages[0]["content"])
        question = next(m["content"] for m in messages if m["role"] == "user")
        if "parking" in question:
            if messages[-1]["role"] != "tool":
                return ToolTurn("", [ToolCall("c1", "search_documents", {"query": "parking lot B"})])
            cite = json.loads(messages[-1]["content"])["results"][0]["cite"]
            return ToolTurn(f"Parking is in lot B {cite}.")
        if "wifi" in question:
            return ToolTurn("I couldn't find anything about that in your documents.")
        return ToolTurn("The pool is open from 8am to 10pm.")

    async def aclose(self):
        pass


class Judge:
    def __init__(self):
        self.prompts = []

    async def chat_json(self, messages, schema, **kw):
        self.prompts.append(messages[0]["content"])
        return {"unsupported_claims": [], "faithful": True, "facts_covered": [True, False]}


def _agent(settings, embedder, model):  # noqa: F811
    directory = FakeDirectory({"alice": {GROUP}}, groups=[GROUP])
    return ReadOnlyAgent(settings, lambda: embedder, lambda: directory, llm_factory=lambda cfg: model), directory


def test_agent_answers_are_scored_and_nothing_can_be_changed(conn, settings, embedder, world):  # noqa: F811
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    model = ScriptedModel()
    agent, directory = _agent(settings, embedder, model)
    # alice is in the agent group, yet the evaluation never gets its reach.
    accounts.link(conn, world["alice"], "alice", directory)
    accounts.set_agent_group(conn, GROUP)
    items = [
        GoldItem("a1", "where is parking", support=[Support("OPERA.pdf", pages=[3])],
                 key_facts=["Lot B", "registered guests"], as_user="alice"),
        GoldItem("u1", "wifi password reset", answerable=False, as_user="alice"),
        GoldItem("a3", "pool hours", support=[Support("OPERA.pdf", pages=[2])], as_user="alice"),
        GoldItem("s1", "night audit", support=[Support("OPERA.pdf", pages=[1])], as_user="bob"),  # can't read it
        GoldItem("n1", "night audit", support=[Support("OPERA.pdf", pages=[1])]),                # nobody to ask as
    ]
    judge = Judge()
    report = asyncio.run(evaluate_agent(conn, agent, judge, items, progress=lambda *_: None))
    s = report["summary"]
    assert s["path"] == "agent" and s["items"] == 3
    assert s["skipped_support_not_readable"] == 1 and s["skipped_no_user"] == 1
    assert report["skipped"] == {"no_user": ["n1"], "support_not_readable": ["s1"]}
    assert s["answer_rate"] == 1.0 and s["refusal_recall"] == 1.0 and s["refusal_precision"] == 1.0
    assert s["citation_precision"] == 1.0 and s["faithfulness"] == 1.0 and s["key_fact_coverage"] == 0.5
    assert s["uncited_answer_rate"] == 0.5 and s["fallback_rate"] == 0.0 and s["errors"] == 0
    assert s["tool_calls_mean"] == 0.33 and s["model_calls_mean"] == 1.33
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["a1"]["tool_calls"] == ["search_documents"] and by_id["a1"]["citations"] == [1]
    assert by_id["a1"]["sources"][0]["file"] == "OPERA.pdf" and by_id["u1"]["status"] == "not_found"
    # The judge got the cited passage's text, not just its title.
    assert "Parking is in lot B for registered guests" in judge.prompts[0]
    # No tool that drafts a change was ever offered, and the model was told it's an ordinary user.
    assert model.offered and all(not (offered & WRITE_TOOLS) for offered in model.offered)
    assert all("only create or change files in alice's Agents folder" in system for system in model.systems)
    assert conn.execute("SELECT count(*) FROM agent_proposals").fetchone()[0] == 0
    assert conn.execute("SELECT conversation_id FROM agent_calls").fetchone()[0] == "eval:a1"


def test_every_question_as_one_user(conn, settings, embedder, world):  # noqa: F811
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    agent, _ = _agent(settings, embedder, ScriptedModel())
    items = [GoldItem("a1", "where is parking", support=[Support("OPERA.pdf", pages=[3])], as_user="alice")]
    report = asyncio.run(evaluate_agent(conn, agent, None, items, username="bob", progress=lambda *_: None))
    assert report["summary"]["items"] == 0 and report["skipped"]["support_not_readable"] == ["a1"]
    report = asyncio.run(evaluate_agent(conn, agent, None, items, username="alice", progress=lambda *_: None))
    assert report["items"][0]["user"] == "alice" and report["items"][0]["faithful"] is None   # no judge


def test_model_down_counts_as_a_fallback(conn, settings, embedder, world):  # noqa: F811
    model = ScriptedModel()
    model.fail = True
    agent, _ = _agent(settings, embedder, model)
    items = [GoldItem("a1", "where is parking", support=[Support("OPERA.pdf", pages=[3])], as_user="alice")]
    s = asyncio.run(evaluate_agent(conn, agent, Judge(), items, progress=lambda *_: None))["summary"]
    assert s["fallback_rate"] == 1.0 and s["answer_rate"] == 0.0 and s["tool_calls_mean"] is None


def test_refusal_wording():
    for text in ("I couldn't find that in your documents.", "That isn't covered by your files.",
                 "Your documents don't cover the wifi password.", "I found nothing about it."):
        assert NOT_FOUND_RE.search(text), text
    assert not NOT_FOUND_RE.search("Parking is in lot B.")


def test_gate_compares_agent_reports_only_with_agent_reports():
    def report(path=None, **s):
        base = {"answer_rate": .8, "refusal_recall": .7, "refusal_precision": .8, "citation_precision": .9,
                "faithfulness": .85, "key_fact_coverage": .6, "total_p50_ms": 4000}
        if path:
            base.update(path=path, fallback_rate=0.0, tool_calls_mean=1.5)
        else:
            base["valid_json"] = 1.0
        return {"summary": {**base, **s}, "config": {"prompt_version": "v1"}}

    ok, lines = compare(report("agent"), report("agent", tool_calls_mean=2.0))
    assert ok and any("tool_calls_mean" in line and "(info)" in line for line in lines)   # no JSON needed
    assert not compare(report("agent"), report("agent", faithfulness=.8))[0]
    ok, lines = compare(report(), report("agent"))
    assert not ok and "different paths" in lines[0]
