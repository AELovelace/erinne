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
  short cache, and every call recorded in api_calls. Calls to an API that runs
  one query at a time (apis.one_at_a_time) wait their turn; a reading call that
  gets HTTP 429 is tried once more.
- Actions: operations marked as changing data (close a ticket, acknowledge an
  alert; PUT, PATCH, DELETE or POST) never run from chat or Claude. They become
  proposals (proposals.propose_api) that the person applies; only then, or an
  admin's Test, calls them, never from the cache.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
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

from mnemerynne import fleetboard_presets, network_presets, wazuh_presets
from mnemerynne.config import Settings

TIMEOUT = 20.0
QUEUE_WAIT = 30.0                # a call to a one-at-a-time API waits this long for the one before it
RETRY_429_AFTER = 1.5            # seconds before trying a reading call again after HTTP 429
MAX_RESPONSE_BYTES = 256 * 1024
EXCERPT_CHARS = 20_000           # kept with each call, for the citation link
PARAM_TYPES = ("string", "int", "date", "enum", "text", "list")
TEXT_MAX = 2000   # a "text" parameter: free text (a ticket comment) in an action's body, always JSON-escaped
LIST_MAX = 50     # a "list" parameter: up to 50 short values (rule IDs, IPs), each checked like a string
LOGIN_TOKEN_SECONDS = 600        # a signed-in API's token is reused this long (Wazuh's last 900 s)
SHAPE_FORMATS = ("", "elasticsearch", "wazuh")
READ_METHODS = ("GET", "POST")                       # a reading POST has a fixed body (e.g. a search)
ACTION_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_METHODS = ("POST", "PUT", "PATCH")
ACTION_VIAS = ("apply", "test")                      # the only ways an action is called
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


# ------------------------------------------------------------- signed links
def view_key(settings: Settings, call_id: int) -> str | None:
    """The key in a call's link for Claude: it opens that one call's page without signing in, since
    Claude Desktop often opens links where the person isn't signed in. It shows no more than the tool
    result Claude already holds. None without a secret key (development), and the link needs sign-in."""
    if not settings.secret_key:
        return None
    return hmac.new(settings.secret_key.encode(), f"api-call:{call_id}".encode(), hashlib.sha256).hexdigest()[:32]


def view_link(settings: Settings, call_id: int) -> str:
    """'/apis/calls/<id>', with its view key when there is one."""
    key = view_key(settings, call_id)
    return f"/apis/calls/{call_id}" + (f"?k={key}" if key else "")


def check_view_key(settings: Settings, call_id: int, key: str) -> bool:
    expected = view_key(settings, call_id)
    return bool(expected and key) and hmac.compare_digest(expected, key)


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
        "SELECT o.*, a.name AS api_name, a.ad_group, a.claude_enabled, a.one_at_a_time FROM api_operations o "
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


def check_specs(path: str, specs: list[dict], method: str, body: str, action: bool = False) -> None:
    """Admin input: refuse operations that couldn't be called safely."""
    if method not in (ACTION_METHODS if action else READ_METHODS):
        raise ApiError(f"{method} changes data: mark the operation as an action (the person applies it)."
                       if method in ACTION_METHODS else f"“{method}” isn't a method operations can use.")
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
        if s.get("in") == "body" and method not in BODY_METHODS:
            raise ApiError(f"Parameter “{name}” is in the body, but {method} operations have none.")
        if s.get("type") == "enum" and not s.get("options"):
            raise ApiError(f"Parameter “{name}” is a list; give its options.")
        if s.get("type") == "text" and not (action and s.get("in") == "body"):
            raise ApiError(f"Parameter “{name}” is free text: only actions take it, and only in the body.")
        if s.get("type") == "list" and s.get("in") == "path":
            raise ApiError(f"Parameter “{name}” is a list: lists go in the query or the body, not the path.")
    route, _, fixed_query = path.partition("?")            # a fixed query (?select=id,name) is sent as written
    if _PLACEHOLDER.search(fixed_query) or "#" in path:
        raise ApiError("A path's fixed query (after ?) can't hold {placeholders}; make them query parameters.")
    full_path, path = path, route
    for placeholder in _PLACEHOLDER.findall(path):
        if not any(s["name"] == placeholder and s.get("in") == "path" for s in specs):
            raise ApiError(f"The path uses {{{placeholder}}} but there's no path parameter by that name.")
    if method == "POST" or (method in BODY_METHODS and body):
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
            raise ApiError(f"A {method} operation needs a JSON body (with {{{{name}}}} where body parameters go).") \
                from exc
        _check_conditions(json.loads(render_body(body, samples)), in_body)   # every {"$if": name} names a body parameter
        fill_body(body, samples)                                             # filled in, and with nothing filled in
        fill_body(body, {})
    elif body:
        raise ApiError(f"{method} operations have no body.")
    elif any(s.get("in") == "body" for s in specs):
        raise ApiError("Body parameters need a body to go in ({{name}} where each goes).")
    if not path.startswith("/") or path.startswith("//") or ".." in full_path or "://" in full_path \
            or "\\" in full_path:
        raise ApiError("The path must start with / and stay under the API's address.")


_DROP = object()   # a {"$if": ...} whose parameter is empty: left out of the body


def _is_set(value) -> bool:
    return value not in (None, "", [])


