import asyncio
import io
import json

import httpx
import pytest

from mnemerynne import permissions, users, worker
from mnemerynne.clients.llm import LLM, strict_schema
from mnemerynne.ingest import store
from mnemerynne.sft import generate
from mnemerynne.teacher import TeacherScopeError, allowed_collections
from fakes import KeywordEmbedder
from pdfgen import make_pdf
from test_sft import FakeTeacher


def test_strict_schema_closes_objects_and_drops_unsupported_keywords():
    schema = {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "inner": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    out = strict_schema(schema)
    assert out["additionalProperties"] is False and out["required"] == ["facts", "inner"]
    assert "maxItems" not in out["properties"]["facts"]
    assert out["properties"]["inner"]["additionalProperties"] is False


def _mock_llm(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer sk-test"})
    return LLM("https://api.example", model="gpt-6-luna", flavor="openai", api_key="sk-test", client=client, **kw)


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_openai_request_shape_and_rejected_parameter_fallback():
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer sk-test"
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'"}})
        return _ok('{"question": "ok"}')

    llm = _mock_llm(handler)
    out = asyncio.run(llm.chat_json([{"role": "user", "content": "hi"}],
                                    {"type": "object", "properties": {"question": {"type": "string"}}}))
    assert out == {"question": "ok"}
    first, second = seen
    assert "chat_template_kwargs" not in first and "max_tokens" not in first
    assert first["max_completion_tokens"] > 0 and first["reasoning_effort"] == "low"
    fmt = first["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
    assert "temperature" not in second and second["reasoning_effort"] == "low"
    # Remembered: the next call goes out without it, first time.
    asyncio.run(llm.chat_json([{"role": "user", "content": "again"}],
                              {"type": "object", "properties": {"question": {"type": "string"}}}))
    assert len(seen) == 3 and "temperature" not in seen[2]


def test_streaming_drops_rejected_parameter_then_streams():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'"}})
        sse = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": part}}]})}\n\n'
                      for part in ['{"status":', '"answered"}']) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    async def collect():
        return "".join([d async for d in _mock_llm(handler).stream([{"role": "user", "content": "q"}])])

    assert asyncio.run(collect()) == '{"status":"answered"}'
    assert len(bodies) == 2 and "temperature" not in bodies[1] and bodies[1]["stream"] is True


def test_usage_is_tracked_and_budget_stops_calls():
    from mnemerynne.clients.llm import BudgetExceeded

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 200_000}})

    llm = _mock_llm(handler)
    llm.price_in_per_m, llm.price_out_per_m, llm.budget_usd = 0.10, 0.50, 0.25
    asyncio.run(llm.chat([{"role": "user", "content": "x"}]))
    assert (llm.tokens_in, llm.tokens_out) == (1_000_000, 200_000) and round(llm.spent_usd, 2) == 0.20
    asyncio.run(llm.chat([{"role": "user", "content": "x"}]))     # 0.20 < 0.25: still allowed
    with pytest.raises(BudgetExceeded):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}]))  # 0.40 >= 0.25: refused, not sent


def test_rate_limit_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return _ok("fine")

    assert asyncio.run(_mock_llm(handler).chat([{"role": "user", "content": "x"}])) == "fine"
    assert len(calls) == 2


def test_local_teacher_keeps_llamacpp_payload():
    llm = LLM("http://127.0.0.1:8081")
    payload = llm._payload([], 0.2, 100, {"type": "object", "properties": {}}, False, False)
    assert payload["max_tokens"] == 100 and "chat_template_kwargs" in payload
    assert "strict" not in payload["response_format"]["json_schema"] and not llm.is_remote


def test_teacher_factory_requires_key(settings):
    settings.teacher_flavor = "openai"
    with pytest.raises(ValueError, match="API_KEY"):
        LLM.teacher(settings)
    settings.teacher_api_key = "sk-x"
    t = LLM.teacher(settings)
    assert t.is_remote and t.model == "gpt-6-luna" and t.url == "https://api.openai.com/v1/chat/completions"


class _Remote:
    is_remote = True


