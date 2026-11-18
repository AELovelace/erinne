"""The built-in Wazuh API presets (apis.PRESETS), from the audit of 2026-11-17
(BeepDrive: "Wazuh API preset - audit findings and improvements (2026-11-17).md").

Three presets, because each has its own address and credential:

- **Wazuh**: the indexer (OpenSearch, port 9200): alerts with server-side filters, timelines, the noise
  report, agent silence, vulnerabilities, installed packages, and the indexer's own health and retention.
- **Wazuh manager**: the manager API (port 55000), read-only: agents, versions, configuration (secrets
  redacted), SCA, FIM, logs, API users, open ports and packages.
- **Wazuh manager actions**: the same manager API with a separate read-write user: active response,
  restart, upgrade and group changes. Each is a proposal the person runs; nothing here runs by itself.

Bodies are written as Python dicts and turned into body templates by _body(): a string that is exactly
"{{name}}" for a name in BARE becomes a bare JSON value (a number or a list), every other {{name}} stays
inside its string. {"$if": name, "then": ...} is an optional clause (apis.fill_body): left out when that
parameter is empty.
"""

from __future__ import annotations

import json

# Parameters whose {{name}} stands for a bare JSON value (numbers and lists), not text inside a string.
BARE = ("count", "top", "min_level", "max_level", "rule_id", "exclude_rule_id", "exclude_srcip", "severity")


def _p(name, type_, *, where="query", required=False, help="", **extra) -> dict:
    """One parameter spec (the same shape as apis._p)."""
    return {"name": name, "type": type_, "in": where, "required": required, "help": help, **extra}


def _body(obj) -> str:
    """A body template from a dict: "{{count}}" → {{count}} for the BARE names."""
    text = json.dumps(obj, ensure_ascii=False)
    for name in BARE:
        text = text.replace(f'"{{{{{name}}}}}"', f"{{{{{name}}}}}")       # "{{count}}" → {{count}}
    return text


def _shape(**shape) -> str:
    return json.dumps(shape, ensure_ascii=False, sort_keys=True)


# ------------------------------------------------------------------ indexer
HOURS = _p("hours", "int", where="body", min=1, max=720, default=24, help="How many hours back")
AGENT = _p("agent", "string", where="body", default="*", help="Machine (agent) name; * for all, or a pattern like FD-*")
TOP = _p("top", "int", where="body", min=5, max=100, default=15,
         help="How many entries each top list (rules, machines, IPs) shows")


def _levels(min_default: int) -> list[dict]:
    return [_p("min_level", "int", where="body", min=0, max=15, default=min_default,
               help="Lowest rule level (7+ notable, 12+ critical)"),
            _p("max_level", "int", where="body", min=0, max=15, default=15,
               help="Highest rule level, e.g. 11 to leave out the critical floods")]


# Server-side filters (P3, P4): they narrow the search itself, not just the page that came back.
FILTERS = [
    _p("rule_id", "list", where="body", help="Only these rule IDs, e.g. 5710,31151"),
    _p("exclude_rule_id", "list", where="body", help="Leave out these rule IDs (noise), e.g. 204,31101"),
    _p("rule_group", "string", where="body", help="Only this rule group, e.g. web, authentication_failed, syscheck"),
    _p("text", "string", where="body", help="Only rules whose description contains this, e.g. web attack"),
    _p("srcip", "string", where="body", help="Only this source IP, or a pattern like 192.168.1.*"),
    _p("exclude_srcip", "list", where="body", help="Leave out these source IPs, e.g. 192.168.1.253"),
]


def _alert_query(*, agent: bool = True, levels: bool = True, filters: bool = True, srcip_required: bool = False,
                 extra: list | None = None) -> dict:
    """The bool query alert searches share: time window, levels, machine and the optional filters."""
    filt: list = [{"range": {"timestamp": {"gte": "now-{{hours}}h"}}}]
    if levels:
        filt.append({"range": {"rule.level": {"gte": "{{min_level}}", "lte": "{{max_level}}"}}})
    if agent:
        filt.append({"wildcard": {"agent.name": "{{agent}}"}})
    if srcip_required:
        filt.append({"wildcard": {"data.srcip": "{{srcip}}"}})
    filt += extra or []
    must_not: list = []
    if filters:
        filt += [{"$if": "rule_id", "then": {"terms": {"rule.id": "{{rule_id}}"}}},
                 {"$if": "rule_group", "then": {"term": {"rule.groups": "{{rule_group}}"}}},
                 {"$if": "text", "then": {"wildcard": {"rule.description": {"value": "*{{text}}*",
                                                                             "case_insensitive": True}}}}]
        if not srcip_required:
            filt.append({"$if": "srcip", "then": {"wildcard": {"data.srcip": "{{srcip}}"}}})
        must_not += [{"$if": "exclude_rule_id", "then": {"terms": {"rule.id": "{{exclude_rule_id}}"}}},
                     {"$if": "exclude_srcip", "then": {"terms": {"data.srcip": "{{exclude_srcip}}"}}}]
    return {"bool": {"filter": filt, **({"must_not": must_not} if filters else {})}}


