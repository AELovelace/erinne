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
    with pytest.raises(apis.ApiError, match="GET operations have no body"):
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
    _upstream(lambda r: httpx.Response(404, json={"Message": "Technician with email technician@example.com not found"}))
    failed = apis.call(conn, settings, op_id, {}, user_id=None, via="test")
    assert failed.error == "the API answered HTTP 404: Technician with email technician@example.com not found"
    assert "technician@example.com" in conn.execute("SELECT response_excerpt FROM api_calls WHERE id = ?",
                                                    (failed.call_id,)).fetchone()[0]
    _upstream(lambda r: httpx.Response(400, json={"error": {"code": 12, "message": "TicketId is invalid"}}))
    assert apis.call(conn, settings, op_id, {}, user_id=None, via="test").error.endswith(": TicketId is invalid")
    _upstream(lambda r: httpx.Response(500, text="Object reference not set to an instance of an object."))
    assert "Object reference" in apis.call(conn, settings, op_id, {}, user_id=None, via="test").error
    _upstream(lambda r: httpx.Response(502, text="<html><body>Bad gateway</body></html>"))
    assert apis.call(conn, settings, op_id, {}, user_id=None, via="test").error == "the API answered HTTP 502"
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


def test_digest_keeps_a_list_whole_when_it_fits():
    rows = [{"account": 900000 + n, "value": n} for n in range(351)]
    assert json.loads(apis.digest({"rows": rows}, 40_000)) == {"rows": rows}


def test_pages_walk_the_longest_list():
    data = {"columns": ["account", "value"], "rows": [[900000 + n, "x" * 40] for n in range(351)]}
    text, info = apis.page(data, 0, 4000)
    first = json.loads(text)
    assert first["columns"] == ["account", "value"] and first["rows"][0][0] == 900000
    assert info["list"] == "rows" and info["total"] == 351 and info["offset"] == 0
    assert info["next_offset"] == info["returned"] == len(first["rows"]) < 351
    seen, offset = [], 0
    while offset is not None:
        text, info = apis.page(data, offset, 4000)
        seen += json.loads(text)["rows"]
        offset = info.get("next_offset")
    assert seen == data["rows"]
    assert apis.page({"status": "ok"}, 0, 4000) == ('{"status":"ok"}', None)
    assert apis.page([1, 2, 3], 2, 4000) == ("[3]", {"list": None, "total": 3, "offset": 2, "returned": 1})


def test_find_keeps_only_the_rows_that_mention_a_phrase():
    data = {"page": 1, "items": [{"TicketID": 1, "TicketTitle": "Internet outage at Comfort Inn Kennewick Richland"},
                                 {"TicketID": 2, "TicketTitle": "Printer jam", "CustomerName": "Red Lion Pasco"},
                                 {"TicketID": 3, "TicketTitle": "VPN", "LastTechnicianComment": "called kennewick"}]}
    text, info = apis.page(data, 0, 4000, find=" Kennewick ")
    assert json.loads(text)["items"] == [data["items"][0], data["items"][2]]        # any field, any case
    assert info == {"list": "items", "total": 2, "offset": 0, "returned": 2, "matched_of": 3}
    text, info = apis.page(data, 0, 4000, find="nothing like it")
    assert json.loads(text)["items"] == [] and info["total"] == 0 and info["matched_of"] == 3
    assert apis.page(data, 0, 4000, find="  ")[1]["total"] == 3                   # blank: no filter


def test_claude_searches_an_answer_by_keyword(conn, settings, directory):
    _api(conn, settings, claude=True)
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    rows = [{"account_number": str(900000 + n), "label": "Kennewick" if n % 100 == 0 else "x"} for n in range(351)]
    _upstream(lambda r: httpx.Response(200, json={"columns": ["account_number", "label"], "rows": rows}))
    tools = Tools(settings, lambda: None, lambda: directory)
    out, _ = tools.query_api(conn, Caller(ivan, "ivan", 1), "Hotel Stats", "Stats summary", {"days": 2},
                             find="kennewick")
    assert [r["account_number"] for r in json.loads(out["data"])["rows"]] == ["900000", "900100", "900200", "900300"]
    assert out["rows"]["total"] == 4 and out["rows"]["matched_of"] == 351 and "next_offset" not in out["rows"]
    assert "4 of the 351 rows" in out["note"] and "other pages" in out["note"]