def test_allowed_collections(conn, settings):
    oracle = permissions.create_library(conn, "Oracle")
    permissions.create_library(conn, "BrandResBW")
    assert oracle in allowed_collections(conn, settings, LLM("http://local"))  # local: everything
    with pytest.raises(TeacherScopeError, match="Nothing is sent by default"):
        allowed_collections(conn, settings, _Remote())
    settings.teacher_allowed_libraries = "oracle"
    assert allowed_collections(conn, settings, _Remote()) == {oracle}
    settings.teacher_allowed_libraries = "Oracle, Nope"
    with pytest.raises(TeacherScopeError, match="Nope"):
        allowed_collections(conn, settings, _Remote())
    settings.teacher_allowed_libraries = "*"
    assert allowed_collections(conn, settings, _Remote()) == {r[0] for r in conn.execute("SELECT id FROM collections")}


class RecordingRemoteTeacher(FakeTeacher):
    is_remote = True

    def __init__(self):
        self.sent = []

    async def chat_json(self, messages, schema, **kw):
        self.sent.extend(m["content"] for m in messages)
        return await super().chat_json(messages, schema, **kw)

    async def chat(self, messages, **kw):
        self.sent.extend(m["content"] for m in messages)
        return await super().chat(messages, **kw)


def test_remote_teacher_never_sees_disallowed_library_text(conn, settings, tmp_path):
    embedder = KeywordEmbedder()
    oracle = permissions.create_library(conn, "Oracle")
    bwh = permissions.create_library(conn, "BrandResBW")
    topics = ["audit", "pool", "checkout"]
    oracle_pdf = make_pdf([[f"{t.title()} Rules", f"The {t} rule for staff is written here."] for t in topics],
                          [(f"{t.title()} Rules", i, 0) for i, t in enumerate(topics)])
    bwh_pdf = make_pdf([[f"{t.title()} Policy", f"CONFIDENTIAL-BWH {t} policy text."] for t in topics],
                       [(f"{t.title()} Policy", i, 0) for i, t in enumerate(topics)])
    store.add_file(conn, settings, store.receive(io.BytesIO(oracle_pdf), "opera.pdf", settings), oracle, None)
    store.add_file(conn, settings, store.receive(io.BytesIO(bwh_pdf), "bwh-standards.pdf", settings), bwh, None)
    while worker.run_one(conn, settings, embedder):
        pass
    conn.execute("UPDATE chunks SET tokens = 80")
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    settings.teacher_allowed_libraries = "Oracle"

    teacher = RecordingRemoteTeacher()
    counts = asyncio.run(generate(conn, settings, embedder, teacher, [], tmp_path / "sft.jsonl", n_answer=3,
                                  n_near_miss=2, n_offtopic=1, n_condense=1, n_memory=1, n_general=0,
                                  progress=lambda *_: None))
    assert counts.get("answer")
    sent = "\n".join(teacher.sent)
    assert "The audit rule" in sent or "The pool rule" in sent or "The checkout rule" in sent
    assert "CONFIDENTIAL-BWH" not in sent and "bwh-standards" not in sent

    # A spent budget stops generation cleanly (no exception) with what was written.
    from mnemerynne.clients.llm import BudgetExceeded

    class BrokeTeacher(RecordingRemoteTeacher):
        async def chat_json(self, messages, schema, **kw):
            raise BudgetExceeded("Teacher budget of $1.00 reached (spent $1.00).")

    notes = []
    counts = asyncio.run(generate(conn, settings, embedder, BrokeTeacher(), [], tmp_path / "b.jsonl", n_answer=3,
                                  n_near_miss=1, n_offtopic=1, n_condense=1, n_memory=1, n_general=0,
                                  progress=notes.append))
    assert counts == {} and "budget" in notes[-1]

    settings.teacher_allowed_libraries = ""
    with pytest.raises(TeacherScopeError):
        asyncio.run(generate(conn, settings, embedder, teacher, [], tmp_path / "x.jsonl", n_answer=1,
                             progress=lambda *_: None))