def _with_exclusions(query: dict) -> dict:
    """A query that also leaves out the rule IDs in exclude_rule_id (when given)."""
    return {"bool": {**query["bool"], "must_not": [
        {"$if": "exclude_rule_id", "then": {"terms": {"rule.id": "{{exclude_rule_id}}"}}}]}}


ALERT_FIELDS = ["timestamp", "rule.level", "rule.id", "rule.description", "rule.groups", "rule.mitre.tactic",
                "rule.mitre.technique", "agent.name", "agent.ip", "data.srcip", "data.srcuser", "data.dstuser",
                "location"]
# Alert rows come back flat with short names (C1): time, level, rule_id, description, agent, srcip, user, …
ALERT_SHAPE = _shape(format="elasticsearch", rename={
    "timestamp": "time", "rule.level": "level", "rule.id": "rule_id", "rule.description": "description",
    "rule.groups": "groups", "rule.mitre.tactic": "tactic", "rule.mitre.technique": "technique",
    "agent.name": "agent", "agent.ip": "agent_ip", "data.srcip": "srcip", "data.srcuser": "srcuser",
    "data.dstuser": "user"}, single=["description", "rule_id", "severity"])
# Sub-aggregations of size 1 always come back as one value (a string, or null); every other one is always a list.
ES_SHAPE = _shape(format="elasticsearch", single=["description", "rule_id", "severity"])


def _terms(field: str, size="{{top}}", **aggs) -> dict:
    """A terms aggregation, with sub-aggregations if given."""
    out: dict = {"terms": {"field": field, "size": size}}
    if aggs:
        out["aggs"] = aggs
    return out


RULE_DETAIL = {"level": {"max": {"field": "rule.level"}}, "rule_id": _terms("rule.id", 1)}

