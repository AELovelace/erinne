# Packaging an API for Mnemerynne

This guide is for a project that wants Mnemerynne (and its assistant, Erynne) to
answer questions from its data. You write one JSON file, an **API template**,
describing a few fixed, read-only requests. The Mnemerynne admin uploads it
(Access → APIs → *Upload template…*), picks it from the list, presses **Add**,
then enters the credential and chooses who may use it.

A working example is [atera-read-write.json](atera-read-write.json) (actions);
the built-in presets in `mnemerynne/apis.py` (`PRESETS`) show reading operations.

## How Mnemerynne uses your API

Mnemerynne never lets the AI write a request. It can only:

1. pick one of the **operations** you define (by matching the question against
   each operation's example questions), and
2. fill in that operation's **typed parameters** (a date, a number in a range,
   one of a list of values, a short piece of plain text).

Mnemerynne builds the request itself from your fixed path and body. So design
operations as **specific, safe questions your system can answer**: "the latest
report per hotel", "failures in the last N days". Don't design them as a
general query endpoint. A free-form query field can't be exposed through a
template; the parameter rules below make that impossible on purpose.

Every reading call is a GET, or a POST with your fixed body. Operations that
change something are **actions** (see *Actions* below): the AI only proposes
them, and they run when the person presses *Run it*. Calls don't follow
redirects, time out after 20 seconds, keep at most 256 KB of the response, are
cached briefly and are logged. The AI sees the response's longest list a page at
a time (as many rows as fit, with an offset to ask for the next ones), so
**return small, flat JSON**: a few columns, dozens of rows, not thousands.

## The file

```json
{
  "format": "mnemerynne-api-template",
  "name": "Hotel Stats",
  "base_url": "https://stats.example.com",
  "auth_type": "bearer",
  "description": "Markdown shown to the admin: what this is and how to set it up.",
  "operations": [ ... ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `format` | yes | Always `"mnemerynne-api-template"`. |
| `name` | yes | The API's name in Mnemerynne, at most 80 characters. Not `Wazuh` or `Atera` (those are built in). Uploading a file with the same name replaces the earlier upload. |
| `base_url` | yes | `https://…` (or `http://…`). Every operation's path is added to it, and requests can't leave it. |
| `auth_type` | no | `none` (default), `bearer` (`Authorization: Bearer <secret>`), `header` (the secret in the header named by `auth_header`), or `basic` (user name `auth_user`, password = the secret). |
| `auth_header` | for `header` | The header name, letters, digits and dashes, e.g. `X-API-Key`. |
| `auth_user` | for `basic` | The user name. |
| `tls_check_hostname` | no | `false` if the server is reached by an address its certificate doesn't name (e.g. an IP). The admin pins the certificate either way. Default `true`. |
| `one_at_a_time` | no | `true` if the API answers only one request at a time (e.g. HTTP 429 while another query runs). Mnemerynne then queues calls to it instead of sending them side by side, and tells Claude. The admin can change it on the API's page. Default `false`. |
| `description` | no | Markdown for the admin. Put the setup steps here (see *Description* below). |
| `operations` | yes | 1 to 50 operations. |

A template is **refused** if it contains `secret`, `token`, `password`,
`credential`, `ca_pem`, `certificate`, `ad_group` or `claude_enabled`, or any
field not listed here. The credential, the trusted certificate, who may use the
API and whether Claude may query it are always set by hand, on the API, by the
admin.

## Operations

```json
{
  "name": "Parse failures",
  "description": "Report files that failed to parse in the last few days: when, which hotel, the file and the error.",
  "method": "POST",
  "path": "/api/device/sql",
  "body": {"sql": "SELECT … WHERE received_at >= datetime('now', ?)", "params": ["-{{days}} days"], "maxRows": 200},
  "params": [
    {"name": "days", "type": "int", "in": "body", "min": 1, "max": 30, "default": 3, "help": "How many days back"}
  ],
  "cache_seconds": 60,
  "example_questions": ["Did any reports fail to parse?", "Which report files failed this week?"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Unique within the template (ignoring case), at most 80 characters. |
| `description` | no | One sentence on what it returns. **The AI reads this** when filling in parameters, so say what the data covers. |
| `method` | no | `GET` (default) or `POST`; actions may also use `PUT`, `PATCH` or `DELETE`. |
| `action` | no | `true` if the operation changes data (see *Actions*). Default `false`. |
| `path` | yes | Starts with `/`; no `..`, `//`, `\` or `://`. May contain `{name}` for a path parameter, e.g. `/tickets/{ticketId}`. |
| `params` | no | Up to 30 parameters (below). |
| `body` | POST only | The fixed JSON body, with `{{name}}` where body parameters go (below). A JSON object, or a string (see *Bodies*). |
| `cache_seconds` | no | 0 to 86400; default 60. Use 0–60 for live data. |
| `example_questions` | strongly advised | A list of questions (up to 30), **phrased the way staff actually ask**. Chat questions are routed to the operation whose examples are closest in meaning, so give each operation 4–6 distinct examples that don't overlap with other operations. |

### Parameters

```json
{"name": "property", "type": "enum", "in": "body", "required": true, "help": "The hotel", "options": ["a", "b"]}
```