def test_claude_pages_through_a_long_answer(conn, settings, directory):
    _api(conn, settings, claude=True)
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    rows = [{"account_number": str(900000 + n), "value": n, "label": "x" * 150} for n in range(351)]
    seen = _upstream(lambda r: httpx.Response(200, json={"columns": ["account_number", "value"], "rows": rows}))
    tools = Tools(settings, lambda: None, lambda: directory)
    caller = Caller(ivan, "ivan", 1)
    got, offset = [], 0
    while True:
        out, _ = tools.query_api(conn, caller, "Hotel Stats", "Stats summary", {"days": 2}, offset=offset)
        got += json.loads(out["data"])["rows"]
        if "next_offset" not in out["rows"]:
            break
        assert f"offset={out['rows']['next_offset']}" in out["note"]
        offset = out["rows"]["next_offset"]
    assert got == rows and out["rows"]["total"] == 351 and offset > 0
    assert len(seen) == 1                                   # later pages come from the cache


def test_a_search_that_finds_nothing_points_at_a_matching_api(conn, settings, directory):
    from fakes import KeywordEmbedder

    embedder = KeywordEmbedder()
    _, op_id = _api(conn, settings, claude=True)
    conn.execute("UPDATE api_operations SET example_questions = 'How many late checkouts were there?'")
    apis.index_examples(conn, embedder, op_id)
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    tools = Tools(settings, lambda: embedder, lambda: directory)
    out, _ = tools.search_documents(conn, Caller(ivan, "ivan", 1), "late checkout report")
    assert out["found"] is False and out["try_api"] == {"api": "Hotel Stats", "operation": "Stats summary"}
    assert "try query_api" in out["note"]
    out, _ = tools.search_documents(conn, Caller(ivan, "ivan", 1), "secret")
    assert out["found"] is False and "try_api" not in out


def test_a_cut_off_answer_says_so(conn, settings, directory):
    _api(conn, settings, claude=True)
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    _upstream(lambda r: httpx.Response(200, content=b'{"rows": [' + b'"x",' * apis.MAX_RESPONSE_BYTES))
    out, _ = Tools(settings, lambda: None, lambda: directory).query_api(conn, Caller(ivan, "ivan", 1),
                                                                        "Hotel Stats", "Stats summary")
    assert "over 256 KB" in out["note"]