INDEXER_OPERATIONS: list[dict] = [
    {"name": "Alert summary", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 120,
     "params": [HOURS, *_levels(0), AGENT, TOP, *FILTERS],
     "body": _body({"size": 0, "track_total_hits": True, "query": _alert_query(), "aggs": {
         "levels": _terms("rule.level", 16),
         "rules": _terms("rule.description", **RULE_DETAIL),
         "rules_by_level": {"terms": {"field": "rule.level", "size": 16, "order": {"_key": "desc"}},
                            "aggs": {"rules": _terms("rule.description", 5)}},
         "agents": _terms("agent.name"),
         "tactics": _terms("rule.mitre.tactic", 10),
         "sources": _terms("data.srcip")}}),
     "shape": ES_SHAPE,
     "description": "Counts of security alerts by level, rule (with its ID and level), rules per level, machine, "
                    "MITRE tactic and source IP, for a time window. Narrow it on the server: a level range, "
                    "machine, rule IDs, rule group, words in the description, source IP; and leave out noisy "
                    "rule IDs or IPs. total is exact; *_others says how many fell outside a top list.",
     "example_questions": "How many security alerts were there today?\nWhat's the alert count by severity?\n"
                          "Which machines have the most alerts this week?\nWhat are the top alert types?\n"
                          "What's left once the flood rules are excluded?\nWhich rules fire at level 10?"},
    {"name": "Alerts", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 60,
     "params": [HOURS, *_levels(7), AGENT,
                _p("count", "int", where="body", min=1, max=50, default=20, help="How many alerts"), *FILTERS],
     "body": _body({"size": "{{count}}", "track_total_hits": True, "sort": [{"timestamp": "desc"}],
                    "query": _alert_query(), "_source": ALERT_FIELDS}),
     "shape": ALERT_SHAPE,
     "description": "The alerts themselves, newest first, one row each: time, level, rule_id, description, "
                    "groups, MITRE tactic, agent, source IP and user. The same server-side filters as Alert "
                    "summary (rule IDs, rule group, description words, source IP, exclusions), so a rare alert "
                    "can be pulled out of a flood.",
     "example_questions": "Show me today's critical alerts.\nWhat triggered alerts on the front desk PC?\n"
                          "What happened on FD-01 overnight?\nList the level 12 alerts this week.\n"
                          "Show me the web attack alerts on site-chan.\nShow rule 5710 alerts."},
    {"name": "Failed logins", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 60,
     "params": [HOURS, AGENT, TOP],
     "body": _body({"size": 0, "track_total_hits": True,
                    "query": _alert_query(levels=False, filters=False, extra=[
                        {"terms": {"rule.groups": ["authentication_failed", "authentication_failures",
                                                   "invalid_login"]}}]),
                    "aggs": {"agents": _terms("agent.name"), "sources": _terms("data.srcip"),
                             "users": _terms("data.dstuser"), "rules": _terms("rule.description"),
                             "per_hour": {"date_histogram": {"field": "timestamp", "fixed_interval": "1h",
                                                             "min_doc_count": 1}}}}),
     "shape": ES_SHAPE,
     "description": "Failed logins: how many, on which machines, from which IPs, for which accounts, and "
                    "per hour (to spot brute force).",
     "example_questions": "Any failed logins today?\nIs anyone brute-forcing us?\n"
                          "Which accounts had failed logins this week?\nWhere are the failed logins coming from?"},
    # One source IP (or a pattern of them): what it set off, where, and the latest alerts themselves.
    # Its own operation, not a filter on Alerts: a wildcard on data.srcip only matches alerts that
    # have a source IP, so a "*" default there would hide every alert without one (Windows logons).
    {"name": "Alerts from an IP", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 60,
     "params": [_p("srcip", "string", where="body", required=True,
                   help="Source IP, e.g. 192.168.1.253, or a pattern like 192.168.1.*"),     # required: no "all IPs"
                HOURS, *_levels(0),                                                     # 0: an IP's quiet alerts matter too
                _p("count", "int", where="body", min=1, max=50, default=20, help="How many alerts"), TOP,
                _p("exclude_rule_id", "list", where="body", help="Leave out these rule IDs, e.g. 31101")],
     "body": _body({"size": "{{count}}", "track_total_hits": True, "sort": [{"timestamp": "desc"}],
                    "query": _with_exclusions(_alert_query(agent=False, filters=False, srcip_required=True)),
                    "aggs": {"levels": _terms("rule.level", 16), "rules": _terms("rule.description", **RULE_DETAIL),
                             "agents": _terms("agent.name"), "locations": _terms("location"),
                             "users": _terms("data.dstuser")},
                    "_source": ALERT_FIELDS}),
     "shape": ALERT_SHAPE,
     "description": "Everything one source IP (or a pattern like 192.168.1.*) set off: counts by level, "
                    "rule, machine, log and account, plus the latest alerts, newest first.",
     "example_questions": "What has 192.168.1.253 been doing?\nShow me the alerts from 34.14.12.83.\n"
                          "Which alerts came from 186.247.44.26 today?\nWhat is 192.168.1.76 setting off?\n"
                          "Is this IP attacking us?"},
    # C4: a timeline, so "when did the flood start and stop" is one call instead of guessing from windows.
    {"name": "Alert timeline", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 120,
     "params": [_p("hours", "int", where="body", min=1, max=720, default=48, help="How many hours back"),
                _p("interval", "enum", where="body", options=["30m", "1h", "3h", "6h", "12h", "1d"], default="1h",
                   help="Bucket size; keep hours / interval under about 200 buckets"),
                _p("by", "enum", where="body", options=["rule.level", "rule.description", "agent.name", "data.srcip"],
                   default="rule.level", help="What each bucket is split by (its top 5)"),
                *_levels(0), AGENT, *FILTERS],
     "body": _body({"size": 0, "track_total_hits": True, "query": _alert_query(), "aggs": {
         "timeline": {"date_histogram": {"field": "timestamp", "fixed_interval": "{{interval}}", "min_doc_count": 1},
                      "aggs": {"by": _terms("{{by}}", 5)}}}}),
     "shape": ES_SHAPE,
     "description": "Alerts over time: a count per hour (or other interval), each split by level, rule, machine "
                    "or source IP. Shows when something started, peaked and stopped. Empty buckets are left out.",
     "example_questions": "When did the site-chan flood start?\nShow the alerts per hour for the last two days.\n"
                          "When did the web errors stop?\nWhat's the alert timeline this week?"},
    # C6: what drives the volume, to tune rules.
    {"name": "Noise report", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 300,
     "params": [_p("hours", "int", where="body", min=1, max=720, default=168, help="How many hours back"),
                *_levels(0), AGENT,
                _p("top", "int", where="body", min=5, max=100, default=20, help="How many rules"),
                _p("exclude_rule_id", "list", where="body", help="Rules already dealt with, to see what's next")],
     "body": _body({"size": 0, "track_total_hits": True,
                    "query": _with_exclusions(_alert_query(filters=False)),
                    "aggs": {"rules": {"terms": {"field": "rule.id", "size": "{{top}}"}, "aggs": {
                        "description": _terms("rule.description", 1), "level": {"max": {"field": "rule.level"}},
                        "agents": _terms("agent.name", 3), "sources": _terms("data.srcip", 3),
                        "groups": _terms("rule.groups", 3)}}}}),
     "shape": ES_SHAPE,
     "description": "The rules that make the most alerts, by rule ID, with each one's description (a string), "
                    "level, top machines, top source IPs and groups (always lists of {key, count}), and the share "
                    "of the total. sources is [] for rules whose alerts carry no data.srcip, which is most "
                    "host-side rules (file integrity, rootcheck, syscheck, package changes). Use it to decide "
                    "what to tune or exclude; pass the IDs you've dealt with in exclude_rule_id to see what's next.",
     "example_questions": "Which rules make the most noise?\nWhat should we tune in Wazuh?\n"
                          "What are the noisiest alerts this week?\nWhat's flooding the alerts?"},
    # C7 / P8: a dead agent and a quiet one look the same in alert counts; this tells them apart.
    {"name": "Agent silence", "method": "POST", "path": "/wazuh-alerts-*/_search", "cache_seconds": 300,
     "params": [_p("hours", "int", where="body", min=24, max=720, default=168,
                   help="How far back to look for each agent's last alert"),
                _p("silent_hours", "int", where="body", min=1, max=720, default=24,
                   help="An agent with no alert in this many hours counts as silent")],
     "body": _body({"size": 0, "query": {"range": {"timestamp": {"gte": "now-{{hours}}h"}}}, "aggs": {
         "agents": {"terms": {"field": "agent.name", "size": 500, "order": {"last": "asc"}}, "aggs": {
             "last": {"max": {"field": "timestamp"}},
             "recent": {"filter": {"range": {"timestamp": {"gte": "now-{{silent_hours}}h"}}}}}}}}),
     "shape": ES_SHAPE,
     "description": "Each agent's last alert time and how many alerts it sent in the last silent_hours (recent, "
                    "always a number), quietest first. recent = 0 means silent. An agent with no alert in the whole window isn't listed: "
                    "Wazuh manager → Agents shows its status and last keep-alive.",
     "example_questions": "Which agents have gone quiet?\nHas any machine stopped sending alerts?\n"
                          "When did each agent last report?\nIs Pi-Chan still sending logs?"},
    # B: vulnerability detection (Wazuh 4.8+ keeps its state in wazuh-states-vulnerabilities-*).
    {"name": "Vulnerabilities", "method": "POST", "path": "/wazuh-states-vulnerabilities-*/_search",
     "cache_seconds": 300,
     "params": [AGENT,
                _p("severity", "list", where="body", help="Only these severities: Critical, High, Medium, Low"),
                _p("cve", "string", where="body", help="Only this CVE, e.g. CVE-2024-3094"),
                _p("package", "string", where="body", help="Only packages named like this, e.g. openssl or *ssl*"),
                TOP, _p("count", "int", where="body", min=0, max=50, default=20,
                        help="How many vulnerabilities to list, highest score first")],
     "body": _body({"size": "{{count}}", "track_total_hits": True,
                    "sort": [{"vulnerability.score.base": {"order": "desc", "unmapped_type": "float"}}],
                    "query": {"bool": {"filter": [
                        {"wildcard": {"agent.name": "{{agent}}"}},
                        {"$if": "severity", "then": {"terms": {"vulnerability.severity": "{{severity}}"}}},
                        {"$if": "cve", "then": {"term": {"vulnerability.id": "{{cve}}"}}},
                        {"$if": "package", "then": {"wildcard": {"package.name": {"value": "{{package}}",
                                                                                  "case_insensitive": True}}}}]}},
                    "aggs": {"severity": _terms("vulnerability.severity", 10),
                             "agents": _terms("agent.name", severity=_terms("vulnerability.severity", 5)),
                             "cves": _terms("vulnerability.id", score={"max": {"field": "vulnerability.score.base"}},
                                            severity=_terms("vulnerability.severity", 1),
                                            packages=_terms("package.name", 3),
                                            machines={"cardinality": {"field": "agent.name"}})},
                    "_source": ["agent.name", "package.name", "package.version", "vulnerability.id",
                                "vulnerability.severity", "vulnerability.score.base", "vulnerability.detected_at",
                                "vulnerability.published_at"]}),
     "shape": _shape(format="elasticsearch", rename={
         "agent.name": "agent", "package.name": "package", "package.version": "version",
         "vulnerability.id": "cve", "vulnerability.severity": "severity", "vulnerability.score.base": "score",
         "vulnerability.detected_at": "detected", "vulnerability.published_at": "published"}),
     "description": "Vulnerability detection results (Wazuh 4.8 or later): counts by severity, by machine (with "
                    "severities) and by CVE (score, packages, how many machines), plus the worst ones first. "
                    "Filter by machine, severity, CVE or package.",
     "example_questions": "What critical vulnerabilities do we have?\nWhich machine has the most vulnerabilities?\n"
                          "Are we affected by CVE-2024-3094?\nWhich hosts have a vulnerable openssl?"},
    {"name": "Installed packages", "method": "POST", "path": "/wazuh-states-inventory-packages-*/_search",
     "cache_seconds": 300,
     "params": [_p("package", "string", where="body", required=True, help="Package name or pattern, e.g. nginx or *ssl*"),
                AGENT, TOP, _p("count", "int", where="body", min=0, max=50, default=30, help="How many rows")],
     "body": _body({"size": "{{count}}", "track_total_hits": True, "query": {"bool": {"filter": [
         {"wildcard": {"package.name": {"value": "{{package}}", "case_insensitive": True}}},
         {"wildcard": {"agent.name": "{{agent}}"}}]}},
         "aggs": {"versions": _terms("package.version", machines=_terms("agent.name", 10))},
         "_source": ["agent.name", "package.name", "package.version", "package.vendor", "package.installed"]}),
     "shape": _shape(format="elasticsearch", rename={
         "agent.name": "agent", "package.name": "package", "package.version": "version",
         "package.vendor": "vendor", "package.installed": "installed"}),
     "description": "Which machines have a package and at which versions, from the indexer's inventory (Wazuh 4.13 "
                    "or later; on older ones use Wazuh manager → Agent packages).",
     "example_questions": "Which machines have an old nginx?\nWhat versions of openssl are installed?\n"
                          "Where is putty installed?"},
    # B: the indexer itself: is there room, is it green, is there retention.
    {"name": "Indexer health", "path": "/_cluster/health", "cache_seconds": 120,
     "description": "The indexer cluster's health: status (green/yellow/red), nodes, active and unassigned shards.",
     "example_questions": "Is the Wazuh indexer healthy?\nAre there unassigned shards?\nIs the indexer green?"},
    {"name": "Indexer disk", "path": "/_cat/allocation?format=json&bytes=gb", "cache_seconds": 300,
     "description": "Disk used and free on each indexer node, in GB, with its shard count.",
     "example_questions": "How much disk does the Wazuh indexer have left?\nIs the indexer running out of space?"},
    {"name": "Indices", "path": "/_cat/indices/wazuh-*?format=json&bytes=mb&s=index"
                                "&h=index,health,status,docs.count,store.size,creation.date.string",
     "cache_seconds": 300,
     "description": "Every Wazuh index (alerts per day, archives, states) with its health, document count, size in "
                    "MB and creation date: how far back alerts go and how fast they grow.",
     "example_questions": "How far back do the Wazuh alerts go?\nHow big are the alert indices?\n"
                          "How much do alerts take per day?"},
    {"name": "Retention policies", "path": "/_plugins/_ism/policies", "cache_seconds": 300,
     "shape": _shape(keep=["_id", "policy.description", "policy.ism_template.index_patterns",
                           "policy.states.name", "policy.states.actions", "policy.states.transitions"]),
     "description": "The index state management (ISM) policies: which index patterns each covers and its states, "
                    "actions and transitions (e.g. delete after 90 days). No policy means no retention.",
     "example_questions": "Is there a retention policy for Wazuh alerts?\nWhen are old alerts deleted?\n"
                          "What ISM policies are there?"},
]