| Field | Meaning |
|---|---|
| `name` | Letters, digits and `_`, starting with a letter or `_`, at most 41 characters. Unique in the operation. |
| `type` | `int`, `date` (`YYYY-MM-DD`), `enum` (one of `options`; matched ignoring case), `string`, or `text` (actions only, in the body: free text up to 2000 characters, e.g. a comment). |
| `in` | `query` (default; added to the URL's query string), `path` (fills `{name}` in the path) or `body` (fills `{{name}}` in a POST body). |
| `required` | `true` if the call makes no sense without it. |
| `default` | Used when the question doesn't say. |
| `min`, `max` | For `int`. |
| `options` | For `enum`: the allowed values, exactly as your API expects them. |
| `help` | A few words for the AI ("How many days back"). |

**Prefer `enum`, `int` and `date`.** `string` values are limited to letters,
digits, spaces and `_ . , : @ + - /` (plus `*` and `?` in a body), at most 200
characters. Quotes, `=`, `(`, `;` and similar are rejected. A path value may
only hold letters, digits, `_`, `.` and `-`. If a value comes from a known set
(properties, sites, statuses), make it an `enum` with the exact list. That's
safer and the AI picks better.

### Bodies

Each `{{name}}` in the body is replaced with the parameter's value as JSON:

- **inside a JSON string**, as escaped text: `"-{{days}} days"` becomes `"-3 days"`;
- **outside a string**, as a JSON value: `{"size": {{count}}}` becomes `{"size": 10}`, and an enum or date outside a string becomes a quoted string.

An optional parameter with no value and no default becomes `""` inside a
string, or `null` outside one.

Every body parameter must appear in the body, and every `{{name}}` must be a
body parameter. Mnemerynne fills in sample values and checks that the result
is valid JSON before it accepts the operation.

Write `body` as a **JSON object** when every placeholder is inside a string
(most cases). When a placeholder stands bare, e.g. `"params": [{{count}}]`, the
body isn't valid JSON until filled in, so write it as a **string** instead:

```json
"body": "{\"sql\": \"SELECT … LIMIT ?\", \"params\": [{{count}}], \"maxRows\": 50}"
```

Keep bodies on one line inside strings: JSON strings can't contain raw newlines.

## Actions

An action is an operation that changes something: close a ticket, resolve an
alert, add a comment. Mark it `"action": true`. Then:

- Erynne and Claude never call it. They propose it with the values filled in,
  and the person sees a card (in chat, or on Mnemerynne's *Changes waiting for
  you* page when Claude proposed it through the connector) and presses
  *Run it*. Only then is it called, after checking again that they may.
- It may use `PUT`, `PATCH` or `DELETE` as well as `POST`. A `PUT` or `DELETE`
  may have no body (e.g. `PUT /alerts/{alertId}` to resolve an alert).
- It's never cached, and an admin's *Test* asks for confirmation, because it
  really makes the change.
- Questions are never routed straight to it; only the agent can propose it.

Make each action do **one thing** and send only its own fields, e.g. *Change
ticket status* sends only the status. Then an optional parameter left empty can
never blank another field. Pair actions with a reading operation that shows the
current state (e.g. *Ticket*), so the AI checks before it proposes, and with
one that lists valid values when they're account-specific (e.g. *Ticket
statuses*).

Put actions in a **separate template** from the reading operations (e.g.
"Atera" and "Atera (read-write)"), so they get their own credential and AD
group. See [atera-read-write.json](atera-read-write.json).

## Description

The template's `description` is the admin's setup sheet, shown on the API's
page. Include:

1. how to issue a **read-only** credential for Mnemerynne (menu path, scope name);
2. how to check the server's certificate fingerprint on the host, e.g.
   `openssl s_client -connect localhost:443 </dev/null | openssl x509 -noout -fingerprint -sha256`;
3. which AD group should see the data;
4. whether the data is safe to send to Claude (Anthropic). If any operation could
   return personal data (guest names, employee details, contact info), say so
   plainly and tell the admin to leave Claude's switch off.

## Rules of thumb

- **Read-only, unless it's an action.** Back reading operations with a
  read-only credential; put actions in their own template with their own
  credential. The credential is your real safety boundary.
- **Answer questions, return little.** Aggregate on your side; cap rows (e.g.
  `maxRows`, `LIMIT`); select only the columns that answer the question.
- **No personal data** unless the admin is told, in the description, which
  operations return it.
- **Stable names.** Re-uploading a template replaces it. Pressing *Add preset
  operations* on an existing API then adds operations **whose names it doesn't
  have yet**. Operations already on the API aren't changed, so to change one,
  give it a new name or ask the admin to edit or delete it on the page.

## Checklist before you hand the file over

- [ ] `"format": "mnemerynne-api-template"`, a `name`, an `https://` `base_url`, the right `auth_type`.
- [ ] No secrets, certificates, groups or Claude settings in the file.
- [ ] Each operation: a clear `description`, 4–6 example questions in staff's words, `enum`/`int`/`date` parameters where possible, defaults for optional ones.
- [ ] POST bodies: every `{{name}}` matches a body parameter; a string body where a placeholder stands bare.
- [ ] Responses are small JSON and contain no personal data, or the description says which do.
- [ ] The `description` tells the admin how to make a read-only credential and check the certificate.
- [ ] Anything that changes data is `"action": true`, does one thing, and lives in its own template.
- [ ] The file is valid JSON (`python -m json.tool your-template.json`).