def _check_conditions(node, in_body: set) -> None:
    """Refuse a {"$if": name, "then": ..., "else": ...} that names no body parameter or has other keys."""
    if isinstance(node, list):
        for item in node:
            _check_conditions(item, in_body)
    elif isinstance(node, dict):
        if "$if" in node:
            if set(node) - {"$if", "then", "else"} or "then" not in node:
                raise ApiError('A condition is {"$if": "name", "then": …} (and optionally "else": …), nothing more.')
            if node["$if"] not in in_body:
                raise ApiError(f"The body's condition {{\"$if\": \"{node['$if']}\"}} names no body parameter.")
        for value in node.values():
            _check_conditions(value, in_body)


def _resolve(node, values: dict):
    """The body with each {"$if": name, "then": X, "else": Y} replaced by X when that parameter has a
    value, Y (or nothing at all) when it doesn't: optional filters, e.g. "only these rule IDs"."""
    if isinstance(node, list):
        return [r for r in (_resolve(item, values) for item in node) if r is not _DROP]
    if isinstance(node, dict):
        if "$if" in node:
            branch = node["then"] if _is_set(values.get(node["$if"])) else node.get("else", _DROP)
            return branch if branch is _DROP else _resolve(branch, values)
        out = {}
        for key, value in node.items():
            resolved = _resolve(value, values)
            if resolved is not _DROP:
                out[key] = resolved
        return out
    return node


def fill_body(template: str, values: dict) -> str:
    """The body as it's sent: placeholders filled in (render_body), then its conditions resolved."""
    rendered = render_body(template, values)
    if '"$if"' not in template:
        return rendered                                    # no conditions: exactly as rendered
    body = _resolve(json.loads(rendered), values)
    return json.dumps({} if body is _DROP else body, ensure_ascii=False)


def _sample(spec: dict):
    """A valid value of the parameter's type, to check that the body is JSON once filled in."""
    kind = spec.get("type")
    if kind == "int":
        return int(spec.get("min") if spec.get("min") is not None else 1)
    if kind == "enum":
        return str((spec.get("options") or ["x"])[0])
    if kind == "list":
        return ["x"]
    return "2026-01-01" if kind == "date" else "x"


def render_body(template: str, values: dict) -> str:
    """The POST body with each {{name}} filled in: escaped text inside a JSON
    string ("now-{{hours}}h"), a JSON value elsewhere ({"size": {{count}}}).
    A parameter without a value becomes "" or null. A list is a JSON array
    outside a string and its values joined with commas inside one."""
    out, pos, in_string, escaped = [], 0, False, False
    while pos < len(template):
        m = _BODY_PLACEHOLDER.match(template, pos)
        if m:
            value = values.get(m.group(1))
            if in_string:
                text = ",".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
                out.append(json.dumps(text)[1:-1])                 # escaped, without its own quotes
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
        if kind == "list":
            value = _list_value(name, s, value)                # [] when nothing was given
            if not value and s.get("default") not in (None, "", []):
                value = _list_value(name, s, s["default"])
            if not value:
                if s.get("required"):
                    raise ApiError(f"“{name}” is needed.")
                continue
            out[name] = value
            continue
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
        elif kind == "text":
            value = str(value).strip()
            if len(value) > TEXT_MAX or any(ord(c) < 32 and c not in "\n\t" for c in value):
                raise ApiError(f"“{name}” must be plain text of at most {TEXT_MAX} characters.")
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


def _list_value(name: str, spec: dict, value) -> list[str]:
    """A list parameter's values: a JSON list or comma-separated text, each a short safe string."""
    if value in (None, ""):
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    items = [str(i).strip() for i in items if str(i).strip()]
    if len(items) > LIST_MAX:
        raise ApiError(f"“{name}” takes at most {LIST_MAX} values.")
    pattern = _SAFE_BODY_STRING if spec.get("in") == "body" else _SAFE_STRING
    if not all(pattern.match(i) and "," not in i for i in items):
        raise ApiError(f"“{name}” contains characters that aren't allowed.")
    return items


def build_request(api, op, params: dict) -> tuple[str, str, dict, str | None]:
    """(method, url, query, body) for a validated call. A fixed query written in the operation's path
    (?select=id,name) is kept, with the query parameters added after it; lists go as a,b,c."""
    from urllib.parse import parse_qsl

    specs = {s["name"]: s for s in param_specs(op)}
    route, _, fixed = op["path"].partition("?")
    path = _PLACEHOLDER.sub(lambda m: quote(str(params[m.group(1)]), safe=""), route)
    url = api["base_url"].rstrip("/") + path
    base, built = urlsplit(api["base_url"]), urlsplit(url)
    if (base.scheme, base.netloc) != (built.scheme, built.netloc) or built.scheme not in ("https", "http"):
        raise ApiError("The operation's address leaves the API.")
    query = dict(parse_qsl(fixed, keep_blank_values=True))
    query.update({k: ",".join(v) if isinstance(v, list) else v for k, v in params.items()
                  if specs[k].get("in", "query") == "query"})
    if op["method"] not in BODY_METHODS:
        return op["method"], url, query, None
    body = {k: v for k, v in params.items() if specs[k].get("in") == "body"}
    return op["method"], url, query, fill_body(op["body"], body) if op["body"] else None