INDEXER = {
    "key": "wazuh", "name": "Wazuh", "base_url": "https://wazuh-indexer.example:9200", "auth_type": "basic",
    "tls_check_hostname": 0,
    "description": "Wazuh indexer (security alerts, vulnerabilities, inventory, index health). Set the address to "
                   "your indexer (port 9200). Use a dedicated indexer user with read-only access to wazuh-* plus "
                   "cluster monitor (for health, disk and retention); only the fixed queries below are ever sent. "
                   "Trust the indexer's certificate on this page. The manager API is a separate preset: "
                   "Wazuh manager.",
    "operations": INDEXER_OPERATIONS,
}


# ------------------------------------------------------------------ manager
WAZUH_SHAPE = _shape(format="wazuh")
REDACTED = _shape(format="wazuh", redact=True)
AGENT_ID = _p("agent_id", "string", where="path", required=True,
              help="Agent ID, e.g. 001 (see Agents); 000 is the manager itself")
LIMIT = _p("limit", "int", min=1, max=500, default=100, help="How many rows")
OFFSET = _p("offset", "int", min=0, max=100000, default=0, help="Skip this many rows (paging)")
LOGIN_PATH = "/security/user/authenticate"
CONFIG_SECTIONS = ["global", "alerts", "auth", "remote", "active-response", "command", "vulnerability-detection",
                   "syscheck", "rootcheck", "sca", "syscollector", "localfile", "integration", "email_alerts",
                   "logging", "cluster", "indexer", "labels", "ruleset"]

