import json

import pytest

from erinne.evaluation import GoldItem, Support, dump_gold, load_gold
from erinne.evaluation.retrieval_eval import evaluate
from erinne.retrieval import Hit
from test_retrieval import embedder, world  # noqa: F401  (fixtures)


def _hit(**kw):
    base = dict(chunk_id=1, file_id=1, collection_id=1, file_name="OPERA.pdf", kind="pdf", page_start=10,
                page_end=11, anchor=None, section_path="", text="", tokens=1)
    base.update(kw)
    return Hit(**base)


def test_support_matching():
    assert Support("opera.pdf", pages=[11]).matches(_hit())
    assert not Support("OPERA.pdf", pages=[12]).matches(_hit())
    assert Support("OPERA.pdf").matches(_hit())
    assert Support("n.md", anchors=["a"]).matches(_hit(file_name="n.md", kind="md", page_start=None,
                                                         page_end=None, anchor="a"))
    assert not Support("other.pdf").matches(_hit())


def test_gold_roundtrip_and_validation(tmp_path):
    items = [GoldItem("q1", "How?", support=[Support("a.pdf", pages=[1])], as_user="rob"),
             GoldItem("q2", "Wifi?", answerable=False, split="test")]
    path = tmp_path / "g.jsonl"
    dump_gold(items, path)
    assert [i.id for i in load_gold([path])] == ["q1", "q2"]
    assert [i.id for i in load_gold([path], split="test")] == ["q2"]
    assert load_gold([path], verified_only=True) == []

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "x", "question": "q", "answerable": True}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no support"):
        load_gold([bad])
    bad.write_text((json.dumps({"id": "x", "question": "q", "answerable": False}) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_gold([bad])


class _Generator:
    async def stream(self, messages, **kw):
        question = messages[-1]["content"].rsplit("Question:", 1)[1]
        if "parking" in question:  # cite source 1
            yield json.dumps({"status": "answered", "answer": "Lot B [1].", "citations": [1]})
        else:
            yield "not json"


class _Judge:
    async def chat_json(self, messages, schema, **kw):
        return {"unsupported_claims": [], "faithful": True, "facts_covered": [True, False]}


def test_answer_evaluation_metrics(conn, settings, embedder, world):  # noqa: F811
    import asyncio

    from erinne.evaluation.answer_eval import evaluate_answers

    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    items = [
        GoldItem("a1", "where is parking lot B", support=[Support("OPERA.pdf", pages=[3])],
                 key_facts=["Lot B", "registered guests"], as_user="alice"),
        GoldItem("a2", "night audit", support=[Support("OPERA.pdf", pages=[1])], as_user="alice"),
        GoldItem("u1", "wifi password reset", answerable=False, as_user="alice"),
    ]
    report = asyncio.run(evaluate_answers(conn, settings, embedder, _Generator(), _Judge(), items,
                                          progress=lambda *_: None))
    s = report["summary"]
    assert s["answer_rate"] == 0.5          # a2 got invalid JSON -> not_found
    assert s["refusal_recall"] == 1.0       # u1 gated before the model
    assert s["citation_precision"] == 1.0 and s["faithfulness"] == 1.0
    assert s["key_fact_coverage"] == 0.5 and s["valid_json"] == round(2 / 3, 3)


def test_remote_generator_is_restricted_to_allowed_libraries(conn, settings, embedder, world):  # noqa: F811
    import asyncio

    from erinne.evaluation.answer_eval import evaluate_answers, restrict_items

    class RemoteGenerator(_Generator):
        is_remote = True
        prompts = []

        async def stream(self, messages, **kw):
            self.prompts.append(messages[-1]["content"])
            async for chunk in super().stream(messages, **kw):
                yield chunk

    items = [
        GoldItem("a1", "where is parking lot B", support=[Support("OPERA.pdf", pages=[3])], as_user="alice"),
        GoldItem("a2", "secret pool code", support=[Support("alice.md")], as_user="alice"),
        GoldItem("u1", "secret code", answerable=False, as_user="alice"),
    ]
    oracle = world["oracle"]
    assert [i.id for i in restrict_items(conn, items, {oracle})] == ["a1", "u1"]
    with pytest.raises(ValueError, match="restrict_to"):
        asyncio.run(evaluate_answers(conn, settings, embedder, RemoteGenerator(), None, items))
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    report = asyncio.run(evaluate_answers(conn, settings, embedder, RemoteGenerator(), None, items,
                                          progress=lambda *_: None, restrict_to={oracle}))
    assert report["summary"]["items"] == 2
    assert all("4411" not in p for p in RemoteGenerator.prompts)  # alice's private note never sent


def test_deployment_gate():
    from erinne.evaluation.gate import compare

    def report(**s):
        base = {"answer_rate": .8, "refusal_recall": .7, "refusal_precision": .8, "citation_precision": .9,
                "faithfulness": .85, "key_fact_coverage": .6, "valid_json": 1.0, "total_p50_ms": 4000}
        return {"summary": {**base, **s}, "config": {"prompt_version": "v1"}}

    assert compare(report(), report(faithfulness=.9, refusal_recall=.8))[0]
    assert compare(report(), report(answer_rate=.79))[0]          # within tolerance
    assert not compare(report(), report(answer_rate=.7))[0]       # dropped too far
    assert not compare(report(), report(faithfulness=.84))[0]     # faithfulness may not drop at all
    assert not compare(report(), report(valid_json=.95))[0]
    other = report()
    other["config"]["prompt_version"] = "v2"
    assert not compare(report(), other)[0]


def test_evaluate_reports_recall_and_gate(conn, settings, embedder, world):  # noqa: F811
    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    items = [
        GoldItem("a1", "night audit", support=[Support("OPERA.pdf", pages=[1])], as_user="alice"),
        GoldItem("a2", "where is parking lot B", support=[Support("OPERA.pdf", pages=[3])], as_user="alice"),
        GoldItem("u1", "wifi password reset", answerable=False, as_user="alice"),
        GoldItem("u2", "night audit", answerable=False, as_user="bob"),  # bob can't read Oracle
    ]
    report = evaluate(conn, settings, embedder, items)
    s = report["summary"]
    assert s["recall@1"] == 1.0 and s["mrr"] == 1.0 and s["context_recall"] == 1.0
    assert s["gate_rejects_unanswerable"] == 1.0 and s["wrongly_gated"] == 0.0
    assert {i["id"]: i["rank"] for i in report["items"]}["a2"] == 1