def test_one_at_a_time_apis_queue_their_calls(conn, settings, monkeypatch):
    import threading
    import time as clock

    from mnemerynne import db

    monkeypatch.setattr(apis, "RETRY_429_AFTER", 0)
    _, op_id = _api(conn, settings)
    running, most, lock = [0], [0], threading.Lock()

    def slow(request):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        clock.sleep(0.3)
        with lock:
            running[0] -= 1
        return httpx.Response(200, json={"ok": True})

    def both():
        most[0] = 0
        start, results = threading.Barrier(2), []

        def one(days):
            c = db.connect(settings.db_path)
            start.wait()
            results.append(apis.call(c, settings, op_id, {"days": days}, user_id=None, via="test"))
            c.close()

        threads = [threading.Thread(target=one, args=(d,)) for d in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    _upstream(slow)
    assert all(r.ok for r in both()) and most[0] == 2      # side by side by default
    conn.execute("UPDATE apis SET one_at_a_time = 1")
    assert all(r.ok for r in both()) and most[0] == 1      # one after the other


def test_a_429_is_tried_once_more(conn, settings, monkeypatch):
    monkeypatch.setattr(apis, "RETRY_429_AFTER", 0)
    _, op_id = _api(conn, settings)
    answers = iter([httpx.Response(429, json={"error": "Another query is still running"}),
                    httpx.Response(200, json={"ok": True})])
    seen = _upstream(lambda r: next(answers))
    assert apis.call(conn, settings, op_id, {}, user_id=None, via="test").ok and len(seen) == 2
    _upstream(lambda r: httpx.Response(429, json={"error": "Another query is still running"}))
    failed = apis.call(conn, settings, op_id, {"days": 3}, user_id=None, via="test")
    assert "HTTP 429: Another query is still running" in failed.error


def test_list_apis_says_which_run_one_at_a_time(conn, settings, directory):
    _api(conn, settings, claude=True)
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    tools = Tools(settings, lambda: None, lambda: directory)
    assert "one_at_a_time" not in tools.list_apis(conn, Caller(ivan, "ivan", 1))[0]["apis"][0]
    conn.execute("UPDATE apis SET one_at_a_time = 1")
    assert tools.list_apis(conn, Caller(ivan, "ivan", 1))[0]["apis"][0]["one_at_a_time"] is True


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
    assert [o["name"] for o in detail["operations"]] == [
        "Tickets", "Ticket", "Ticket comments", "Ticket statuses", "Tickets by status", "Customer's tickets",
        "Customers", "Tickets modified since", "Alerts", "Alert"]
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


# A read-only SQL service like the ones api-templates/api-package-guide.md describes.
REPORTS = json.dumps({
    "format": "mnemerynne-api-template", "name": "Hotel Reports", "base_url": "https://reports.test",
    "auth_type": "bearer", "description": "Night-audit reports through a read-only SQL endpoint.",
    "operations": [
        {"name": "Parse failures", "method": "POST", "path": "/api/sql",
         "body": {"sql": "SELECT * FROM failures WHERE at >= datetime('now', ?)", "params": ["-{{days}} days"]},
         "params": [{"name": "days", "type": "int", "in": "body", "min": 1, "max": 30, "default": 3}],
         "example_questions": ["Did any reports fail to parse?", "Which report files failed this week?"]},
        {"name": "Recent polls", "method": "POST", "path": "/api/sql",
         "body": '{"sql": "SELECT * FROM polls ORDER BY at DESC LIMIT ?", "params": [{{count}}]}',
         "params": [{"name": "count", "type": "int", "in": "body", "min": 1, "max": 50, "default": 10}],
         "example_questions": ["When was the mailbox last checked?", "Show the recent mailbox polls."]},
        {"name": "Day's statistics", "method": "POST", "path": "/api/sql",
         "body": {"sql": "SELECT * FROM stats WHERE hotel = ? AND day = ?", "params": ["{{hotel}}", "{{day}}"],
                  "maxRows": 500},
         "params": [{"name": "hotel", "type": "enum", "in": "body", "required": True, "options": ["north", "south"]},
                    {"name": "day", "type": "date", "in": "body", "required": True}],
         "example_questions": ["What were yesterday's statistics for North?", "Show South's stats for Oct 21."]},
    ]})


def test_admin_uploads_a_template_and_makes_an_api_from_it(admin_client, conn):
    up = admin_client.post("/api/admin/apis/templates", json={"template": REPORTS})
    assert up.status_code == 201 and up.json()["operations"] == 3 and not up.json()["replaced"]
    listed = admin_client.get("/api/admin/apis").json()["presets"]
    assert {"key": up.json()["key"], "name": "Hotel Reports", "uploaded": True} in [
        {k: p[k] for k in ("key", "name", "uploaded")} for p in listed]

    made = admin_client.post("/api/admin/apis", json={"preset": up.json()["key"]}).json()
    detail = admin_client.get(f"/api/admin/apis/{made['id']}").json()
    assert detail["base_url"] == "https://reports.test" and detail["auth_type"] == "bearer"
    assert detail["credential"] == "missing" and detail["ad_group"] == "" and not detail["claude_enabled"]
    ops = {o["name"]: o["id"] for o in detail["operations"]}
    assert len(ops) == 3 and conn.execute("SELECT count(*) FROM api_examples").fetchone()[0] == 6

    admin_client.put(f"/api/admin/apis/{made['id']}/credential", json={"secret": "rep_x"})
    seen = _upstream(lambda r: httpx.Response(200, json={"columns": [], "rows": []}))
    assert admin_client.post(f"/api/admin/apis/operations/{ops['Parse failures']}/test",
                             json={"params": {}}).json()["ok"]
    assert admin_client.post(f"/api/admin/apis/operations/{ops['Recent polls']}/test",
                             json={"params": {"count": 5}}).json()["ok"]
    assert admin_client.post(f"/api/admin/apis/operations/{ops["Day's statistics"]}/test",
                             json={"params": {"hotel": "North", "day": "2026-10-21"}}).json()["ok"]
    sent = [json.loads(r.content) for r in seen]
    assert seen[0].url == "https://reports.test/api/sql"
    assert seen[0].headers["Authorization"] == "Bearer rep_x"
    assert sent[0]["params"] == ["-3 days"] and sent[1]["params"] == [5]
    assert sent[2]["params"] == ["north", "2026-10-21"] and sent[2]["maxRows"] == 500
    refused = admin_client.post(f"/api/admin/apis/operations/{ops["Day's statistics"]}/test",
                                json={"params": {"hotel": "x'; DROP TABLE stats;--", "day": "2026-10-21"}})
    assert refused.status_code == 422 and len(seen) == 3

    # Uploading again replaces it; removing it leaves the API alone.
    again = admin_client.post("/api/admin/apis/templates", json={"template": REPORTS}).json()
    assert again["replaced"] and again["key"] == up.json()["key"]
    template_id = int(up.json()["key"].removeprefix("upload-"))
    assert admin_client.delete(f"/api/admin/apis/templates/{template_id}").json() == {"status": "deleted"}
    assert not [p for p in admin_client.get("/api/admin/apis").json()["presets"] if p["uploaded"]]
    assert len(admin_client.get(f"/api/admin/apis/{made['id']}").json()["operations"]) == 3


def _template(**changes):
    t = {"format": "mnemerynne-api-template", "name": "Stats", "base_url": "https://stats.test",
         "operations": [{"name": "Summary", "path": "/summary"}]}
    t.update(changes)
    return json.dumps(t)


@pytest.mark.parametrize("text, message", [
    ("not json", "isn't JSON"),
    (json.dumps({"name": "Stats"}), '"format"'),
    (_template(secret="tok"), "can't hold “secret”"),
    (_template(ad_group="IT"), "can't hold “ad_group”"),
    (_template(claude_enabled=True), "can't hold “claude_enabled”"),
    (_template(base_url="ftp://x"), "base_url"),
    (_template(operations=[]), "1 to 50 operations"),
    (_template(operations=[{"name": "A", "path": "/../admin"}]), "stay under"),
    (_template(operations=[{"name": "A", "path": "/a", "method": "POST", "body": {"q": "{{x}}"}}]), "{{x}}"),
    (_template(operations=[{"name": "A", "path": "/a"}, {"name": "a", "path": "/b"}]), "name of its own"),
    (_template(operations=[{"name": "A", "path": "/a", "headers": {}}]), "unknown field"),
])
def test_bad_templates_are_refused(admin_client, text, message):
    r = admin_client.post("/api/admin/apis/templates", json={"template": text})
    assert r.status_code == 422 and message in r.json()["detail"]


def test_one_at_a_time_comes_from_a_template_or_the_admin(admin_client, conn):
    up = admin_client.post("/api/admin/apis/templates", json={"template": _template(one_at_a_time=True)}).json()
    made = admin_client.post("/api/admin/apis", json={"preset": up["key"]}).json()
    assert admin_client.get(f"/api/admin/apis/{made['id']}").json()["one_at_a_time"] is True
    assert admin_client.patch(f"/api/admin/apis/{made['id']}", json={"one_at_a_time": False}).json() == {
        "status": "saved"}
    assert conn.execute("SELECT one_at_a_time FROM apis WHERE id = ?", (made["id"],)).fetchone()[0] == 0


def test_a_template_cant_take_a_built_in_preset_name(admin_client):
    r = admin_client.post("/api/admin/apis/templates", json={"template": _template(name="wazuh")})
    assert r.status_code == 409


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