MANAGER_OPERATIONS: list[dict] = [
    {"name": "Agents", "path": "/agents?select=id,name,ip,status,version,group,lastKeepAlive,dateAdd,os.name,"
                               "os.version,node_name", "cache_seconds": 60,
     "params": [_p("status", "list", help="Only these: active, disconnected, never_connected, pending"),
                _p("group", "string", help="Only agents in this group"),
                _p("search", "string", help="Only agents whose fields contain this, e.g. chan"),
                _p("limit", "int", min=1, max=500, default=500, help="How many rows"), OFFSET],
     "shape": WAZUH_SHAPE,
     "description": "Every agent: ID, name, IP, status (active / disconnected / never_connected / pending), "
                    "version, groups, last keep-alive, when it was added and its OS. Tells a dead agent from a "
                    "quiet one.",
     "example_questions": "Which Wazuh agents are disconnected?\nList the Wazuh agents.\n"
                          "When did Pi-Chan last check in?\nWhat version is each agent on?\nWhat's agent 003?"},
    {"name": "Agent status counts", "path": "/agents/summary/status", "cache_seconds": 60, "shape": WAZUH_SHAPE,
     "description": "How many agents are active, disconnected, never connected and pending.",
     "example_questions": "How many Wazuh agents are online?\nAre all agents connected?"},
    {"name": "Outdated agents", "path": "/agents/outdated", "cache_seconds": 300, "shape": WAZUH_SHAPE,
     "params": [LIMIT],
     "description": "Agents running an older version than the manager: ID, name and version.",
     "example_questions": "Which agents need upgrading?\nAre any Wazuh agents out of date?"},
    {"name": "Manager info", "path": "/manager/info", "cache_seconds": 300, "shape": WAZUH_SHAPE,
     "description": "The manager's version, install path, type, max agents and OpenSSL support.",
     "example_questions": "What version is the Wazuh manager?\nWhich Wazuh version are we running?"},
    {"name": "Manager status", "path": "/manager/status", "cache_seconds": 60, "shape": WAZUH_SHAPE,
     "description": "Which manager daemons are running or stopped (analysisd, remoted, authd, modulesd, …).",
     "example_questions": "Are all Wazuh daemons running?\nIs wazuh-authd running?\nIs the Wazuh manager up?"},
    {"name": "Manager configuration", "path": "/manager/configuration", "cache_seconds": 300, "shape": REDACTED,
     "params": [_p("section", "enum", required=True, options=CONFIG_SECTIONS,
                   help="Which part of ossec.conf, e.g. auth for enrollment, remote for listening ports")],
     "description": "One section of the manager's ossec.conf: enrollment (auth), listening ports (remote), active "
                    "response, vulnerability detection, FIM, integrations, email and so on. Passwords, keys and "
                    "webhook addresses are redacted on the server.",
     "example_questions": "How is agent enrollment configured?\nIs enrollment password-protected?\n"
                          "Which port does the manager listen on?\nIs vulnerability detection enabled?\n"
                          "What active responses are configured?\nWhich integrations are set up?"},
    {"name": "Agent configuration", "path": "/agents/{agent_id}/config/{component}/{configuration}",
     "cache_seconds": 300, "shape": REDACTED,
     "params": [AGENT_ID,
                _p("component", "enum", where="path", required=True,
                   options=["agent", "com", "logcollector", "syscheck", "wmodules"],
                   help="agent (client, buffer, labels), com (active-response, logging), logcollector (localfile, "
                        "socket), syscheck (syscheck, rootcheck), wmodules (wmodules)"),
                _p("configuration", "enum", where="path", required=True,
                   options=["client", "buffer", "labels", "active-response", "logging", "localfile", "socket",
                            "syscheck", "rootcheck", "wmodules"],
                   help="Must belong to the component (see component's help)")],
     "description": "What an agent is really running (it must be connected): FIM (syscheck/syscheck), which logs "
                    "it reads (logcollector/localfile), its modules (wmodules/wmodules) and so on. Secrets are "
                    "redacted.",
     "example_questions": "Does Stream-Chan run FIM?\nWhich logs does Pi-Chan collect?\n"
                          "Is syscheck enabled on agent 004?\nWhat modules does DC-2 run?"},
    {"name": "Groups", "path": "/groups", "cache_seconds": 300, "shape": WAZUH_SHAPE,
     "description": "The agent groups, with how many agents each has.",
     "example_questions": "What Wazuh agent groups are there?\nHow many agents are in each group?"},
    {"name": "Group members", "path": "/groups/{group_id}/agents?select=id,name,status,version", "cache_seconds": 120,
     "params": [_p("group_id", "string", where="path", required=True, help="Group name, e.g. default"), LIMIT],
     "shape": WAZUH_SHAPE,
     "description": "The agents in one group, with their status and version.",
     "example_questions": "Which agents are in the default group?\nWho's in the linux group?"},
    {"name": "Group configuration", "path": "/groups/{group_id}/configuration", "cache_seconds": 300,
     "params": [_p("group_id", "string", where="path", required=True, help="Group name, e.g. default")],
     "shape": REDACTED,
     "description": "What a group pushes to its agents (agent.conf): FIM, log files, SCA and so on. Secrets are "
                    "redacted.",
     "example_questions": "What does the default group push to agents?\nWhat's in agent.conf for the linux group?"},
    {"name": "SCA results", "path": "/sca/{agent_id}", "cache_seconds": 300, "params": [AGENT_ID],
     "shape": WAZUH_SHAPE,
     "description": "One agent's security configuration assessment (CIS) policies: score, passed, failed and "
                    "not-applicable counts, and when it last ran.",
     "example_questions": "What's the CIS score of DC-2?\nHow did agent 001 do on SCA?\nWhich SCA policies ran on Pi-Chan?"},
    {"name": "SCA checks", "path": "/sca/{agent_id}/checks/{policy_id}?select=id,title,result,remediation",
     "cache_seconds": 300,
     "params": [AGENT_ID,
                _p("policy_id", "string", where="path", required=True,
                   help="The policy's ID from SCA results, e.g. cis_ubuntu22-04"),
                _p("result", "enum", options=["failed", "passed", "not applicable"], default="failed",
                   help="Which checks"),
                _p("limit", "int", min=1, max=500, default=50, help="How many rows"), OFFSET],
     "shape": WAZUH_SHAPE,
     "description": "The checks of one SCA policy on one agent (failed ones by default), with each one's title "
                    "and how to fix it.",
     "example_questions": "Which CIS checks fail on DC-2?\nWhat SCA checks failed on agent 001?\n"
                          "How do we fix the failed CIS checks?"},
    {"name": "FIM last scan", "path": "/syscheck/{agent_id}/last_scan", "cache_seconds": 120, "params": [AGENT_ID],
     "shape": WAZUH_SHAPE,
     "description": "When one agent's file integrity monitoring scan last started and ended: is FIM really running?",
     "example_questions": "When did FIM last run on Stream-Chan?\nIs syscheck actually running on agent 002?"},
    {"name": "Manager logs", "path": "/manager/logs?sort=-timestamp", "cache_seconds": 60, "shape": REDACTED,
     "params": [_p("level", "enum", options=["critical", "error", "warning", "info", "debug"], default="error",
                   help="Which log level"),
                _p("tag", "string", help="Only one daemon, e.g. wazuh-authd, wazuh-remoted, wazuh-modulesd"),
                _p("search", "string", help="Only lines containing this"),
                _p("limit", "int", min=1, max=500, default=50, help="How many lines")],
     "description": "The manager's own log (ossec.log), newest first, by level and daemon. Passwords and keys in "
                    "log lines (authd logs a random enrollment password) are redacted on the server.",
     "example_questions": "Any errors in the Wazuh manager log?\nWhat is wazuh-remoted complaining about?\n"
                          "Show the Wazuh manager warnings."},
    {"name": "Manager log summary", "path": "/manager/logs/summary", "cache_seconds": 300, "shape": WAZUH_SHAPE,
     "description": "How many log lines of each level each manager daemon wrote.",
     "example_questions": "Which Wazuh daemon logs the most errors?\nSummarise the Wazuh manager log."},
    {"name": "Cluster status", "path": "/cluster/status", "cache_seconds": 300, "shape": WAZUH_SHAPE,
     "description": "Whether the manager runs as a cluster and whether the cluster is running.",
     "example_questions": "Is the Wazuh manager clustered?\nIs the Wazuh cluster running?"},
    {"name": "API users", "path": "/security/users", "cache_seconds": 300,
     "shape": _shape(format="wazuh", keep=["id", "username", "allow_run_as", "roles"]),
     "description": "The manager API's users and their role IDs (never passwords), to spot default or unused "
                    "accounts like wazuh and wazuh-wui.",
     "example_questions": "Who can use the Wazuh API?\nAre the default Wazuh API users still there?"},
    {"name": "API roles", "path": "/security/roles", "cache_seconds": 300,
     "shape": _shape(format="wazuh", keep=["id", "name", "policies", "users"]),
     "description": "The manager API's roles, with their policy IDs and user IDs.",
     "example_questions": "What roles does the Wazuh API have?\nWhich users are administrators in the Wazuh API?"},
    {"name": "Rule details", "path": "/rules?select=id,level,description,groups,filename,status", "cache_seconds": 600,
     "params": [_p("rule_ids", "list", required=True, help="Rule IDs, e.g. 204,5710")], "shape": WAZUH_SHAPE,
     "description": "What one or more rules are: level, description, groups, which file they're in and whether "
                    "they're enabled. Useful before tuning a noisy rule.",
     "example_questions": "What is rule 204?\nWhich file is rule 31151 in?\nWhat level is rule 5710?"},
    {"name": "Agent packages", "path": "/syscollector/{agent_id}/packages?select=name,version,vendor,architecture",
     "cache_seconds": 300,
     "params": [AGENT_ID, _p("search", "string", help="Only packages containing this, e.g. ssl"), LIMIT, OFFSET],
     "shape": WAZUH_SHAPE,
     "description": "The software installed on one agent (syscollector): name, version, vendor and architecture.",
     "example_questions": "What version of nginx is on site-chan?\nWhat's installed on agent 003?\n"
                          "Does DC-2 have an old openssl?"},
    {"name": "Agent open ports", "path": "/syscollector/{agent_id}/ports?select=local.ip,local.port,remote.ip,"
                                         "protocol,state,process,pid", "cache_seconds": 300,
     "params": [AGENT_ID, _p("state", "string", help="Only this state, e.g. listening"), LIMIT, OFFSET],
     "shape": WAZUH_SHAPE,
     "description": "The ports one agent has open (syscollector): local address and port, protocol, state and the "
                    "process. Use state=listening for what it's serving, and agent 000 for the manager itself.",
     "example_questions": "What ports is the Wazuh manager listening on?\nWhich ports are open on site-chan?\n"
                          "Is anything listening on 0.0.0.0 on DC-2?"},
    {"name": "Upgrade tasks", "path": "/tasks/status?command=upgrade", "cache_seconds": 60,
     "params": [_p("agents_list", "list", help="Only these agent IDs"), LIMIT], "shape": WAZUH_SHAPE,
     "description": "How agent upgrades went: each task's agent, status (In progress, Done, Failed…) and error.",
     "example_questions": "Did the agent upgrade work?\nWhat's the status of the Wazuh agent upgrades?"},
]

