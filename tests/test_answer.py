import json

import pytest

from erinne.answer import (ANSWER_SCHEMA, NOT_FOUND_TEXT, AnswerStreamer, build_messages, format_source,
                           parse_answer)
from erinne.retrieval import Hit


def _hit(n=1, **kw):
    base = dict(chunk_id=n, file_id=n, collection_id=1, file_name="OPERA.pdf", kind="pdf", page_start=12,
                page_end=13, anchor=None, section_path="Night Audit > Running", text=f"Text {n}.", tokens=3)
    base.update(kw)
    return Hit(**base)


def test_messages_number_sources_and_include_memories():
    msgs = build_messages("How?", [_hit(1), _hit(2, kind="md", file_name="n.md", page_start=None,
                                               page_end=None, anchor="x", section_path="Checkout")],
                          about_user=["Works at the Portland property"])
    assert "Works at the Portland property" in msgs[0]["content"] and "never cite it" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "[1] OPERA.pdf · p. 12-13 · Section: Night Audit > Running\nText 1." in user
    assert "[2] n.md · § Checkout · Section: Checkout" in user
    assert user.endswith("Question: How?")
    assert list(ANSWER_SCHEMA["properties"]) == ["status", "answer", "citations"]


def test_parse_valid_answer_collects_citations_in_order():
    raw = json.dumps({"status": "answered", "answer": "Do X [2]. Then Y [1][2].", "citations": [1, 2]})
    a = parse_answer(raw, 3)
    assert (a.status, a.citations, a.text) == ("answered", [2, 1], "Do X [2]. Then Y [1][2].")


def test_parse_drops_out_of_range_citations():
    raw = json.dumps({"status": "answered", "answer": "Do X [7]. Then Y [1].", "citations": [7, 1]})
    a = parse_answer(raw, 2)
    assert a.citations == [1] and a.text == "Do X. Then Y [1]."


@pytest.mark.parametrize("raw", [
    json.dumps({"status": "answered", "answer": "Confident but uncited.", "citations": []}),
    json.dumps({"status": "answered", "answer": "", "citations": [1]}),
    json.dumps({"status": "not_found", "answer": "Something", "citations": [1]}),
    "not json at all",
    json.dumps({"status": "maybe", "answer": "x [1]", "citations": [1]}),
])
def test_unsupported_or_malformed_become_not_found(raw):
    a = parse_answer(raw, 2)
    assert (a.status, a.text, a.citations) == ("not_found", NOT_FOUND_TEXT, [])


def test_streamer_extracts_answer_text_across_chunk_boundaries():
    payload = json.dumps({"status": "answered", "answer": "Line one [1].\nTwo \"quoted\" café", "citations": [1]})
    s = AnswerStreamer()
    out = "".join(s.feed(payload[i:i + 3]) for i in range(0, len(payload), 3))
    assert out == "Line one [1].\nTwo \"quoted\" café"
    assert s.feed("more") == ""


def test_streamer_handles_unicode_escapes_split_mid_escape():
    payload = '{"status":"answered","answer":"caf\\u00e9 ok","citations":[1]}'
    s = AnswerStreamer()
    out = "".join(s.feed(ch) for ch in payload)
    assert out == "café ok"


def test_format_source_without_section():
    assert format_source(3, _hit(section_path="")) == "[3] OPERA.pdf · p. 12-13\nText 1."
