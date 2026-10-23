"""Internal APIs that Erynne may query: read-only, per AD group.

Admins define an API (address, credential, who may use it) and the operations
on it: a fixed path (and, for POST, a fixed body) with typed parameters. The
model only ever picks an operation and fills its parameters; the request
itself is built here, so it can't reach anything an admin didn't set up.

- Credentials are encrypted with a key derived from MNEMERYNNE_SECRET_KEY and
  are never returned by any endpoint.
- Who may use an API: members of its AD group (through their linked domain
  account). Claude additionally needs the API's Claude switch.
- Calls: GET (or an admin-fixed POST), no redirects, 20 s timeout, 256 KB cap,
  short cache, and every call recorded in api_calls.
"""

from __future__ import annotations

import base64
import json
import re
import ssl
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import numpy as np

from mnemerynne.config import Settings

TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 256 * 1024
EXCERPT_CHARS = 20_000           # kept with each call, for the citation link
PARAM_TYPES = ("string", "int", "date", "enum")
_SAFE_STRING = re.compile(r"^[\w .,:@+\-/]{0,200}$")
# In a POST body, * and ? are allowed too: search patterns (e.g. agent "*" for all, "FD-*").
_SAFE_BODY_STRING = re.compile(r"^[\w .,:@+\-/*?]{0,200}$")
# {{name}} in a POST body: a body parameter, written in as JSON (escaped inside a
# string, a bare value elsewhere), so the model fills values but never the query's shape.
_BODY_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_]\w*)\}\}")
_SAFE_PATH_VALUE = re.compile(r"^[\w.\-]{1,100}$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
_SECRET_INFO = b"mnemerynne-api-secrets"

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


class ApiError(Exception):
    """A refusal or failure; the message is safe to show to the person asking."""


# ------------------------------------------------------------------- secrets
def _fernet(settings: Settings):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if not settings.secret_key:
        raise ApiError("Set MNEMERYNNE_SECRET_KEY on the server before storing API credentials.")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_SECRET_INFO).derive(
        settings.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(settings: Settings, value: str) -> bytes:
    return _fernet(settings).encrypt(value.encode())


def decrypt_secret(settings: Settings, blob: bytes | None) -> str | None:
    """The credential, or None if there is none or it can't be read (the secret key changed)."""
    from cryptography.fernet import InvalidToken

    if not blob:
        return None
    try:
        return _fernet(settings).decrypt(bytes(blob)).decode()
    except (InvalidToken, ApiError, ValueError):
        return None


def credential_status(settings: Settings, api: sqlite3.Row) -> str:
    if api["auth_type"] == "none":
        return "not needed"
    if not api["secret_enc"]:
        return "missing"
    return "set" if decrypt_secret(settings, api["secret_enc"]) is not None else "needs re-entering"


# -------------------------------------------------------------------- access
def allowed_operations(conn: sqlite3.Connection, user, directory, *, for_claude: bool = False) -> list[sqlite3.Row]:
    """Enabled operations on enabled APIs whose group (AD or Entra, group_refs) the user is in."""
    from mnemerynne import group_refs

    if user is None:
        return []
    groups = group_refs.member_of(conn, user, directory)
    if not groups:
        return []
    rows = conn.execute(
        "SELECT o.*, a.name AS api_name, a.ad_group, a.claude_enabled FROM api_operations o "
        "JOIN apis a ON a.id = o.api_id WHERE o.enabled = 1 AND a.enabled = 1 AND a.ad_group <> '' "
        "ORDER BY a.name COLLATE NOCASE, o.name COLLATE NOCASE").fetchall()
    return [r for r in rows if group_refs.in_any(conn, user, directory, r["ad_group"], groups)
            and (not for_claude or r["claude_enabled"])]


def operation_for(conn: sqlite3.Connection, user, directory, api_name: str, operation_name: str,
                  *, for_claude: bool = False) -> sqlite3.Row:
    for r in allowed_operations(conn, user, directory, for_claude=for_claude):
        if r["api_name"].lower() == (api_name or "").strip().lower() and \
                r["name"].lower() == (operation_name or "").strip().lower():
            return r
    raise ApiError(f"There's no API operation “{api_name} / {operation_name}” you can use.")


# ---------------------------------------------------------------- parameters
def param_specs(op) -> list[dict]:
    try:
        specs = json.loads(op["params"] or "[]")
    except ValueError:
        return []
    return [s for s in specs if isinstance(s, dict) and s.get("name")]


def check_specs(path: str, specs: list[dict], method: str, body: str) -> None:
    """Admin input: refuse operations that couldn't be called safely."""
    names = set()
    for s in specs:
        name = s.get("name", "")
        if not re.fullmatch(r"[A-Za-z_]\w{0,40}", name) or name in names:
            raise ApiError(f"Parameter name “{name}” isn't valid or repeats.")
        names.add(name)
        if s.get("type") not in PARAM_TYPES:
            raise ApiError(f"Parameter “{name}” needs a type: {', '.join(PARAM_TYPES)}.")
        if s.get("in", "query") not in ("query", "path", "body"):
            raise ApiError(f"Parameter “{name}” goes in the query, the path or the body.")
        if s.get("in") == "body" and method != "POST":
            raise ApiError(f"Parameter “{name}” is in the body, but only POST operations have one.")
        if s.get("type") == "enum" and not s.get("options"):
            raise ApiError(f"Parameter “{name}” is a list; give its options.")
    for placeholder in _PLACEHOLDER.findall(path):
        if not any(s["name"] == placeholder and s.get("in") == "path" for s in specs):
            raise ApiError(f"The path uses {{{placeholder}}} but there's no path parameter by that name.")
    if method == "POST":
        in_body = {s["name"] for s in specs if s.get("in") == "body"}
        used = set(_BODY_PLACEHOLDER.findall(body or ""))
        if used - in_body:
            raise ApiError(f"The body uses {{{{{sorted(used - in_body)[0]}}}}} but there's no body parameter by that name.")
        if in_body - used:
            raise ApiError(f"Body parameter “{sorted(in_body - used)[0]}” isn't used in the body ({{{{name}}}}).")
        samples = {s["name"]: _sample(s) for s in specs if s.get("in") == "body"}
        try:
            json.loads(render_body(body, samples))
        except ValueError as exc:
            raise ApiError("A POST operation needs a JSON body (with {{name}} where body parameters go).") from exc
    elif body:
        raise ApiError("Only POST operations have a body.")
    if not path.startswith("/") or path.startswith("//") or ".." in path or "://" in path or "\\" in path:
        raise ApiError("The path must start with / and stay under the API's address.")


def _sample(spec: dict):
    """A valid value of the parameter's type, to check that the body is JSON once filled in."""
    kind = spec.get("type")
    if kind == "int":
        return int(spec.get("min") if spec.get("min") is not None else 1)
    if kind == "enum":
        return str((spec.get("options") or ["x"])[0])
    return "2026-01-01" if kind == "date" else "x"


def render_body(template: str, values: dict) -> str:
    """The POST body with each {{name}} filled in: escaped text inside a JSON
    string ("now-{{hours}}h"), a JSON value elsewhere ({"size": {{count}}}).
    A parameter without a value becomes "" or null."""
    out, pos, in_string, escaped = [], 0, False, False
    while pos < len(template):
        m = _BODY_PLACEHOLDER.match(template, pos)
        if m:
            value = values.get(m.group(1))
            if in_string:
                out.append(json.dumps("" if value is None else str(value))[1:-1])
            else:
                out.append(json.dumps(value))
            pos = m.end()
            continue
        ch = template[pos]
        out.append(ch)
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        pos += 1
    return "".join(out)


def validate_params(specs: list[dict], given: dict | None) -> dict:
    """Check what the model (or a person) supplied against the operation's specs."""
    given = dict(given or {})
    unknown = set(given) - {s["name"] for s in specs}
    if unknown:
        raise ApiError(f"Unknown parameter(s): {', '.join(sorted(unknown))}.")
    out: dict[str, Any] = {}
    for s in specs:
        name, kind = s["name"], s["type"]
        value = given.get(name)
        if value in (None, ""):
            if s.get("default") not in (None, ""):
                value = s["default"]
            elif s.get("required"):
                raise ApiError(f"“{name}” is needed.")
            else:
                continue
        if kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ApiError(f"“{name}” must be a whole number.") from None
            if s.get("min") is not None and value < int(s["min"]) or s.get("max") is not None and value > int(s["max"]):
                raise ApiError(f"“{name}” must be between {s.get('min', '…')} and {s.get('max', '…')}.")
        elif kind == "date":
            try:
                value = date.fromisoformat(str(value)).isoformat()
            except ValueError:
                raise ApiError(f"“{name}” must be a date like 2026-10-22.") from None
        elif kind == "enum":
            options = [str(o) for o in s.get("options") or []]
            match = next((o for o in options if o.lower() == str(value).strip().lower()), None)
            if match is None:
                raise ApiError(f"“{name}” must be one of: {', '.join(options)}.")
            value = match
        else:
            value = str(value).strip()
            if not (_SAFE_BODY_STRING if s.get("in") == "body" else _SAFE_STRING).match(value):
                raise ApiError(f"“{name}” contains characters that aren't allowed.")
        if s.get("in") == "path" and not _SAFE_PATH_VALUE.match(str(value)):
            raise ApiError(f"“{name}” can't be used in the address.")
        out[name] = value
    return out


def build_request(api, op, params: dict) -> tuple[str, str, dict, str | None]:
    """(method, url, query, body) for a validated call."""
    specs = {s["name"]: s for s in param_specs(op)}
    path = _PLACEHOLDER.sub(lambda m: quote(str(params[m.group(1)]), safe=""), op["path"])
    url = api["base_url"].rstrip("/") + path
    base, built = urlsplit(api["base_url"]), urlsplit(url)
    if (base.scheme, base.netloc) != (built.scheme, built.netloc) or built.scheme not in ("https", "http"):
        raise ApiError("The operation's address leaves the API.")
    query = {k: v for k, v in params.items() if specs[k].get("in", "query") == "query"}
    if op["method"] != "POST":
        return op["method"], url, query, None
    body = {k: v for k, v in params.items() if specs[k].get("in") == "body"}
    return op["method"], url, query, render_body(op["body"], body) if op["body"] else None


# ------------------------------------------------------------------- calling
@dataclass
class CallResult:
    call_id: int
    ok: bool
    api_name: str
    operation_name: str
    at: str
    status_code: int | None = None
    data: Any = None             # parsed JSON, or text
    error: str | None = None
    cached: bool = False
    truncated: bool = False


_cache: dict[tuple, tuple[float, CallResult]] = {}
_cache_lock = threading.Lock()


def _ssl(api) -> ssl.SSLContext | bool:
    if not api["ca_pem"].strip():
        return True
    ctx = ssl.create_default_context(cadata=api["ca_pem"])
    if not api["tls_check_hostname"]:
        # Only the pinned certificate is trusted, so the name it carries
        # matters less (e.g. a server reached by IP).
        ctx.check_hostname = False
    return ctx


def _headers(settings: Settings, api) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Mnemerynne"}
    if api["auth_type"] == "none":
        return headers
    secret = decrypt_secret(settings, api["secret_enc"])
    if secret is None:
        raise ApiError(f"{api['name']} has no usable credential; an admin needs to enter it on the APIs page.")
    if api["auth_type"] == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif api["auth_type"] == "header":
        headers[api["auth_header"] or "X-API-Key"] = secret
    else:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{api['auth_user']}:{secret}".encode()).decode()
    return headers


def _fetch(api, method, url, query, body, headers) -> tuple[int, str, bytes, bool]:
    with httpx.Client(verify=_ssl(api), follow_redirects=False, timeout=TIMEOUT, transport=TRANSPORT) as client:
        if body is not None:
            headers = {**headers, "Content-Type": "application/json"}
        with client.stream(method, url, params=query, content=body, headers=headers) as response:
            chunks, size, truncated = [], 0, False
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    truncated = True
                    break
            return response.status_code, response.headers.get("content-type", ""), b"".join(chunks)[:MAX_RESPONSE_BYTES], truncated


def _explain(status: int, text: str) -> str:
    if status in (401, 403):
        return f"the API refused the credential (HTTP {status}); an admin should check it on the APIs page"
    if 300 <= status < 400:
        return f"the API tried to redirect (HTTP {status}); redirects aren't followed"
    detail = ""
    try:
        detail = str(json.loads(text).get("error") or "")[:200]
    except (ValueError, AttributeError):
        pass
    return f"the API answered HTTP {status}" + (f": {detail}" if detail else "")


def call(conn: sqlite3.Connection, settings: Settings, op_id: int, params: dict | None, *,
         user_id: int | None, via: str) -> CallResult:
    """Run one operation (after the caller checked the user may) and record it."""
    op = conn.execute("SELECT * FROM api_operations WHERE id = ?", (op_id,)).fetchone()
    if op is None:
        raise ApiError("That API operation no longer exists.")
    api = conn.execute("SELECT * FROM apis WHERE id = ?", (op["api_id"],)).fetchone()
    params = validate_params(param_specs(op), params)
    key = (op_id, json.dumps(params, sort_keys=True))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    started = time.perf_counter()
    if hit and hit[0] > now and via != "test":
        result = CallResult(0, True, api["name"], op["name"], "", hit[1].status_code, hit[1].data,
                            cached=True, truncated=hit[1].truncated)
        raw_len = None
    else:
        result = CallResult(0, False, api["name"], op["name"], "")
        raw_len = None
        try:
            method, url, query, body = build_request(api, op, params)
            status, ctype, raw, truncated = _fetch(api, method, url, query, body, _headers(settings, api))
            raw_len = len(raw)
            text = raw.decode("utf-8", errors="replace")
            result.status_code, result.truncated = status, truncated
            if 200 <= status < 300:
                result.ok = True
                try:
                    result.data = json.loads(text) if not truncated else text
                except ValueError:
                    result.data = text
            else:
                result.error = _explain(status, text)
        except ApiError as exc:
            result.error = str(exc)
        except httpx.TimeoutException:
            result.error = f"{api['name']} didn't answer within {int(TIMEOUT)} seconds"
        except (httpx.HTTPError, ssl.SSLError, OSError) as exc:
            message = str(exc)
            result.error = ("couldn't verify the API's certificate; check the trusted certificate on the APIs page"
                            if "CERTIFICATE" in message.upper() else f"couldn't reach {api['name']}: {message[:200]}")
        if result.ok and op["cache_seconds"] > 0:
            with _cache_lock:
                _cache[key] = (now + op["cache_seconds"], result)
    excerpt = None
    if result.ok:
        excerpt = (json.dumps(result.data, indent=1, ensure_ascii=False) if not isinstance(result.data, str)
                   else result.data)[:EXCERPT_CHARS]
    cur = conn.execute(
        "INSERT INTO api_calls(user_id, via, api_id, operation_id, api_name, operation_name, params, status_code, "
        "ok, cached, ms, error, response_bytes, response_excerpt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING id, at",
        (user_id, via, api["id"], op["id"], api["name"], op["name"], json.dumps(params), result.status_code,
         int(result.ok), int(result.cached), round((time.perf_counter() - started) * 1000), result.error,
         raw_len, excerpt)).fetchone()
    result.call_id, result.at = cur["id"], cur["at"]
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ----------------------------------------------------- shaping for the model
def _trim(value, items: int):
    if isinstance(value, list):
        kept = [_trim(v, items) for v in value[:items]]
        if len(value) > items:
            kept.append(f"(showing {items} of {len(value)})")
        return kept
    if isinstance(value, dict):
        return {k: _trim(v, items) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def digest(data, max_chars: int) -> str:
    """The response as compact text that fits the model's context."""
    if isinstance(data, str):
        return data[:max_chars]
    for items in (50, 20, 10, 5, 3, 1):
        text = json.dumps(_trim(data, items), ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text
    return text[:max_chars] + "…"


def infer_shape(data, prefix: str = "", out: list | None = None, limit: int = 200) -> list[tuple[str, str]]:
    """Field paths and types of a response, for the APIs page."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            out.append((path, _type(value)))
            if isinstance(value, (dict, list)):
                infer_shape(value, path, out, limit)
    elif isinstance(data, list) and data:
        merged: dict = {}
        for item in data[:3]:
            if isinstance(item, dict):
                for k, v in item.items():
                    merged.setdefault(k, v)
        if merged:
            infer_shape(merged, f"{prefix}[]", out, limit)
    return out[:limit]


def _type(value) -> str:
    return {dict: "object", list: "list", bool: "true/false", int: "number", float: "number",
            str: "text", type(None): "null"}.get(type(value), "value")


# ------------------------------------------------------------------- routing
def index_examples(conn: sqlite3.Connection, embedder, op_id: int) -> int:
    """Embed an operation's example questions (as queries, like chat questions)."""
    op = conn.execute("SELECT example_questions FROM api_operations WHERE id = ?", (op_id,)).fetchone()
    texts = [t.strip() for t in (op["example_questions"] if op else "").splitlines() if t.strip()][:30]
    conn.execute("DELETE FROM api_examples WHERE operation_id = ?", (op_id,))
    for text in texts:
        vector = np.asarray(embedder.embed_query(text), dtype=np.float32)
        conn.execute("INSERT INTO api_examples(operation_id, text, embedding) VALUES (?, ?, ?)",
                     (op_id, text, vector.tobytes()))
    return len(texts)


def match(conn: sqlite3.Connection, vector, op_ids: list[int]) -> tuple[int, float] | None:
    """The operation whose example questions are closest to the question (cosine)."""
    if not op_ids:
        return None
    marks = ",".join("?" * len(op_ids))
    rows = conn.execute(f"SELECT operation_id, embedding FROM api_examples WHERE operation_id IN ({marks})",
                        op_ids).fetchall()
    if not rows:
        return None
    q = np.asarray(vector, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    best = None
    for r in rows:
        v = np.frombuffer(r["embedding"], dtype=np.float32)
        if v.shape != q.shape:
            continue
        score = float(np.dot(q, v / (np.linalg.norm(v) or 1.0)))
        if best is None or score > best[1]:
            best = (r["operation_id"], score)
    return best


def fill_prompt(op, question: str) -> str:
    """What the model sees when filling an operation's parameters from a question."""
    lines = []
    for s in param_specs(op):
        detail = s["type"]
        if s["type"] == "enum":
            detail += ": " + ", ".join(str(o) for o in s["options"])
        if s.get("min") is not None or s.get("max") is not None:
            detail += f", {s.get('min', '')}..{s.get('max', '')}"
        if s.get("default") not in (None, ""):
            detail += f", default {s['default']}"
        lines.append(f"- {s['name']} ({detail}){': ' + s['help'] if s.get('help') else ''}")
    return (f"Fill in the parameters for a call to “{op['api_name']} / {op['name']}” ({op['description']}) "
            f"so that its answer covers the question. Today is {date.today().isoformat()}. Use null for anything "
            f"the question doesn't say; the defaults then apply.\n\nParameters:\n" + "\n".join(lines) +
            f"\n\nQuestion: {question}")


def params_schema(op) -> dict | None:
    """A JSON schema for filling an operation's parameters (None: nothing to fill)."""
    specs = param_specs(op)
    if not specs:
        return None
    props: dict[str, dict] = {}
    for s in specs:
        if s["type"] == "int":
            prop: dict = {"type": "integer"}
            if s.get("min") is not None:
                prop["minimum"] = int(s["min"])
            if s.get("max") is not None:
                prop["maximum"] = int(s["max"])
        elif s["type"] == "enum":
            prop = {"type": "string", "enum": [str(o) for o in s["options"]]}
        elif s["type"] == "date":
            # [0-9], not \d: llama-server's schema-to-grammar turns \d into a literal and the grammar fails to parse.
            prop = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
        else:
            prop = {"type": "string"}
        if s.get("help"):
            prop["description"] = str(s["help"])[:200]
        props[s["name"]] = {"anyOf": [prop, {"type": "null"}]} if not s.get("required") else prop
    return {"type": "object", "properties": props, "required": list(props)}


# ------------------------------------------------------------------- presets
def _p(name, type_, *, where="query", required=False, help="", **extra) -> dict:
    return {"name": name, "type": type_, "in": where, "required": required, "help": help, **extra}


PRESETS: list[dict] = [
    {
        "key": "wazuh", "name": "Wazuh", "base_url": "https://172.27.22.250:9200", "auth_type": "basic",
        "tls_check_hostname": 0,
        "description": "Wazuh indexer (security alerts). Use a dedicated read-only user; only the fixed query "
                       "below is ever sent. Trust the indexer's certificate on this page.",
        "operations": [
            {"name": "Alert summary", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 120,
             "params": [_p("hours", "int", where="body", min=1, max=720, default=24, help="How many hours back"),
                        _p("min_level", "int", where="body", min=0, max=15, default=0,
                           help="Lowest rule level to count (7+ notable, 12+ critical)"),
                        _p("agent", "string", where="body", default="*",
                           help="Machine (agent) name; * for all, or a pattern like FD-*")],
             "body": '{"size": 0, "query": {"bool": {"filter": [{"range": {"timestamp": {"gte": "now-{{hours}}h"}}}, {"range": {"rule.level": {"gte": {{min_level}}}}}, {"wildcard": {"agent.name": "{{agent}}"}}]}}, "aggs": {"levels": {"terms": {"field": "rule.level", "size": 16}}, "rules": {"terms": {"field": "rule.description", "size": 15}, "aggs": {"level": {"max": {"field": "rule.level"}}}}, "agents": {"terms": {"field": "agent.name", "size": 15}}, "tactics": {"terms": {"field": "rule.mitre.tactic", "size": 10}}, "sources": {"terms": {"field": "data.srcip", "size": 10}}}}',
             "description": "Counts of security alerts by level, rule, machine, MITRE tactic and source IP, "
                            "for a time window, minimum level and machine.",
             "example_questions": "How many security alerts were there today?\nWhat's the alert count by severity?\n"
                                  "Which machines have the most alerts this week?\nWhat are the top alert types?"},
            {"name": "Alerts", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 60,
             "params": [_p("hours", "int", where="body", min=1, max=720, default=24, help="How many hours back"),
                        _p("min_level", "int", where="body", min=0, max=15, default=7,
                           help="Lowest rule level (7+ notable, 12+ critical)"),
                        _p("agent", "string", where="body", default="*",
                           help="Machine (agent) name; * for all, or a pattern like FD-*"),
                        _p("count", "int", where="body", min=1, max=50, default=20, help="How many alerts")],
             "body": '{"size": {{count}}, "sort": [{"timestamp": "desc"}], "query": {"bool": {"filter": [{"range": {"timestamp": {"gte": "now-{{hours}}h"}}}, {"range": {"rule.level": {"gte": {{min_level}}}}}, {"wildcard": {"agent.name": "{{agent}}"}}]}}, "_source": ["timestamp", "rule.level", "rule.id", "rule.description", "rule.groups", "rule.mitre.tactic", "rule.mitre.technique", "agent.name", "agent.ip", "data.srcip", "data.srcuser", "data.dstuser", "location"]}',
             "description": "The alerts themselves, newest first: time, level, rule, MITRE tactic, machine, "
                            "source IP and user.",
             "example_questions": "Show me today's critical alerts.\nWhat triggered alerts on the front desk PC?\n"
                                  "What happened on FD-01 overnight?\nList the level 12 alerts this week."},
            {"name": "Failed logins", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 60,
             "params": [_p("hours", "int", where="body", min=1, max=720, default=24, help="How many hours back"),
                        _p("agent", "string", where="body", default="*",
                           help="Machine (agent) name; * for all, or a pattern like FD-*")],
             "body": '{"size": 0, "query": {"bool": {"filter": [{"range": {"timestamp": {"gte": "now-{{hours}}h"}}}, {"terms": {"rule.groups": ["authentication_failed", "authentication_failures", "invalid_login"]}}, {"wildcard": {"agent.name": "{{agent}}"}}]}}, "aggs": {"agents": {"terms": {"field": "agent.name", "size": 10}}, "sources": {"terms": {"field": "data.srcip", "size": 10}}, "users": {"terms": {"field": "data.dstuser", "size": 10}}, "rules": {"terms": {"field": "rule.description", "size": 10}}, "per_hour": {"date_histogram": {"field": "timestamp", "fixed_interval": "1h", "min_doc_count": 1}}}}',
             "description": "Failed logins: how many, on which machines, from which IPs, for which accounts, and "
                            "per hour (to spot brute force).",
             "example_questions": "Any failed logins today?\nIs anyone brute-forcing us?\n"
                                  "Which accounts had failed logins this week?\nWhere are the failed logins coming from?"},
        ],
    },
    {
        "key": "atera", "name": "Atera", "base_url": "https://api.atera.com/api/v3", "auth_type": "bearer",
        "description": "Atera IT tickets (the same calls the IT dashboard makes). Full ticket text, including "
                       "emails and comments: give it to the IT group only. Use a read-only Tickets token.",
        "operations": [
            {"name": "Tickets", "path": "/tickets/noComments", "cache_seconds": 60,
             "params": [_p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "Tickets with title, status, priority, customer, technician and dates.",
             "example_questions": "What IT tickets are open?\nWhich tickets are high priority?\n"
                                  "What's the IT ticket queue like?\nWho is working on which ticket?"},
            {"name": "Ticket", "path": "/tickets/{ticketId}", "cache_seconds": 60,
             "params": [_p("ticketId", "int", where="path", required=True, min=1, help="Ticket number")],
             "description": "One ticket in full.",
             "example_questions": "What's the status of ticket 4812?\nShow me ticket 1200."},
            {"name": "Ticket comments", "path": "/tickets/{ticketId}/comments", "cache_seconds": 60,
             "params": [_p("ticketId", "int", where="path", required=True, min=1, help="Ticket number")],
             "description": "Public and internal comments on one ticket.",
             "example_questions": "What are the comments on ticket 4812?\nWhat did the technician say on ticket 1200?"},
        ],
    },
]


def openapi_operations(spec_text: str) -> list[dict]:
    """GET operations described by an OpenAPI/Swagger JSON document, ready to
    add (path and query parameters only); [] if it isn't one."""
    try:
        spec = json.loads(spec_text)
    except ValueError:
        return []
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        return []
    types = {"integer": "int", "number": "string", "string": "string", "boolean": "enum"}
    out = []
    for path, item in (spec.get("paths") or {}).items():
        get = (item or {}).get("get") if isinstance(item, dict) else None
        if not isinstance(get, dict):
            continue
        params = []
        for p in (item.get("parameters") or []) + (get.get("parameters") or []):
            if not isinstance(p, dict) or p.get("in") not in ("query", "path") or "$ref" in p:
                continue
            schema = p.get("schema") or p
            kind = "enum" if schema.get("enum") else types.get(schema.get("type"), "string")
            if schema.get("format") == "date":
                kind = "date"
            spec_ = {"name": p.get("name", ""), "type": kind, "in": p["in"], "required": bool(p.get("required")),
                     "help": (p.get("description") or "")[:200]}
            if kind == "enum":
                spec_["options"] = [str(o) for o in schema.get("enum") or ["true", "false"]]
            for key in ("minimum", "maximum", "default"):
                if key in schema:
                    spec_[{"minimum": "min", "maximum": "max"}.get(key, key)] = schema[key]
            params.append(spec_)
        out.append({"name": (get.get("summary") or get.get("operationId") or path)[:80], "path": path,
                    "description": (get.get("description") or get.get("summary") or "")[:500], "params": params})
    return out


TEMPLATE_FORMAT = "mnemerynne-api-template"
_TEMPLATE_KEYS = {"format", "name", "description", "base_url", "auth_type", "auth_header", "auth_user",
                  "tls_check_hostname", "operations"}
# Set by hand on the API, never brought in by a file.
_TEMPLATE_REFUSED = {"secret", "token", "password", "credential", "ca_pem", "certificate", "ad_group",
                     "claude_enabled"}
_OP_KEYS = {"name", "description", "example_questions", "method", "path", "params", "body", "cache_seconds"}
MAX_TEMPLATE_OPERATIONS = 50


def parse_template(text: str) -> dict:
    """An uploaded API template, checked like the admin page checks each field,
    in the shape of PRESETS. Refuses credentials, certificates and access."""
    try:
        t = json.loads(text)
    except ValueError as exc:
        raise ApiError(f"The template isn't JSON ({exc}).") from None
    if not isinstance(t, dict) or t.get("format") != TEMPLATE_FORMAT:
        raise ApiError(f'A template is a JSON object with "format": "{TEMPLATE_FORMAT}".')
    refused = sorted(_TEMPLATE_REFUSED & set(t))
    if refused:
        raise ApiError(f"A template can't hold “{refused[0]}”: credentials, certificates, the AD group and "
                       "Claude's switch are set on the API after it's made.")
    unknown = sorted(set(t) - _TEMPLATE_KEYS)
    if unknown:
        raise ApiError(f"Unknown template field “{unknown[0]}”.")
    name = str(t.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ApiError("The template needs a name (at most 80 characters).")
    base_url = str(t.get("base_url") or "").strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")) or " " in base_url or len(base_url) > 500:
        raise ApiError("The template's base_url must start with https:// (or http://).")
    auth_type = t.get("auth_type", "none")
    if auth_type not in ("none", "bearer", "header", "basic"):
        raise ApiError("auth_type is one of none, bearer, header or basic.")
    auth_header = str(t.get("auth_header") or "")
    if auth_header and not all(c.isalnum() or c == "-" for c in auth_header):
        raise ApiError("A header name has only letters, digits and dashes.")
    ops = t.get("operations")
    if not isinstance(ops, list) or not ops or len(ops) > MAX_TEMPLATE_OPERATIONS:
        raise ApiError(f"The template needs 1 to {MAX_TEMPLATE_OPERATIONS} operations.")
    out_ops, names = [], set()
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            raise ApiError(f"Operation {i} isn't an object.")
        unknown = sorted(set(op) - _OP_KEYS)
        if unknown:
            raise ApiError(f"Operation {i} has an unknown field “{unknown[0]}”.")
        op_name = str(op.get("name") or "").strip()
        if not op_name or len(op_name) > 80 or op_name.lower() in names:
            raise ApiError(f"Operation {i} needs a name of its own (at most 80 characters).")
        names.add(op_name.lower())
        method = op.get("method", "GET")
        if method not in ("GET", "POST"):
            raise ApiError(f"“{op_name}”: the method is GET or POST.")
        # A body may be written as a JSON object, or as text when a {{name}} stands bare (e.g. [{{count}}]).
        body = op.get("body", "")
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        examples = op.get("example_questions", "")
        if isinstance(examples, list):
            examples = "\n".join(str(e).strip() for e in examples)
        params = op.get("params", [])
        cache = op.get("cache_seconds", 60)
        if not isinstance(params, list) or len(params) > 30 or not all(isinstance(p, dict) for p in params):
            raise ApiError(f"“{op_name}”: params is a list of at most 30 parameters.")
        if not isinstance(cache, int) or isinstance(cache, bool) or not 0 <= cache <= 86400:
            raise ApiError(f"“{op_name}”: cache_seconds is a whole number from 0 to 86400.")
        if not isinstance(body, str) or len(body) > 20000 or not isinstance(examples, str) or len(examples) > 4000:
            raise ApiError(f"“{op_name}”: the body or example questions are too long or the wrong kind.")
        try:
            check_specs(str(op.get("path") or ""), params, method, body)
        except ApiError as exc:
            raise ApiError(f"“{op_name}”: {exc}") from None
        out_ops.append({"name": op_name, "description": str(op.get("description") or "")[:2000],
                        "example_questions": examples, "method": method, "path": op["path"], "params": params,
                        "body": body if method == "POST" else "", "cache_seconds": cache})
    return {"name": name, "description": str(t.get("description") or "")[:20000], "base_url": base_url,
            "auth_type": auth_type, "auth_header": auth_header, "auth_user": str(t.get("auth_user") or "")[:200],
            "tls_check_hostname": 0 if t.get("tls_check_hostname") is False else 1, "operations": out_ops}


def uploaded_templates(conn: sqlite3.Connection) -> list[dict]:
    """Uploaded templates as presets, keyed "upload-<id>"."""
    out = []
    for r in conn.execute("SELECT id, template FROM api_templates ORDER BY name COLLATE NOCASE"):
        out.append({**json.loads(r["template"]), "key": f"upload-{r['id']}", "template_id": r["id"]})
    return out


def preset(key: str, conn: sqlite3.Connection | None = None) -> dict | None:
    """A built-in preset, or (given conn) an uploaded template."""
    found = next((p for p in PRESETS if p["key"] == key), None)
    if found is None and conn is not None:
        found = next((p for p in uploaded_templates(conn) if p["key"] == key), None)
    return found


def fetch_certificate(base_url: str) -> tuple[str, str]:
    """(PEM, SHA-256 fingerprint) of the certificate a server presents, for an
    admin to compare and trust (trust on first use)."""
    import hashlib
    import socket

    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname:
        raise ApiError("Only https addresses have a certificate.")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=parts.hostname) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError as exc:
        raise ApiError(f"Couldn't connect to {parts.hostname}: {exc}") from exc
    fingerprint = ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())
    return ssl.DER_cert_to_PEM_cert(der), fingerprint