# ------------------------------------------------------------------- shaping
# An operation's shape boils its answer down on the server, before it is cached, logged, shown or handed to
# the model: Elasticsearch boilerplate (_shards, _index, sort keys) goes, aggregations become plain lists,
# and secrets (an operation with "redact") never leave this module.
_SHAPE_KEYS = {"format", "keep", "rename", "redact", "flatten", "single", "merge"}
_REDACT_KEY = re.compile(r"(?i)(pass(wd|word)?$|password|secret|token|api_?key|hook_url|webhook|credential"
                         r"|private_key|^key$)")
_REDACT_TEXT = (
    # "Random password chosen for agent authentication: 3b7c…", "api_key=…", "token: …"
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b([^:=\n]{0,40}?[:=]\s*)([^\s,;'\"]+)"),
     r"\1\2[redacted]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"(?i)https?://[^\s\"'<>]*(hooks?|webhook)[^\s\"'<>]*"), "[redacted webhook]"),
)
_HARMLESS = {"", "yes", "no", "true", "false", "on", "off", "none", "disabled", "enabled"}


def check_shape(shape) -> str:
    """Admin or template input: the shape as tidy JSON text ('' for none), or ApiError."""
    if shape in (None, "", {}):
        return ""
    if isinstance(shape, str):
        try:
            shape = json.loads(shape)
        except ValueError:
            raise ApiError("The response shape is JSON, e.g. {\"format\": \"elasticsearch\"}.") from None
    if not isinstance(shape, dict) or set(shape) - _SHAPE_KEYS:
        raise ApiError(f"A response shape is an object with only: {', '.join(sorted(_SHAPE_KEYS))}.")
    if shape.get("format", "") not in SHAPE_FORMATS:
        raise ApiError("The shape's format is elasticsearch, wazuh, or left out.")
    keep, rename = shape.get("keep", []), shape.get("rename", {})
    if not isinstance(keep, list) or len(keep) > 60 or not all(isinstance(k, str) and 0 < len(k) <= 120 for k in keep):
        raise ApiError("The shape's keep is a list of at most 60 field paths, like \"rule.level\".")
    if not isinstance(rename, dict) or len(rename) > 60 or \
            not all(isinstance(k, str) and isinstance(v, str) and v for k, v in rename.items()):
        raise ApiError("The shape's rename maps field paths to new names, like {\"rule.level\": \"level\"}.")
    for flag in ("redact", "flatten"):
        if not isinstance(shape.get(flag, True), bool):
            raise ApiError(f"The shape's {flag} is true or false.")
    single = shape.get("single", [])                              # sub-aggregations that are always one value
    if not isinstance(single, list) or len(single) > 20 or \
            not all(isinstance(k, str) and 0 < len(k) <= 120 for k in single):
        raise ApiError("The shape's single is a list of at most 20 aggregation names, like \"description\".")
    merge = shape.get("merge", {})                                # {"sources": ["src_ip", "win_ip"]}: lists folded in
    if not isinstance(merge, dict) or len(merge) > 20 or not all(
            isinstance(k, str) and k and isinstance(v, list) and 0 < len(v) <= 10
            and all(isinstance(n, str) and n for n in v) for k, v in merge.items()):
        raise ApiError("The shape's merge maps a list's name to the aggregations folded into it, "
                       "like {\"sources\": [\"src_ip\", \"win_ip\"]}.")
    return json.dumps(shape, ensure_ascii=False, sort_keys=True)


def redact_text(text: str) -> str:
    """Text with passwords, tokens, keys and webhook addresses blanked out."""
    for pattern, replacement in _REDACT_TEXT:
        text = pattern.sub(replacement, text)
    return text


def _redact(value):
    """Every secret-looking field blanked (yes/no switches like use_password stay), and secrets in text."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if _REDACT_KEY.search(str(k)) and _secretish(v) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return redact_text(value) if isinstance(value, str) else value


def _secretish(value) -> bool:
    if isinstance(value, (dict, list)):
        return bool(value)
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and \
        str(value).strip().lower() not in _HARMLESS


def _flat(value, prefix: str = "") -> dict:
    """{"rule": {"level": 3}} → {"rule.level": 3}; lists stay lists."""
    out: dict = {}
    for key, item in (value or {}).items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict) and item:
            out.update(_flat(item, path))
        else:
            out[path] = item
    return out


def _pick(value, parts: list[str]):
    """The value at a dotted path, mapped over lists on the way (states.name → every state's name)."""
    if not parts:
        return value
    if isinstance(value, list):
        picked = [_pick(v, parts) for v in value]
        return [p for p in picked if p is not _DROP] or _DROP
    if isinstance(value, dict) and parts[0] in value:
        return _pick(value[parts[0]], parts[1:])
    return _DROP


def _row(item, shape: dict):
    """One row: kept fields (all if keep is empty), flattened, renamed."""
    if not isinstance(item, dict):
        return item
    if shape.get("keep"):
        picked = {p: _pick(item, p.split(".")) for p in shape["keep"]}
        item = {p: v for p, v in picked.items() if v is not _DROP}
    if shape.get("flatten", True):
        item = _flat(item)
    rename = shape.get("rename") or {}
    return {rename.get(k, k): v for k, v in item.items()}


def _agg(value, shape: dict):
    """One Elasticsearch aggregation as plain data: buckets → [{key, count, …sub}], a metric → its value."""
    if not isinstance(value, dict):
        return value
    if "buckets" in value:
        buckets = value["buckets"]
        if isinstance(buckets, dict):                               # a "filters" aggregation: named buckets
            buckets = [{"key": k, **v} for k, v in buckets.items()]
        return [_bucket(b, shape) for b in buckets]
    if isinstance(value.get("hits"), dict):                         # top_hits
        return [_row(h.get("_source") or {}, shape) for h in value["hits"].get("hits") or []]
    if "value" in value:
        got = value.get("value_as_string", value["value"])
        return int(got) if isinstance(got, float) and got.is_integer() else got   # a max level: 3, not 3.0
    if "doc_count" in value:                                        # a single-bucket filter
        sub = _subaggs(value, shape)
        return {"count": value["doc_count"], **sub} if sub else value["doc_count"]
    return value


def _subaggs(bucket: dict, shape: dict) -> dict:
    """A bucket's sub-aggregations as {name: plain value}, each name always the same kind of value."""
    single = shape.get("single")                                   # None: an older shape, guessed per answer
    out = {}
    for name, sub in bucket.items():
        if not isinstance(sub, dict) or name in ("key", "meta"):  # OpenSearch echoes "meta": {}; it's not a result
            continue
        got = _agg(sub, shape)
        if single is not None:
            if name in single and isinstance(got, list):          # named in the shape: just the top key, or null
                got = got[0].get("key") if got and isinstance(got[0], dict) else None
        # An older shape: a one-bucket terms list that covers the whole parent is just its key.
        elif isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict) \
                and set(got[0]) == {"key", "count"} and got[0]["count"] == bucket.get("doc_count"):
            got = got[0]["key"]
        out[name] = got
    for target, names in (shape.get("merge") or {}).items():       # one top list from several fields
        lists = [out.pop(n, None) for n in names] + [out.get(target)]
        lists = [x for x in lists if isinstance(x, list)]
        if lists:
            out[target] = _merge_tops(lists)
    return out


def _merge_tops(lists: list[list]) -> list:
    """Several top lists of {key, count} as one: counts added by key, biggest first, as long as the longest."""
    counts: dict = {}
    for items in lists:
        for item in items:
            if isinstance(item, dict) and "key" in item:
                counts[item["key"]] = counts.get(item["key"], 0) + (item.get("count") or 0)
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:max(len(x) for x in lists)]
    return [{"key": k, "count": n} for k, n in top]


def _bucket(bucket: dict, shape: dict) -> dict:
    return {"key": bucket.get("key_as_string", bucket.get("key")), "count": bucket.get("doc_count"),
            **_subaggs(bucket, shape)}


def _shape_elasticsearch(data, shape: dict):
    if isinstance(data, list):                                     # a _cat answer (?format=json): rows as they are
        return {"total": len(data), "rows": [_row(r, shape) for r in data]}
    if not isinstance(data, dict) or not ("hits" in data or "aggregations" in data):
        return data
    out: dict = {}
    hits = data.get("hits") or {}
    total = hits.get("total")
    if isinstance(total, dict):
        out["total"] = total.get("value")
        if total.get("relation") == "gte":
            out["total_is_at_least"] = True                      # tracking was capped: "10,000 or more"
    elif isinstance(total, int):
        out["total"] = total
    if hits.get("hits") or not data.get("aggregations"):
        out["rows"] = [_row(h.get("_source") or {}, shape) for h in hits.get("hits") or []]
    for name, agg in (data.get("aggregations") or {}).items():
        out[name] = _agg(agg, shape)
        if isinstance(agg, dict) and agg.get("sum_other_doc_count"):
            out[name + "_others"] = agg["sum_other_doc_count"]   # how many more fell outside the top list
    if data.get("timed_out"):
        out["timed_out"] = True
    if (data.get("_shards") or {}).get("failed"):
        out["shards_failed"] = data["_shards"]["failed"]
    return out


def _shape_wazuh(data, shape: dict):
    """The Wazuh manager API's {data: {affected_items, total_affected_items, failed_items}} as {total, rows}."""
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        return data
    if "affected_items" not in inner:
        return _row(inner, shape)                                # one object (an agent's configuration)
    out: dict = {"total": inner.get("total_affected_items", len(inner["affected_items"])),
                 "rows": [_row(i, shape) for i in inner["affected_items"]]}
    if inner.get("total_failed_items"):
        out["failed"] = inner.get("failed_items")
        if data.get("message"):
            out["message"] = data["message"]
    return out


def apply_shape(data, shape_text: str):
    """The response boiled down by the operation's shape ('' = unchanged)."""
    if not shape_text:
        return data
    shape = json.loads(shape_text)
    if isinstance(data, str):                                    # not JSON, or cut off at 256 KB
        return redact_text(data) if shape.get("redact") else data
    fmt = shape.get("format", "")
    if fmt == "elasticsearch":
        data = _shape_elasticsearch(data, shape)
    elif fmt == "wazuh":
        data = _shape_wazuh(data, shape)
    elif shape.get("keep") or shape.get("rename"):
        found = _main_list(data)
        if found:
            data = _put(data, found[0], [_row(r, shape) for r in found[1]])
        elif isinstance(data, dict):
            data = _row(data, shape)
    return _redact(data) if shape.get("redact") else data


def _col(row, name: str, default=""):
    """A column that older rows (or hand-made ones in tests) may not have."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


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
_turns: dict[int, threading.Lock] = {}     # api id -> held while a call to a one-at-a-time API runs
_turns_lock = threading.Lock()


def _turn(api) -> threading.Lock | None:
    """The API's lock, held (for up to QUEUE_WAIT) if it runs one query at a time; None otherwise."""
    if not api["one_at_a_time"]:
        return None
    with _turns_lock:
        lock = _turns.setdefault(api["id"], threading.Lock())
    if not lock.acquire(timeout=QUEUE_WAIT):
        raise ApiError(f"{api['name']} runs one query at a time and is still busy; try again in a moment")
    return lock


def _ssl(api) -> ssl.SSLContext | bool:
    if not api["ca_pem"].strip():
        return True
    ctx = ssl.create_default_context(cadata=api["ca_pem"])
    # Python 3.13+ turns on X.509 strict mode, which refuses a pinned self-signed certificate
    # without basicConstraints (UniFi OS Server, many appliances: "invalid CA certificate").
    # The pinned certificate is the only one trusted, so its RFC 5280 tidiness adds nothing here.
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    if not api["tls_check_hostname"]:
        # Only the pinned certificate is trusted, so the name it carries
        # matters less (e.g. a server reached by IP).
        ctx.check_hostname = False
    return ctx


_tokens: dict[int, tuple[float, str, str]] = {}   # api id -> (good until, what it was made from, token)
_tokens_lock = threading.Lock()
_TOKEN_KEYS = ("token", "access_token", "jwt", "id_token")


def check_login_path(path: str) -> str:
    """Admin or template input: '' or a path under the API's address, like /security/user/authenticate."""
    path = (path or "").strip()
    if path and (not re.fullmatch(r"/[\w.\-/]{0,200}(\?[\w.\-=&]{0,100})?", path) or ".." in path or "//" in path):
        raise ApiError("The sign-in path starts with / and stays under the API's address, "
                       "like /security/user/authenticate.")
    return path


def _login_token(settings: Settings, api, secret: str, *, fresh: bool = False) -> str:
    """A token from the API's sign-in path (user name + password, HTTP Basic, POST), reused for
    LOGIN_TOKEN_SECONDS or until the API refuses it."""
    made_from = hashlib.sha256(f"{api['base_url']}|{api['auth_user']}|{secret}|{api['auth_login_path']}"
                               .encode()).hexdigest()
    with _tokens_lock:
        hit = _tokens.get(api["id"])
    if hit and not fresh and hit[0] > time.monotonic() and hit[1] == made_from:
        return hit[2]
    url = api["base_url"].rstrip("/") + api["auth_login_path"]
    basic = "Basic " + base64.b64encode(f"{api['auth_user']}:{secret}".encode()).decode()
    status, _ctype, raw, _cut = _fetch(api, "POST", url, {}, None,
                                       {"Accept": "application/json", "User-Agent": "Mnemerynne", "Authorization": basic})
    if status in (401, 403):
        raise ApiError(f"{api['name']} refused the user name and password at sign-in (HTTP {status}); an admin "
                       "should check them on the APIs page")
    if not 200 <= status < 300:
        raise ApiError(f"{api['name']}'s sign-in answered HTTP {status}")
    text = raw.decode("utf-8", errors="replace").strip()
    token = None
    try:
        body = json.loads(text)
        for holder in (body.get("data") if isinstance(body, dict) else None, body):
            if isinstance(holder, dict):
                token = next((holder[k] for k in _TOKEN_KEYS if isinstance(holder.get(k), str)), None)
                if token:
                    break
    except ValueError:
        token = text if re.fullmatch(r"[\w.\-~+/=]{10,8000}", text) else None   # ?raw=true: the token alone
    if not token:
        raise ApiError(f"{api['name']}'s sign-in answered without a token")
    with _tokens_lock:
        _tokens[api["id"]] = (time.monotonic() + LOGIN_TOKEN_SECONDS, made_from, token)
    return token


def _headers(settings: Settings, api, *, fresh: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Mnemerynne"}
    if api["auth_type"] == "none":
        return headers
    secret = decrypt_secret(settings, api["secret_enc"])
    if secret is None:
        raise ApiError(f"{api['name']} has no usable credential; an admin needs to enter it on the APIs page.")
    if api["auth_type"] == "basic" and _col(api, "auth_login_path"):
        headers["Authorization"] = f"Bearer {_login_token(settings, api, secret, fresh=fresh)}"
    elif api["auth_type"] == "bearer":
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
    return f"the API answered HTTP {status}" + (f": {_detail(text)}" if _detail(text) else "")


_DETAIL_KEYS = ("error", "message", "Message", "detail", "title", "error_description", "errors")


def _detail(text: str) -> str:
    """What the API said went wrong: the usual message field of a JSON error (one level down too),
    or a short plain-text body; "" if there's nothing readable."""
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return "" if not text or text.startswith("<") or len(text) > 300 else text[:200]
    for _ in range(2):
        if not isinstance(data, dict):
            break
        found = next((data[k] for k in _DETAIL_KEYS if data.get(k)), None)
        if found is None:
            return ""
        if isinstance(found, (dict, list)):
            data = found[0] if isinstance(found, list) and found else found
            continue
        return str(found)[:200]
    return str(data)[:200] if isinstance(data, str) else ""


def call(conn: sqlite3.Connection, settings: Settings, op_id: int, params: dict | None, *,
         user_id: int | None, via: str) -> CallResult:
    """Run one operation (after the caller checked the user may) and record it."""
    op = conn.execute("SELECT * FROM api_operations WHERE id = ?", (op_id,)).fetchone()
    if op is None:
        raise ApiError("That API operation no longer exists.")
    api = conn.execute("SELECT * FROM apis WHERE id = ?", (op["api_id"],)).fetchone()
    if op["action"] and via not in ACTION_VIAS:
        raise ApiError(f"“{op['name']}” changes data in {api['name']}: it runs only when the person applies it.")
    params = validate_params(param_specs(op), params)
    key = (op_id, json.dumps(params, sort_keys=True))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    started = time.perf_counter()
    if hit and hit[0] > now and via != "test" and not op["action"]:
        result = CallResult(0, True, api["name"], op["name"], "", hit[1].status_code, hit[1].data,
                            cached=True, truncated=hit[1].truncated)
        raw_len = None
    else:
        result = CallResult(0, False, api["name"], op["name"], "")
        raw_len = None
        turn = None
        failed_text = None
        try:
            method, url, query, body = build_request(api, op, params)
            headers = _headers(settings, api)
            turn = _turn(api)
            status, ctype, raw, truncated = _fetch(api, method, url, query, body, headers)
            if status == 401 and api["auth_type"] == "basic" and _col(api, "auth_login_path"):
                headers = _headers(settings, api, fresh=True)      # the token ran out early: sign in again, once
                status, ctype, raw, truncated = _fetch(api, method, url, query, body, headers)
            if status == 429 and not op["action"]:
                time.sleep(RETRY_429_AFTER)
                status, ctype, raw, truncated = _fetch(api, method, url, query, body, headers)
            raw_len = len(raw)
            text = raw.decode("utf-8", errors="replace")
            result.status_code, result.truncated = status, truncated
            shape = _col(op, "shape")
            if 200 <= status < 300:
                result.ok = True
                try:
                    result.data = json.loads(text) if not truncated else text
                except ValueError:
                    result.data = text
                result.data = apply_shape(result.data, shape)      # before it's cached, logged or shown
            else:
                if shape and json.loads(shape).get("redact"):
                    text = redact_text(text)
                result.error = _explain(status, text)
                failed_text = text
        except ApiError as exc:
            result.error = str(exc)
        except httpx.TimeoutException:
            result.error = f"{api['name']} didn't answer within {int(TIMEOUT)} seconds"
        except (httpx.HTTPError, ssl.SSLError, OSError) as exc:
            message = str(exc)
            result.error = ("couldn't verify the API's certificate; check the trusted certificate on the APIs page"
                            if "CERTIFICATE" in message.upper() else f"couldn't reach {api['name']}: {message[:200]}")
        finally:
            if turn is not None:
                turn.release()
        if result.ok and op["cache_seconds"] > 0 and not op["action"]:
            with _cache_lock:
                _cache[key] = (now + op["cache_seconds"], result)
    excerpt = None
    if result.ok:
        excerpt = (json.dumps(result.data, indent=1, ensure_ascii=False) if not isinstance(result.data, str)
                   else result.data)[:EXCERPT_CHARS]
    elif not result.cached and failed_text:
        excerpt = failed_text[:EXCERPT_CHARS]          # what the API said, for the call's page
    from mnemerynne import call_logs

    with call_logs.on(conn) as logs:                   # logs.db (Phase 8): committed even if the caller rolls back
        cur = logs.execute(
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
    with _tokens_lock:
        _tokens.clear()                     # a changed credential or address signs in afresh


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


DIGEST_ITEMS = (500, 200, 100, 50, 20, 10, 5, 3, 1)   # list lengths to try, longest first


def digest(data, max_chars: int) -> str:
    """The response as compact text that fits the model's context."""
    if isinstance(data, str):
        return data[:max_chars]
    for items in DIGEST_ITEMS:
        text = json.dumps(_trim(data, items), ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text
    return text[:max_chars] + "…"


def _main_list(data, path: tuple = ()) -> tuple[tuple, list] | None:
    """The longest list in a response (at most three levels down) and the keys that lead to it."""
    best = (path, data) if isinstance(data, list) else None
    if isinstance(data, dict) and len(path) < 3:
        for key, value in data.items():
            found = _main_list(value, path + (key,))
            if found and (best is None or len(found[1]) > len(best[1])):
                best = found
    return best


def _put(data, path: tuple, value):
    """A copy of data with the value at path replaced."""
    if not path:
        return value
    return {**data, path[0]: _put(data[path[0]], path[1:], value)}


def page(data, offset: int, max_chars: int, find: str | None = None) -> tuple[str, dict | None]:
    """The response as compact text, its longest list from row `offset` on, as many rows as fit;
    and where that page is ({list, total, offset, returned, next_offset}), or None if it has no list.
    With `find`, only the rows whose text mentions it (any field, ignoring case) are kept and
    `total` counts those; `matched_of` says how many rows the API returned."""
    found = None if isinstance(data, str) else _main_list(data)
    if not found or not found[1]:
        return digest(data, max_chars), None
    path, rows = found
    matched_of = None
    if find and find.strip():
        needle = find.strip().lower()
        matched_of, rows = len(rows), [r for r in rows if needle in json.dumps(r, ensure_ascii=False).lower()]
    offset = max(0, min(int(offset), len(rows)))
    text = ""
    for items in DIGEST_ITEMS:
        chunk = rows[offset:offset + items]
        text = json.dumps(_trim(_put(data, path, chunk), items), ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            break
    else:
        chunk, text = rows[offset:offset + 1], text[:max_chars] + "…"
    info = {"list": ".".join(map(str, path)) or None, "total": len(rows), "offset": offset, "returned": len(chunk)}
    if matched_of is not None:
        info["matched_of"] = matched_of
    if offset + len(chunk) < len(rows):
        info["next_offset"] = offset + len(chunk)
    return text, info


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
        if s["type"] == "list":
            detail += " of values, comma-separated"
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
        elif s["type"] == "list":
            prop = {"type": "array", "items": {"type": "string"}, "maxItems": LIST_MAX}
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
    *wazuh_presets.PRESETS,          # Wazuh indexer, Wazuh manager, Wazuh manager actions (wazuh_presets.py)
    {
        "key": "atera", "name": "Atera", "base_url": "https://api.atera.com/api/v3", "auth_type": "bearer",
        "description": "Atera IT tickets and alerts (the same calls the IT dashboard makes). Full ticket text, "
                       "including emails and comments: give it to the IT group only. The reading operations need a "
                       "read-only token; the changes (create a ticket, change its status or priority, add an "
                       "internal note, resolve an alert) are proposals the person runs, and need a token that may "
                       "write. Delete the change operations here if this entry should stay read-only, and edit "
                       "Add internal note's body to the technician the notes should come from. Contact by email "
                       "returns names, emails and phone numbers: leave Claude's switch off if that mustn't go to "
                       "Anthropic.",
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
            {"name": "Ticket statuses", "path": "/tickets/statuses", "cache_seconds": 300,
             "description": "The ticket statuses this account uses (e.g. Open, Pending, Resolved, Closed): the exact "
                            "names to filter tickets by.",
             "example_questions": "What ticket statuses are there?\nWhich statuses can a ticket have in Atera?"},
            {"name": "Tickets by status", "path": "/tickets/noComments", "cache_seconds": 60,
             "params": [_p("ticketStatus", "string", required=True,
                           help="A status name exactly as Atera has it (see Ticket statuses), e.g. Open"),
                        _p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "Tickets in one status (Open, Pending, Resolved, Closed, or your own), with title, "
                            "priority, customer, technician and dates; 50 a page.",
             "example_questions": "Which tickets are pending?\nList the open tickets.\n"
                                  "What tickets were resolved?\nShow me the closed tickets."},
            {"name": "Customer's tickets", "path": "/tickets/noComments", "cache_seconds": 60,
             "params": [_p("customerId", "int", required=True, min=1,
                           help="The customer's CustomerID (see Customers)"),
                        _p("ticketStatus", "string", help="Only this status, e.g. Open; empty for all"),
                        _p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "One customer's (site's) tickets, optionally in one status: title, status, priority, "
                            "technician and dates. Find the CustomerID with Customers first.",
             "example_questions": "What tickets does the Comfort Inn have open?\nShow me Red Lion Pasco's tickets.\n"
                                  "Which tickets are there for customer 1200?\nAny tickets for the Kennewick site?"},
            {"name": "Customers", "path": "/customers", "cache_seconds": 300,
             "params": [_p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "The customers (sites) in Atera with their CustomerID, name, domain and address, to "
                            "find the ID for Customer's tickets.",
             "example_questions": "What customers are in Atera?\nWhat's the Atera customer ID for the Comfort Inn?\n"
                                  "List the Atera sites."},
            {"name": "Tickets modified since", "path": "/tickets/lastmodified", "cache_seconds": 60,
             "params": [_p("date", "date", required=True, help="Tickets changed on or after this date (UTC)"),
                        _p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "Tickets changed on or after a date (Atera's last-modified list): title, status, "
                            "priority, customer, technician and dates.",
             "example_questions": "Which tickets changed this week?\nWhat tickets were updated since Monday?\n"
                                  "What moved on the ticket queue since 2026-10-20?"},
            {"name": "Alerts", "path": "/alerts", "cache_seconds": 60,
             "params": [_p("alertStatus", "enum", options=["Open", "Resolved", "Snoozed"], default="Open",
                           help="Which alerts"),
                        _p("page", "int", min=1, max=50, default=1),
                        _p("itemsInPage", "int", min=1, max=50, default=50)],
             "description": "Atera alerts by status (Open by default): alert ID, title, severity, device, customer, "
                            "when it was raised and its ticket if any.",
             "example_questions": "What alerts are open in Atera?\nAre there any open alerts?\n"
                                  "Which alerts are snoozed?\nShow me the Atera alerts."},
            {"name": "Alert", "path": "/alerts/{alertId}", "cache_seconds": 60,
             "params": [_p("alertId", "int", where="path", required=True, min=1, help="Alert ID")],
             "description": "One Atera alert in full: title, message, severity, device, customer, status and dates.",
             "example_questions": "What is alert 5531 about?\nShow me alert 5531.\nIs alert 5531 still open?"},
            {"name": "Contact by email", "path": "/contacts", "cache_seconds": 300,
             "params": [_p("Email", "string", required=True, help="The person's email address, exactly as in Atera")],
             "description": "The Atera contact (end user) with a given email: EndUserID, name, customer. Look it up "
                            "before proposing Create ticket, which needs the EndUserID. Returns personal data.",
             "example_questions": "What's the Atera contact for jane@example.com?\n"
                                  "Who is the end user with the email jane@example.com in Atera?"},
            # Changes: proposed by Erynne or Claude, run only when the person presses Run it.
            {"name": "Create ticket", "action": True, "method": "POST", "path": "/tickets", "cache_seconds": 0,
             "body": '{"TicketTitle": "{{title}}", "Description": "{{description}}", "EndUserID": {{endUserId}}, '
                     '"TicketPriority": "{{priority}}", "TicketType": "{{type}}", "TicketImpact": "{{impact}}"}',
             "params": [_p("title", "text", where="body", required=True, help="A short, one-line title"),
                        _p("description", "text", where="body", required=True,
                           help="What's wrong, where it was seen and what should happen; include the evidence"),
                        _p("endUserId", "int", where="body", required=True, min=1,
                           help="The contact's EndUserID from Contact by email (who the ticket is for)"),
                        _p("priority", "enum", where="body", default="Medium", options=["Low", "Medium", "High", "Critical"],
                           help="The priority"),
                        _p("type", "enum", where="body", default="Incident",
                           options=["Incident", "Problem", "Request", "Change"],
                           help="Incident for something broken or a security finding, Request for something wanted"),
                        _p("impact", "enum", where="body", default="Minor",
                           options=["NoImpact", "Minor", "Major", "ServerIssue", "SiteDown", "Crisis"],
                           help="How much it affects work")],
             "description": "Open a new ticket for an existing Atera contact: title, description, priority, type and "
                            "impact. Proposed: the person runs it. Needs the contact's EndUserID (Contact by email). "
                            "Never makes a contact."},
            {"name": "Change ticket status", "action": True, "method": "PUT", "path": "/tickets/{ticketId}",
             "cache_seconds": 0, "body": '{"TicketStatus": "{{status}}"}',
             "params": [_p("ticketId", "int", where="path", required=True, min=1, help="Ticket number"),
                        _p("status", "string", where="body", required=True,
                           help="A status name from Ticket statuses, exactly as listed, e.g. Resolved")],
             "description": "Set one ticket's status, e.g. to Resolved or Closed. Proposed: the person runs it."},
            {"name": "Change ticket priority", "action": True, "method": "PUT", "path": "/tickets/{ticketId}",
             "cache_seconds": 0, "body": '{"TicketPriority": "{{priority}}"}',
             "params": [_p("ticketId", "int", where="path", required=True, min=1, help="Ticket number"),
                        _p("priority", "enum", where="body", required=True, options=["Low", "Medium", "High", "Critical"],
                           help="The new priority")],
             "description": "Set one ticket's priority. Proposed: the person runs it."},
            {"name": "Add internal note", "action": True, "method": "POST", "path": "/tickets/{ticketId}/comments",
             "cache_seconds": 0,
             "body": '{"CommentText": "{{text}}", "TechnicianCommentDetails": {"TechnicianEmail": '
                     '"technician@example.com", "IsInternal": true}}',
             "params": [_p("ticketId", "int", where="path", required=True, min=1, help="Ticket number"),
                        _p("text", "text", where="body", required=True, help="The note")],
             "description": "Add a technician-only note to a ticket (the customer doesn't see it). Proposed: the "
                            "person runs it. Edit the body's TechnicianEmail to the technician the notes come from."},
            {"name": "Resolve alert", "action": True, "method": "PUT", "path": "/alerts/{alertId}", "cache_seconds": 0,
             "params": [_p("alertId", "int", where="path", required=True, min=1, help="Alert ID")],
             "description": "Mark one Atera alert as resolved. Proposed: the person runs it."},
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
                  "auth_login_path", "tls_check_hostname", "one_at_a_time", "operations"}
# Set by hand on the API, never brought in by a file.
_TEMPLATE_REFUSED = {"secret", "token", "password", "credential", "ca_pem", "certificate", "ad_group",
                     "claude_enabled"}
_OP_KEYS = {"name", "description", "example_questions", "method", "path", "params", "body", "cache_seconds",
            "action", "shape"}
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
        action = op.get("action", False)
        if not isinstance(action, bool):
            raise ApiError(f"“{op_name}”: action is true or false.")
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
            check_specs(str(op.get("path") or ""), params, method, body, action)
            shape = check_shape(op.get("shape"))                 # how its answer is boiled down ('' = as sent)
        except ApiError as exc:
            raise ApiError(f"“{op_name}”: {exc}") from None
        out_ops.append({"name": op_name, "description": str(op.get("description") or "")[:2000],
                        "example_questions": examples, "method": method, "path": op["path"], "params": params,
                        "body": body if method in BODY_METHODS else "", "cache_seconds": cache, "action": action,
                        "shape": shape})
    login_path = check_login_path(str(t.get("auth_login_path") or ""))   # sign in there first (basic only)
    if login_path and auth_type != "basic":
        raise ApiError("auth_login_path needs auth_type basic: the user name and password are swapped for a token.")
    return {"name": name, "description": str(t.get("description") or "")[:20000], "base_url": base_url,
            "auth_type": auth_type, "auth_header": auth_header, "auth_user": str(t.get("auth_user") or "")[:200],
            "auth_login_path": login_path,
            "tls_check_hostname": 0 if t.get("tls_check_hostname") is False else 1,
            "one_at_a_time": 1 if t.get("one_at_a_time") is True else 0, "operations": out_ops}


# Fleetboard and Fleetboard (changes): built in, read from api-templates/ through the same checks as an upload.
PRESETS.extend(fleetboard_presets.load(parse_template))
# UniFi Network and OPNsense: the same, from api-templates/unifi-network.json and opnsense.json (network_presets.py).
PRESETS.extend(network_presets.load(parse_template))


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