MANAGER = {
    "key": "wazuh-manager", "name": "Wazuh manager", "base_url": "https://wazuh-manager.example:55000",
    "auth_type": "basic", "auth_login_path": LOGIN_PATH, "tls_check_hostname": 0,
    "description": "Wazuh manager API (port 55000), read-only: agents and their health, versions, configuration, "
                   "SCA, FIM, logs, API users, open ports and installed software. Set the address to your manager. "
                   "Use a dedicated API user with a read-only role (agent:read, group:read, manager:read, "
                   "cluster:read, sca:read, syscheck:read, syscollector:read, rules:read, security:read, "
                   "task:status); never wazuh or wazuh-wui. It signs in at /security/user/authenticate and "
                   "reuses the token for 10 minutes. Passwords, keys and webhook addresses in configuration and "
                   "logs are redacted on the server, before anything is cached, logged or shown. The changes are "
                   "a separate preset, Wazuh manager actions, with its own read-write user.",
    "operations": MANAGER_OPERATIONS,
}


# ------------------------------------------------------------------ actions
ACTION_OPERATIONS: list[dict] = [
    {"name": "Run active response", "action": True, "method": "PUT", "path": "/active-response", "cache_seconds": 0,
     "params": [_p("agents_list", "list", required=True, help="Agent IDs to run it on, e.g. 001"),
                _p("command", "enum", where="body", required=True,
                   options=["firewall-drop", "host-deny", "route-null", "netsh", "win_route-null"],
                   help="A command configured in the manager's active-response section (its timeout applies)"),
                _p("srcip", "string", where="body", required=True, help="The IP address to act on")],
     "body": _body({"command": "{{command}}", "arguments": [], "alert": {"data": {"srcip": "{{srcip}}"}}}),
     "description": "Run an active response on agents, e.g. firewall-drop to block an attacking IP. The command "
                    "must be configured on the manager; its timeout there decides how long the block lasts (check "
                    "Wazuh manager → Manager configuration, section active-response). Proposed: the person runs it."},
    {"name": "Restart agent", "action": True, "method": "PUT", "path": "/agents/{agent_id}/restart",
     "cache_seconds": 0, "params": [AGENT_ID],
     "description": "Restart one agent (it must be connected). Proposed: the person runs it."},
    {"name": "Upgrade agents", "action": True, "method": "PUT", "path": "/agents/upgrade", "cache_seconds": 0,
     "params": [_p("agents_list", "list", required=True, help="Agent IDs to upgrade, e.g. 003,004")],
     "description": "Upgrade agents to the manager's version from the Wazuh repository (see Outdated agents; "
                    "Upgrade tasks shows how it went). Proposed: the person runs it."},
    {"name": "Add agent to group", "action": True, "method": "PUT", "path": "/agents/{agent_id}/group/{group_id}",
     "cache_seconds": 0,
     "params": [AGENT_ID, _p("group_id", "string", where="path", required=True, help="Group name, e.g. linux")],
     "description": "Put one agent in a group, so it gets that group's configuration. Proposed: the person runs it."},
    {"name": "Remove agent from group", "action": True, "method": "DELETE",
     "path": "/agents/{agent_id}/group/{group_id}", "cache_seconds": 0,
     "params": [AGENT_ID, _p("group_id", "string", where="path", required=True, help="Group name")],
     "description": "Take one agent out of a group (the undo of Add agent to group). Proposed: the person runs it."},
]

ACTIONS = {
    "key": "wazuh-manager-actions", "name": "Wazuh manager actions", "base_url": "https://wazuh-manager.example:55000",
    "auth_type": "basic", "auth_login_path": LOGIN_PATH, "tls_check_hostname": 0,
    "description": "Changes on the Wazuh manager API: active response, restarting and upgrading agents, group "
                   "membership. Erynne and Claude only ever propose these; the person (or an admin, if you tick "
                   "\"needs admin approval\") runs each one. Use a separate API user whose role allows only "
                   "active-response:command, agent:restart, agent:upgrade, agent:modify_group and "
                   "group:modify_assignments, so the read-only Wazuh manager entry stays read-only. Editing "
                   "local_rules.xml isn't offered: Wazuh replaces the whole file, so one proposal could wipe "
                   "every custom rule.",
    "operations": ACTION_OPERATIONS,
}

PRESETS = [INDEXER, MANAGER, ACTIONS]
