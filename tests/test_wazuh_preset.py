"""The Wazuh expansion of 2026-11-17 (mnemerynne/wazuh_presets.py and the engine features it needs):
list parameters, optional {"$if"} body parts, fixed queries in paths, response shapes with redaction,
sign-in-for-a-token APIs, updating an API to its preset, and JSON (not a JSON string) from query_api."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, apis, call_logs, users, wazuh_presets
from mnemerynne.api_tokens import Caller
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app


@pytest.fixture(autouse=True)
def _fresh_cache():
    apis.clear_cache()                                  # also forgets sign-in tokens
    yield
    apis.TRANSPORT = None
    apis.clear_cache()


@pytest.fixture
def directory():
    return FakeDirectory({"ivan": {"IT", "Domain Users"}, "boss": {"Domain Admins"}},
                         groups=["Domain Admins", "Domain Users", "IT"])


@pytest.fixture
def admin_client(settings, conn, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        uid = users.create_user(conn, "boss", "password123")
        accounts.link(conn, uid, "boss", directory)
        c.post("/login", data={"username": "boss", "password": "password123"})
        yield c


def _upstream(handler):
    """Every request Mnemerynne sends goes to handler; returns the list of requests seen."""
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    apis.TRANSPORT = httpx.MockTransport(wrapped)
    return seen


def _preset_op(key: str, name: str) -> dict:
    preset = next(p for p in apis.PRESETS if p["key"] == key)
    return next(o for o in preset["operations"] if o["name"] == name)


def _add(conn, settings, key: str, names: list[str], *, login: bool = False, secret="pw") -> dict[str, int]:
    """An API made from a preset with some of its operations, in group IT; {operation name: id}."""
    preset = next(p for p in apis.PRESETS if p["key"] == key)
    api_id = conn.execute(
        "INSERT INTO apis(name, base_url, auth_type, auth_user, auth_login_path, ad_group, claude_enabled, secret_enc) "
        "VALUES (?, ?, 'basic', 'reader', ?, 'IT', 1, ?)",
        (preset["name"], preset["base_url"], preset.get("auth_login_path", "") if login else "",
         apis.encrypt_secret(settings, secret))).lastrowid
    ids = {}
    for name in names:
        op = _preset_op(key, name)
        ids[name] = conn.execute(
            "INSERT INTO api_operations(api_id, name, method, path, params, body, shape, action, cache_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (api_id, name, op.get("method", "GET"), op["path"], json.dumps(op.get("params", [])), op.get("body", ""),
             op.get("shape", ""), int(op.get("action", False)), op.get("cache_seconds", 60))).lastrowid
    return ids


# ------------------------------------------------------------- the presets
def test_every_wazuh_operation_passes_the_admin_checks():
    keys = {p["key"] for p in wazuh_presets.PRESETS}
    assert keys == {"wazuh", "wazuh-manager", "wazuh-manager-actions"}
    for preset in wazuh_presets.PRESETS:
        assert ".example" in preset["base_url"]                          # P9: no site's own address ships
        for op in preset["operations"]:
            apis.check_specs(op["path"], op.get("params", []), op.get("method", "GET"), op.get("body", ""),
                             op.get("action", False))
            apis.check_shape(op.get("shape", ""))
    # The read-only manager preset reads only; the changes live in their own preset (own credential).
    assert not any(o.get("action") for o in wazuh_presets.MANAGER["operations"])
    assert all(o.get("action") for o in wazuh_presets.ACTIONS["operations"])


# ------------------------------------------------------- list parameters
def test_list_parameters_are_checked_and_sent_as_lists_or_commas():
    spec = [{"name": "ids", "type": "list", "in": "body"}]
    assert apis.validate_params(spec, {"ids": "204, 31101 ,"}) == {"ids": ["204", "31101"]}
    assert apis.validate_params(spec, {"ids": ["5710"]}) == {"ids": ["5710"]}
    assert apis.validate_params(spec, {"ids": ""}) == {}                  # empty: left out, like any optional
    for bad in ({"ids": ["a\"b"]}, {"ids": [str(i) for i in range(51)]}, {"ids": "x;y"}):
        with pytest.raises(apis.ApiError):
            apis.validate_params(spec, bad)
    with pytest.raises(apis.ApiError, match="not the path"):
        apis.check_specs("/x/{ids}", [{"name": "ids", "type": "list", "in": "path"}], "GET", "")
    assert apis.render_body('{"a": {{ids}}, "b": "{{ids}}"}', {"ids": ["1", "2"]}) == '{"a": ["1", "2"], "b": "1,2"}'


# ---------------------------------------------------------- optional parts
def test_if_parts_are_left_out_when_their_parameter_is_empty():
    body = '{"filter": [{"a": 1}, {"$if": "ids", "then": {"terms": {"x": {{ids}}}}}], ' \
           '"k": {"$if": "q", "then": "{{q}}", "else": "*"}}'
    specs = [{"name": "ids", "type": "list", "in": "body"}, {"name": "q", "type": "string", "in": "body"}]
    apis.check_specs("/s", specs, "POST", body)
    assert json.loads(apis.fill_body(body, {})) == {"filter": [{"a": 1}], "k": "*"}
    assert json.loads(apis.fill_body(body, {"ids": ["7"], "q": "web"})) == {
        "filter": [{"a": 1}, {"terms": {"x": ["7"]}}], "k": "web"}
    with pytest.raises(apis.ApiError, match="names no body parameter"):
        apis.check_specs("/s", [], "POST", '{"f": [{"$if": "nope", "then": 1}]}')
    with pytest.raises(apis.ApiError, match="nothing more"):
        apis.check_specs("/s", specs[:1], "POST", '{"f": [{"$if": "ids", "then": {{ids}}, "x": 1}]}')


def test_a_fixed_query_in_the_path_is_kept_and_cant_hold_placeholders():
    op = {"path": "/agents?select=id,name", "method": "GET", "params": json.dumps([
        {"name": "status", "type": "list", "in": "query"}]), "body": ""}
    _, url, query, _ = apis.build_request({"base_url": "https://m:55000"}, op, {"status": ["active", "pending"]})
    assert url == "https://m:55000/agents" and query == {"select": "id,name", "status": "active,pending"}
    with pytest.raises(apis.ApiError, match="fixed query"):
        apis.check_specs("/agents?select={x}", [{"name": "x", "type": "string", "in": "query"}], "GET", "")


# ---------------------------------------------------------------- shapes
ES_ANSWER = {
    "took": 12, "timed_out": False, "_shards": {"total": 3, "failed": 0},
    "hits": {"total": {"value": 10000, "relation": "gte"}, "max_score": None, "hits": [
        {"_index": "wazuh-alerts-4.x-2026.11.17", "_id": "abc", "sort": [1],
         "_source": {"timestamp": "2026-11-17T10:00:00Z", "rule": {"level": 12, "id": "204", "description": "Flood"},
                     "agent": {"name": "site-chan"}, "data": {"srcip": "192.168.1.253"}}}]},
    "aggregations": {
        "agents": {"doc_count_error_upper_bound": 0, "sum_other_doc_count": 42, "buckets": [
            {"key": "site-chan", "doc_count": 900, "last": {"value": 1.7e12, "value_as_string": "2026-11-17T10:00"},
             "recent": {"doc_count": 0}, "rule_id": {"buckets": [{"key": "204", "doc_count": 900}]}}]},
        "timeline": {"buckets": [{"key_as_string": "2026-11-17T09:00", "key": 1, "doc_count": 5}]}}}


def test_the_elasticsearch_shape_drops_boilerplate_and_flattens():
    shape = apis.check_shape({"format": "elasticsearch", "rename": {"rule.level": "level", "agent.name": "agent"}})
    out = apis.apply_shape(ES_ANSWER, shape)
    assert out["total"] == 10000 and out["total_is_at_least"] is True
    assert out["rows"] == [{"timestamp": "2026-11-17T10:00:00Z", "level": 12, "rule.id": "204",
                            "rule.description": "Flood", "agent": "site-chan", "data.srcip": "192.168.1.253"}]
    # A one-bucket list that covers its whole parent is just its key; a filter with nothing under it, its count.
    assert out["agents"] == [{"key": "site-chan", "count": 900, "last": "2026-11-17T10:00", "recent": 0,
                              "rule_id": "204"}]
    assert out["agents_others"] == 42 and out["timeline"] == [{"key": "2026-11-17T09:00", "count": 5}]
    assert "_shards" not in json.dumps(out) and "_index" not in json.dumps(out)


def test_noise_report_and_agent_silence_fields_keep_one_type_from_answer_to_answer():
    # Reported from work on 2026-11-18: recent was a number one time and {count, meta} the next, and a Noise
    # report's description/agents a string one time and a list the next.
    def noise(agents, descriptions):                    # one rule's bucket, as OpenSearch sends it
        return {"aggregations": {"rules": {"sum_other_doc_count": 0, "buckets": [{
            "key": "5710", "doc_count": 30, "level": {"value": 5.0},
            "description": {"sum_other_doc_count": 30 - descriptions[0][1],
                            "buckets": [{"key": k, "doc_count": n} for k, n in descriptions[:1]]},
            "agents": {"buckets": [{"key": k, "doc_count": n} for k, n in agents]},
            "sources": {"buckets": []}, "groups": {"buckets": [{"key": "sshd", "doc_count": 30}]}}]}}}

    shape = dict(json.loads(wazuh_presets.ES_SHAPE))
    one_agent = apis.apply_shape(noise([("site-chan", 30)], [("sshd: attempt to login", 30)]), json.dumps(shape))
    two_agents = apis.apply_shape(noise([("site-chan", 20), ("pi-chan", 10)],
                                        [("sshd: attempt to login", 20), ("sshd: other", 10)]), json.dumps(shape))
    for out in (one_agent, two_agents):                 # same types either way
        rule = out["rules"][0]
        assert rule["description"] == "sshd: attempt to login"                     # always a string
        assert isinstance(rule["agents"], list) and rule["agents"][0]["key"] == "site-chan"
        assert rule["sources"] == [] and rule["groups"] == [{"key": "sshd", "count": 30}]
    assert one_agent["rules"][0]["agents"] == [{"key": "site-chan", "count": 30}]

    silence = {"aggregations": {"agents": {"buckets": [         # OpenSearch echoes "meta": {} on some filters
        {"key": "pi-chan", "doc_count": 4, "last": {"value": 1.0, "value_as_string": "2026-11-16T03:00"},
         "recent": {"doc_count": 0, "meta": {}}},
        {"key": "site-chan", "doc_count": 900, "last": {"value": 2.0, "value_as_string": "2026-11-18T09:00"},
         "recent": {"doc_count": 70}}]}}}
    for old_or_new in (json.dumps(shape), apis.check_shape({"format": "elasticsearch"})):
        rows = apis.apply_shape(silence, old_or_new)["agents"]
        assert [r["recent"] for r in rows] == [0, 70]   # always a number

    empty = {"aggregations": {"vulns": {"buckets": [{"key": "CVE-1", "doc_count": 1, "severity": {"buckets": []}}]}}}
    assert apis.apply_shape(empty, json.dumps(shape))["vulns"][0]["severity"] is None
    with pytest.raises(apis.ApiError):
        apis.check_shape({"format": "elasticsearch", "single": "description"})


def test_the_wazuh_shape_and_keep_and_redaction():
    answer = {"data": {"affected_items": [
        {"id": 1, "username": "wazuh", "password": "hash", "roles": [1], "allow_run_as": True}],
        "total_affected_items": 1, "total_failed_items": 0, "failed_items": []},
        "message": "All specified users were returned", "error": 0}
    shape = apis.check_shape({"format": "wazuh", "keep": ["id", "username", "roles"]})
    assert apis.apply_shape(answer, shape) == {"total": 1, "rows": [{"id": 1, "username": "wazuh", "roles": [1]}]}

    config = {"data": {"affected_items": [{
        "auth": {"disabled": "no", "use_password": "yes", "port": 1515},
        "integration": [{"name": "slack", "hook_url": "https://hooks.slack.com/services/T0/B0/XYZ"},
                        {"name": "virustotal", "api_key": "0123abcd"}]}], "total_affected_items": 1}}
    out = apis.apply_shape(config, apis.check_shape({"format": "wazuh", "redact": True}))
    row = out["rows"][0]
    assert row["auth.use_password"] == "yes" and row["auth.port"] == 1515      # switches stay
    assert row["integration"][0]["hook_url"] == "[redacted]" and row["integration"][1]["api_key"] == "[redacted]"
    log = "wazuh-authd: INFO: Random password chosen for agent authentication: 3b7c9e1f2a"
    assert apis.redact_text(log).endswith("authentication: [redacted]") and "3b7c" not in apis.redact_text(log)
    assert "XYZ" not in apis.redact_text("posting to https://hooks.slack.com/services/T0/B0/XYZ failed")
    with pytest.raises(apis.ApiError):
        apis.check_shape({"format": "xml"})
    with pytest.raises(apis.ApiError):
        apis.check_shape({"keep": "id"})


# ---------------------------------------------------------- calling them
def test_alerts_filter_on_the_indexer_and_come_back_as_rows(conn, settings):
    ids = _add(conn, settings, "wazuh", ["Alerts"])
    seen = _upstream(lambda r: httpx.Response(200, json=ES_ANSWER))
    result = apis.call(conn, settings, ids["Alerts"], {"text": "web attack", "exclude_rule_id": "204,31101",
                                                       "agent": "site-chan"}, user_id=None, via="chat")
    sent = json.loads(seen[0].content)
    assert sent["track_total_hits"] is True and sent["size"] == 20
    assert {"wildcard": {"rule.description": {"value": "*web attack*", "case_insensitive": True}}} in \
        sent["query"]["bool"]["filter"]
    assert sent["query"]["bool"]["must_not"] == [{"terms": {"rule.id": ["204", "31101"]}}]
    assert not any("rule.groups" in json.dumps(f) for f in sent["query"]["bool"]["filter"])   # unset: left out
    assert result.data["rows"][0] == {"time": "2026-11-17T10:00:00Z", "level": 12, "rule_id": "204",
                                      "description": "Flood", "agent": "site-chan", "srcip": "192.168.1.253"}


def test_the_manager_signs_in_once_and_again_when_the_token_is_refused(conn, settings):
    ids = _add(conn, settings, "wazuh-manager", ["Agents", "Manager logs"], login=True, secret="s3cret")
    tokens = iter(["tok-1", "tok-2"])
    state = {"refuse": None}

    def handler(request):
        if request.url.path == "/security/user/authenticate":
            assert request.method == "POST" and request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"data": {"token": next(tokens)}, "error": 0})
        if request.headers["authorization"] == state["refuse"]:
            return httpx.Response(401, json={"title": "Unauthorized"})
        if request.url.path == "/manager/logs":
            return httpx.Response(200, json={"data": {"affected_items": [
                {"timestamp": "2026-11-17", "tag": "wazuh-authd", "level": "info",
                 "description": "Random password chosen for agent authentication: deadbeef99"}],
                "total_affected_items": 1}})
        return httpx.Response(200, json={"data": {"affected_items": [
            {"id": "001", "name": "Pi-Chan", "status": "disconnected", "os": {"name": "Debian"}}],
            "total_affected_items": 1}})

    seen = _upstream(handler)
    first = apis.call(conn, settings, ids["Agents"], {"status": "disconnected"}, user_id=None, via="chat")
    assert first.data == {"total": 1, "rows": [{"id": "001", "name": "Pi-Chan", "status": "disconnected",
                                                "os.name": "Debian"}]}
    assert seen[1].headers["authorization"] == "Bearer tok-1" and seen[1].url.params["status"] == "disconnected"
    assert seen[1].url.params["select"].startswith("id,name,ip")               # the fixed query went too
    apis.call(conn, settings, ids["Agents"], {"limit": 5}, user_id=None, via="chat")
    assert [r.url.path for r in seen].count("/security/user/authenticate") == 1  # the token is reused

    state["refuse"] = "Bearer tok-1"                                           # it expired early
    logs = apis.call(conn, settings, ids["Manager logs"], {}, user_id=None, via="chat")
    assert logs.ok and [r.url.path for r in seen].count("/security/user/authenticate") == 2
    assert logs.data["rows"][0]["description"].endswith("authentication: [redacted]")
    with call_logs.on(conn) as logs_db:                                        # api_calls lives in logs.db
        stored = " ".join(r[0] or "" for r in logs_db.execute("SELECT response_excerpt FROM api_calls"))
    assert "deadbeef99" not in stored and "[redacted]" in stored               # never reached the call log


def test_a_refused_sign_in_says_so(conn, settings):
    ids = _add(conn, settings, "wazuh-manager", ["Agents"], login=True)
    _upstream(lambda r: httpx.Response(401, json={"title": "Unauthorized"}))
    result = apis.call(conn, settings, ids["Agents"], {}, user_id=None, via="chat")
    assert not result.ok and "refused the user name and password at sign-in" in result.error


def test_query_api_hands_claude_json_not_a_string(conn, settings, directory):
    ids = _add(conn, settings, "wazuh", ["Alert summary"])
    ivan = users.create_user(conn, "ivan", "password123")
    accounts.link(conn, ivan, "ivan", directory)
    _upstream(lambda r: httpx.Response(200, json=ES_ANSWER))
    tools = Tools(settings, lambda: None, lambda: directory)
    out, _ = tools.query_api(conn, Caller(ivan, "ivan", 1), "Wazuh", "Alert summary", {"min_level": 10})
    assert isinstance(out["data"], dict) and out["data"]["agents"][0]["key"] == "site-chan"
    assert ids


# ------------------------------------------------------------------ admin
def test_admin_makes_the_manager_api_and_updates_an_old_wazuh_api(admin_client, conn):
    presets = {p["key"]: p for p in admin_client.get("/api/admin/apis").json()["presets"]}
    assert presets["wazuh-manager"]["base_url"] == "https://wazuh-manager.example:55000"
    made = admin_client.post("/api/admin/apis", json={"preset": "wazuh-manager",
                                                      "base_url": "https://10.0.0.5:55000"}).json()
    detail = admin_client.get(f"/api/admin/apis/{made['id']}").json()
    assert detail["base_url"] == "https://10.0.0.5:55000" and detail["auth_login_path"] == "/security/user/authenticate"
    assert {"Agents", "Manager configuration", "API users"} <= {o["name"] for o in detail["operations"]}
    assert json.loads(next(o for o in detail["operations"] if o["name"] == "Manager logs")["shape"])["redact"] is True
    assert admin_client.patch(f"/api/admin/apis/{made['id']}", json={"auth_login_path": "/../x"}).status_code == 422

    # An API from the old four-operation preset: Update brings its operations to the new version.
    old = conn.execute("INSERT INTO apis(name, base_url, auth_type) VALUES ('Wazuh', 'https://10.0.0.6:9200', "
                       "'basic')").lastrowid
    conn.execute("INSERT INTO api_operations(api_id, name, method, path, body, enabled) VALUES (?, 'Alerts', 'POST', "
                 "'/wazuh-alerts-*/_search', '{\"size\": 5}', 0)", (old,))
    conn.execute("INSERT INTO api_operations(api_id, name, path) VALUES (?, 'My own', '/x')", (old,))
    added = admin_client.post(f"/api/admin/apis/{old}/preset-operations", json={"preset": "wazuh"}).json()
    assert added == {"added": 12, "updated": 0}
    assert conn.execute("SELECT body FROM api_operations WHERE api_id = ? AND name = 'Alerts'", (old,)).fetchone()[0] \
        == '{"size": 5}'                                                       # plain Add leaves it
    done = admin_client.post(f"/api/admin/apis/{old}/preset-operations",
                             json={"preset": "wazuh", "update": True}).json()
    assert done == {"added": 0, "updated": 13}
    row = conn.execute("SELECT body, shape, enabled FROM api_operations WHERE api_id = ? AND name = 'Alerts'",
                       (old,)).fetchone()
    assert "track_total_hits" in row["body"] and json.loads(row["shape"])["format"] == "elasticsearch"
    assert row["enabled"] == 0                                                 # the admin's switch is kept
    assert conn.execute("SELECT path FROM api_operations WHERE api_id = ? AND name = 'My own'", (old,)).fetchone()[0] \
        == "/x"


def test_an_operation_shape_is_checked_when_saved(admin_client, conn):
    made = admin_client.post("/api/admin/apis", json={"name": "Thing", "base_url": "https://t.test"}).json()
    bad = admin_client.post(f"/api/admin/apis/{made['id']}/operations",
                            json={"name": "x", "path": "/x", "shape": '{"format": "xml"}'})
    assert bad.status_code == 422
    ok = admin_client.post(f"/api/admin/apis/{made['id']}/operations",
                           json={"name": "x", "path": "/x", "shape": '{"redact": true}'})
    assert ok.status_code == 201


def test_templates_carry_shapes_and_sign_in_paths():
    t = apis.parse_template(json.dumps({
        "format": "mnemerynne-api-template", "name": "Thing", "base_url": "https://t.test", "auth_type": "basic",
        "auth_user": "r", "auth_login_path": "/login",
        "operations": [{"name": "Items", "path": "/items?fields=a,b", "shape": {"keep": ["a"]}}]}))
    assert t["auth_login_path"] == "/login" and json.loads(t["operations"][0]["shape"]) == {"keep": ["a"]}
    with pytest.raises(apis.ApiError, match="auth_type basic"):
        apis.parse_template(json.dumps({"format": "mnemerynne-api-template", "name": "T", "base_url": "https://t.test",
                                        "auth_login_path": "/login", "operations": [{"name": "I", "path": "/i"}]}))
