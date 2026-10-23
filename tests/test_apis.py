import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, apis, users
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.mcp_server import Tools, ToolError
from mnemerynne.web.app import Services, create_app


@pytest.fixture(autouse=True)
def _fresh_cache():
    apis.clear_cache()
    yield
    apis.TRANSPORT = None
    apis.clear_cache()


@pytest.fixture
def directory():
    return FakeDirectory({"ivan": {"IT", "Domain Users"}, "sam": {"Domain Users"}, "boss": {"Domain Admins"}},
                         groups=["Domain Admins", "Domain Users", "IT"])


def _upstream(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    apis.TRANSPORT = httpx.MockTransport(wrapped)
    return seen


def _api(conn, settings, *, auth="bearer", group="IT", claude=False, header="", user="", secret="s3cret"):
    api_id = conn.execute(
        "INSERT INTO apis(name, base_url, auth_type, auth_header, auth_user, ad_group, claude_enabled, secret_enc) "
        "VALUES ('Hotel Stats', 'https://stats.test/api', ?, ?, ?, ?, ?, ?)",
        (auth, header, user, group, int(claude), apis.encrypt_secret(settings, secret) if secret else None)).lastrowid
    params = [{"name": "property", "type": "string", "in": "query", "default": "all"},
              {"name": "days", "type": "int", "in": "query", "min": 1, "max": 31, "default": 7}]
    op_id = conn.execute(
        "INSERT INTO api_operations(api_id, name, path, params, example_questions) VALUES (?, 'Stats summary', "
        "'/stats/summary', ?, 'What was occupancy last week?')", (api_id, json.dumps(params))).lastrowid
    return api_id, op_id


# ------------------------------------------------------------------ secrets
def test_secrets_are_encrypted_and_tied_to_the_secret_key(settings):
    blob = apis.encrypt_secret(settings, "tok_abc")
    assert b"tok_abc" not in blob and apis.decrypt_secret(settings, blob) == "tok_abc"
    other = Settings(_env_file=None, data_dir=settings.data_dir, secret_key="another-key", embed_dim=4)
    assert apis.decrypt_secret(other, blob) is None                 # key changed: re-enter it
    with pytest.raises(apis.ApiError, match="MNEMERYNNE_SECRET_KEY"):
        apis.encrypt_secret(Settings(_env_file=None, data_dir=settings.data_dir, embed_dim=4), "x")


# --------------------------------------------------------------- parameters
def test_parameters_are_checked_before_any_request():
    specs = [{"name": "days", "type": "int", "min": 1, "max": 31, "default": 7},
             {"name": "when", "type": "date"},
             {"name": "status", "type": "enum", "options": ["Open", "Closed"]},
             {"name": "id", "type": "int", "in": "path", "required": True}]
    assert apis.validate_params(specs, {"status": "open", "id": "12"}) == {"days": 7, "status": "Open", "id": 12}
    for bad in ({"id": 1, "days": 40}, {"id": 1, "when": "next week"}, {"id": 1, "status": "Lost"},
                {"days": 2}, {"id": 1, "evil": "x"}):
        with pytest.raises(apis.ApiError):
            apis.validate_params(specs, bad)
    with pytest.raises(apis.ApiError):
        apis.validate_params([{"name": "slug", "type": "string", "in": "path"}], {"slug": "../../admin"})


def test_operations_that_could_escape_are_refused():
    with pytest.raises(apis.ApiError, match="no path parameter"):
        apis.check_specs("/tickets/{id}", [], "GET", "")
    with pytest.raises(apis.ApiError, match="start with /"):
        apis.check_specs("//evil.example/x", [], "GET", "")
    with pytest.raises(apis.ApiError, match="Only POST"):
        apis.check_specs("/x", [], "GET", '{"a": 1}')
    with pytest.raises(apis.ApiError, match="needs a JSON body"):
        apis.check_specs("/x", [], "POST", "not json")


# ------------------------------------------------------------------ calling
def test_calls_send_the_credential_and_are_recorded_and_cached(conn, settings):
    api_id, op_id = _api(conn, settings)
    seen = _upstream(lambda r: httpx.Response(200, json={"totals": {"occupancyPct": 81.5}}))
    first = apis.call(conn, settings, op_id, {"days": "3"}, user_id=None, via="chat")
    assert first.ok and first.data == {"totals": {"occupancyPct": 81.5}}
    request = seen[0]
    assert request.headers["authorization"] == "Bearer s3cret"
    assert request.url.path == "/api/stats/summary"
    assert dict(request.url.params) == {"property": "all", "days": "3"}
    second = apis.call(conn, settings, op_id, {"days": 3}, user_id=None, via="chat")
    assert second.cached and len(seen) == 1
    rows = conn.execute("SELECT ok, cached, params FROM api_calls ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 0, '{"property": "all", "days": 3}'), (1, 1, '{"property": "all", "days": 3}')]


@pytest.mark.parametrize("auth, header, user, expected", [
    ("header", "X-API-Key", "", ("x-api-key", "s3cret")),
    ("basic", "", "reader", ("authorization", "Basic " + base64.b64encode(b"reader:s3cret").decode())),
])
def test_other_credential_kinds(conn, settings, auth, header, user, expected):
    _, op_id = _api(conn, settings, auth=auth, header=header, user=user)
    seen = _upstream(lambda r: httpx.Response(200, json={}))
    apis.call(conn, settings, op_id, {}, user_id=None, via="test")
    assert seen[0].headers[expected[0]] == expected[1]


def test_failures_explain_themselves_and_redirects_are_not_followed(conn, settings):
    _, op_id = _api(conn, settings)
    _upstream(lambda r: httpx.Response(401, json={"error": "revoked"}))
    assert "refused the credential" in apis.call(conn, settings, op_id, {}, user_id=None, via="test").error
    _upstream(lambda r: httpx.Response(302, headers={"location": "https://evil.example/"}))
    assert "redirect" in apis.call(conn, settings, op_id, {}, user_id=None, via="test").error
    _upstream(lambda r: httpx.Response(200, content=b"x" * (apis.MAX_RESPONSE_BYTES + 10)))
    big = apis.call(conn, settings, op_id, {}, user_id=None, via="test")
    assert big.ok and big.truncated and len(big.data) <= apis.MAX_RESPONSE_BYTES


def test_post_sends_only_the_fixed_body(conn, settings):
    api_id, _ = _api(conn, settings)
    body = '{"size": 0}'
    op_id = conn.execute("INSERT INTO api_operations(api_id, name, method, path, body) "
                         "VALUES (?, 'Alerts', 'POST', '/alerts/_search', ?)", (api_id, body)).lastrowid
    seen = _upstream(lambda r: httpx.Response(200, json={"aggregations": {}}))
    apis.call(conn, settings, op_id, {}, user_id=None, via="test")
    assert seen[0].method == "POST" and seen[0].content == body.encode()


def test_digest_trims_long_lists():
    data = {"tickets": [{"id": n, "title": "x" * 50} for n in range(500)]}
    text = apis.digest(data, 2000)
    assert len(text) <= 2001 and "(showing" in text and "of 500)" in text
    assert ("tickets[].id", "number") in apis.infer_shape(data)


# ------------------------------------------------------------------- access
def test_operations_follow_the_ad_group_and_claude_switch(conn, settings, directory):
    _api(conn, settings, group="IT", claude=False)
    ivan = users.get_user_by_id(conn, users.create_user(conn, "ivan", "password123"))
    accounts.link(conn, ivan["id"], "ivan", directory)
    ivan = users.get_user_by_id(conn, ivan["id"])
    sam = users.create_user(conn, "sam", "password123")
    accounts.link(conn, sam, "sam", directory)
    assert [o["name"] for o in apis.allowed_operations(conn, ivan, directory)] == ["Stats summary"]
    assert apis.allowed_operations(conn, users.get_user_by_id(conn, sam), directory) == []
    assert apis.allowed_operations(conn, ivan, directory, for_claude=True) == []

    tools = Tools(settings, lambda: None, lambda: directory)
    with pytest.raises(ToolError):
        tools.query_api(conn, Caller(ivan["id"], "ivan", 1), "Hotel Stats", "Stats summary")
    conn.execute("UPDATE apis SET claude_enabled = 1")
    listed, _ = tools.list_apis(conn, Caller(ivan["id"], "ivan", 1))
    assert listed["apis"][0]["operations"][0]["operation"] == "Stats summary"
    _upstream(lambda r: httpx.Response(200, json={"occupancyPct": 80}))
    out, _ = tools.query_api(conn, Caller(ivan["id"], "ivan", 1), "hotel stats", "stats summary", {"days": 2})
    assert out["citation"].startswith("Hotel Stats · Stats summary (live,") and '"occupancyPct":80' in out["data"]


# ------------------------------------------------------------------ admin page
@pytest.fixture
def admin_client(settings, conn, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        uid = users.create_user(conn, "boss", "password123")
        accounts.link(conn, uid, "boss", directory)
        c.post("/login", data={"username": "boss", "password": "password123"})
        yield c


def test_admin_sets_up_an_api_from_a_preset(admin_client, conn):
    made = admin_client.post("/api/admin/apis", json={"preset": "atera"}).json()
    detail = admin_client.get(f"/api/admin/apis/{made['id']}").json()
    assert [o["name"] for o in detail["operations"]] == ["Tickets", "Ticket", "Ticket comments"]
    assert detail["credential"] == "missing"
    assert conn.execute("SELECT count(*) FROM api_examples").fetchone()[0] > 0   # indexed for routing

    assert admin_client.put(f"/api/admin/apis/{made['id']}/credential", json={"secret": "tok_x"}).json() == {
        "credential": "set"}
    listed = admin_client.get("/api/admin/apis").text + admin_client.get(f"/api/admin/apis/{made['id']}").text
    assert "tok_x" not in listed                                              # write-only
    assert admin_client.patch(f"/api/admin/apis/{made['id']}", json={"ad_group": "Nope"}).status_code == 422
    assert admin_client.patch(f"/api/admin/apis/{made['id']}", json={"ad_group": "ETERNALHOTELS\\IT"}).json() == {
        "status": "saved"}
    assert admin_client.get("/api/admin/log").json()["entries"][0]["action"] == "api.group"

    tickets = detail["operations"][0]["id"]
    _upstream(lambda r: httpx.Response(200, json={"status": "ok", "sites": {"total": 12, "missing": 0}}))
    tested = admin_client.post(f"/api/admin/apis/operations/{tickets}/test", json={"params": {}}).json()
    assert tested["ok"] and {"path": "sites.total", "type": "number"} in tested["fields"]


def test_admin_api_pages_are_admin_only(settings, conn, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        users.create_user(conn, "sam", "password123")
        c.post("/login", data={"username": "sam", "password": "password123"})
        assert c.get("/api/admin/apis").status_code == 403


# --------------------------------------------------------------------- chat
class ApiLLM:
    """Fills parameters, then answers from source [1]."""

    def __init__(self):
        self.filled = []

    async def chat_json(self, messages, schema, **kwargs):
        self.filled.append(messages[-1]["content"])
        return {"property": None, "days": 7}

    async def stream(self, messages, **kwargs):
        if kwargs.get("json_schema") is None:
            yield "Sorry!"
            return
        yield json.dumps({"status": "answered", "answer": "Occupancy was 81.5% [1].", "citations": [1]})

    async def aclose(self):
        pass


def _ask(client, question):
    with client.stream("POST", "/api/ask", json={"question": question}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    return next(e for e in events if e["type"] == "final"), events


def test_chat_answers_from_an_api_for_people_in_its_group(settings, conn, directory):
    _, op_id = _api(conn, settings)
    apis.index_examples(conn, FakeEmbedder(), op_id)
    llm = ApiLLM()
    with TestClient(create_app(settings, Services(llm=llm, embedder=FakeEmbedder(), directory=directory))) as c:
        ivan = users.create_user(conn, "ivan", "password123")
        accounts.link(conn, ivan, "ivan", directory)
        c.post("/login", data={"username": "ivan", "password": "password123"})
        _upstream(lambda r: httpx.Response(200, json={"totals": {"occupancyPct": 81.5}}))
        final, events = _ask(c, "What was occupancy last week?")
        assert final["route"] == "api" and final["status"] == "answered"
        source = final["sources"][0]
        assert source["cited"] and source["url"].startswith("/apis/calls/") and source["file"] == "Hotel Stats"
        assert "What was occupancy last week?" in llm.filled[0]
        assert "81.5" in c.get(source["url"]).text                       # the response behind the citation
        conv = c.get(f"/api/conversations/{events[0]['conversation_id']}").json()
        assert conv["messages"][1]["sources"][0]["url"] == source["url"]

        # Someone outside the group gets ordinary document search, not the API.
        sam = users.create_user(conn, "sam", "password123")
        accounts.link(conn, sam, "sam", directory)
        c.post("/logout")
        c.post("/login", data={"username": "sam", "password": "password123"})
        final, _ = _ask(c, "What was occupancy last week?")
        assert final["route"] != "api"
        assert c.get(source["url"]).status_code == 404                  # not their call


def test_chat_explains_an_api_failure(settings, conn, directory):
    _, op_id = _api(conn, settings)
    apis.index_examples(conn, FakeEmbedder(), op_id)
    with TestClient(create_app(settings, Services(llm=ApiLLM(), embedder=FakeEmbedder(), directory=directory))) as c:
        ivan = users.create_user(conn, "ivan", "password123")
        accounts.link(conn, ivan, "ivan", directory)
        c.post("/login", data={"username": "ivan", "password": "password123"})
        _upstream(lambda r: httpx.Response(403, json={"error": "wrong scope"}))
        final, _ = _ask(c, "What was occupancy last week?")
        assert final["status"] == "not_found" and "refused the credential" in final["answer"]
