"""Parameters inside POST bodies ({{name}}): the Wazuh operations a log-reading agent needs."""

import json

import httpx
import pytest

from mnemerynne import agent as agent_mod, apis
from test_apis import _fresh_cache, admin_client, directory  # noqa: F401  (fixtures)


def _op(conn, body, params, method="POST"):
    api_id = conn.execute("INSERT INTO apis(name, base_url, auth_type, ad_group) "
                          "VALUES ('Wazuh', 'https://wazuh.test:9200', 'none', 'IT')").lastrowid
    return conn.execute("INSERT INTO api_operations(api_id, name, method, path, params, body) VALUES "
                        "(?, 'Alerts', ?, '/wazuh-alerts-*/_search', ?, ?)",
                        (api_id, method, json.dumps(params), body)).lastrowid


def test_values_go_in_as_json_never_as_query_syntax():
    template = '{"size": {{count}}, "q": {"gte": "now-{{hours}}h", "agent": "{{agent}}"}, "x": {{missing}}}'
    out = json.loads(apis.render_body(template, {"count": 5, "hours": 6, "agent": 'FD-*"} ,{"evil": 1'}))
    assert out == {"size": 5, "q": {"gte": "now-6h", "agent": 'FD-*"} ,{"evil": 1'}, "x": None}   # stays a string
    # A {{name}} that is part of a key or already-escaped text is still just text in its string.
    assert json.loads(apis.render_body('{"a\\"{{n}}": "{{n}}"}', {"n": "z"})) == {'a"z': "z"}


def test_admin_input_is_checked():
    good = [{"name": "hours", "type": "int", "in": "body", "min": 1, "max": 720}]
    apis.check_specs("/x", good, "POST", '{"gte": "now-{{hours}}h"}')
    with pytest.raises(apis.ApiError, match="no body parameter"):
        apis.check_specs("/x", good, "POST", '{"gte": "now-{{days}}h"}')
    with pytest.raises(apis.ApiError, match="isn't used in the body"):
        apis.check_specs("/x", good, "POST", '{"gte": "now-24h"}')
    with pytest.raises(apis.ApiError, match="only POST"):
        apis.check_specs("/x", good, "GET", "")
    with pytest.raises(apis.ApiError, match="JSON body"):
        apis.check_specs("/x", good, "POST", '{"gte": {{hours}}')


def test_wildcards_only_in_bodies():
    body = [{"name": "agent", "type": "string", "in": "body", "default": "*"}]
    query = [{"name": "agent", "type": "string", "in": "query"}]
    assert apis.validate_params(body, {}) == {"agent": "*"}
    assert apis.validate_params(body, {"agent": "FD-*"}) == {"agent": "FD-*"}
    with pytest.raises(apis.ApiError, match="characters"):
        apis.validate_params(query, {"agent": "FD-*"})
    with pytest.raises(apis.ApiError, match="characters"):
        apis.validate_params(body, {"agent": 'x"}'})


def test_wazuh_preset_operations(conn, settings):
    ops = {op["name"]: op for op in apis.preset("wazuh")["operations"]}
    assert set(ops) == {"Alert summary", "Alerts", "Failed logins"}
    seen = []
    apis.TRANSPORT = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json={"hits": {"hits": []}}))
    alerts = ops["Alerts"]
    op_id = _op(conn, alerts["body"], alerts["params"])
    result = apis.call(conn, settings, op_id, {"hours": 6, "min_level": 12, "agent": "FD-01"}, user_id=None, via="chat")
    assert result.ok
    sent = json.loads(seen[0].content)
    filters = sent["query"]["bool"]["filter"]
    assert sent["size"] == 20 and filters[0] == {"range": {"timestamp": {"gte": "now-6h"}}}
    assert filters[1] == {"range": {"rule.level": {"gte": 12}}} and filters[2] == {"wildcard": {"agent.name": "FD-01"}}
    with pytest.raises(apis.ApiError, match="between"):
        apis.call(conn, settings, op_id, {"hours": 9999}, user_id=None, via="chat")
    # The agent's tool for it has typed, bounded parameters.
    row = conn.execute("SELECT o.*, 'Wazuh' AS api_name FROM api_operations o WHERE o.id = ?", (op_id,)).fetchone()
    spec = agent_mod.api_tools([row])[0][0]["function"]
    assert spec["name"] == "wazuh_alerts"
    assert spec["parameters"]["properties"]["hours"]["anyOf"][0] == {"type": "integer", "minimum": 1, "maximum": 720,
                                                                     "description": "How many hours back"}


def test_existing_apis_take_in_new_preset_operations(admin_client, conn):  # noqa: F811
    api_id = conn.execute("INSERT INTO apis(name, base_url, auth_type) VALUES ('Wazuh', 'https://w.test', 'basic')"
                          ).lastrowid
    conn.execute("INSERT INTO api_operations(api_id, name, method, path, body) VALUES (?, 'Alerts', 'POST', "
                 "'/x', '{}')", (api_id,))
    added = admin_client.post(f"/api/admin/apis/{api_id}/preset-operations", json={"preset": "wazuh"}).json()
    assert added == {"added": 2}                       # "Alerts" was already there
    names = [r[0] for r in conn.execute("SELECT name FROM api_operations WHERE api_id = ? ORDER BY id", (api_id,))]
    assert names == ["Alerts", "Alert summary", "Failed logins"]
    assert admin_client.post(f"/api/admin/apis/{api_id}/preset-operations",
                             json={"preset": "wazuh"}).json() == {"added": 0}
    assert admin_client.post(f"/api/admin/apis/{api_id}/preset-operations",
                             json={"preset": "nope"}).status_code == 404
