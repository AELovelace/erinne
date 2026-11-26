# Internal APIs

Erynne can answer live questions from internal systems: "did every hotel send its night audit?", "any critical Wazuh alerts?", "what's the status of ticket 4812?". Admins set them up on **Access → APIs**.

## Adding an API

Pick a starting point in the box above the list and press **Add**:

- **Presets**: Wazuh (the indexer: alerts with server-side filters, timeline, noise report, agent silence, vulnerabilities, packages, index health and retention), Wazuh manager (agents, versions, configuration with secrets redacted, SCA, FIM, logs, API users, open ports), Wazuh manager actions (active response, restart, upgrade, groups; proposals only), Atera (tickets, statuses, customers, alerts), Fleetboard and Fleetboard (changes) (server load, disks, forecasts and history; see *Fleetboard* below), and UniFi Network and OPNsense (one console or firewall each, read-only; see *UniFi Network and OPNsense* below). See [Wazuh](wazuh.md) for the users and roles each needs. Picking a preset asks for the address, since built-in presets carry example ones.
- **Uploaded templates**: a JSON file a project hands you, describing its operations. **Upload template…** adds it to the list; re-uploading a file with the same name replaces it. The format is documented in `api-templates/api-package-guide.md` in the repository.
- **Blank**: define operations by hand.

Then, on the API's page:

1. **Credential**: pasted once, stored encrypted with a key derived from `MNEMERYNNE_SECRET_KEY`, never shown again. Use dedicated **read-only** credentials (a Wazuh user with a read-only role, an Atera token limited to tickets). If the secret key ever changes, every credential must be entered again.
2. **Certificate**: for self-signed servers press *Fetch from server*, compare the SHA-256 fingerprint with the real one, and save it; only that certificate is then trusted. For servers reached by IP, untick *Check the name in the certificate*.
3. **Who**: one AD or Entra group (or several, `;`-separated). People outside it never see the API, in chat or from Claude.
4. **Claude can query this**: off by default. On means whatever Claude asks goes to the API and the response goes to Anthropic; read the template's description for whether the data is safe to send.
5. **One at a time**: for servers that answer only one request at a time (429 while another runs); calls are then queued.

## Operations

Each operation is a fixed request: a path (which may carry a fixed query, like `/agents?select=id,name`), GET or a POST with a fixed body, and **typed parameters** (int with limits, date, enum from a list, short string, a list of up to 50 short values, free text for actions) that the model fills in. In a body, `{"$if": "name", "then": {…}}` is an optional part, left out when that parameter is empty: that's how the Wazuh alert operations take filters like "only these rule IDs" without a default that would hide everything else. The model never writes a request itself; it picks an operation and fills its parameters, which are checked against the types and limits and escaped into the body. Redirects aren't followed, responses are capped at 256 KB, calls time out after 20 seconds, are cached briefly (`cache_seconds`) and logged.

- **Example questions** decide routing: a chat question goes to the operation whose examples are closest in meaning, if at least `MNEMERYNNE_API_ROUTE_THRESHOLD` (0.80) close. Give each operation 4 to 6 examples phrased the way staff ask, distinct from other operations' examples.
- **Test** calls the operation now and lists the fields in the response. For actions it asks for confirmation, because it really makes the change.
- **Add preset operations** on an existing API adds the preset's operations whose names it doesn't have yet; existing ones aren't changed.
- **Update preset operations** does that and also rewrites the operations named like the preset's to its current version (path, body, parameters, shape, description, examples). It keeps whether each is enabled or needs an admin's approval, and leaves operations of the API's own alone.
- **Sign in first at** (on *Connection*, for *User name and password*): for APIs that swap the user name and password for a token, like the Wazuh manager's `/security/user/authenticate`. The token is reused for 10 minutes, and fetched again if the API refuses it sooner.

## Response shapes

An operation's **Response shape** boils its answer down on the server, before it is cached, logged, shown on a call's page or handed to Erynne or Claude. It's JSON; leave it empty to keep the answer as the API sent it.

| Key | Effect |
|---|---|
| `"format": "elasticsearch"` | A `_search` answer becomes `{total, rows, <each aggregation>}`: hits become flat rows of their `_source`, aggregations become plain lists (`[{"key": "site-chan", "count": 21604, …sub-aggregations}]`), metrics become their value, and `_shards`, `_index`, `_id`, sort keys go. `<name>_others` says how many fell outside a top list; `total_is_at_least` means the count was capped. Whole-number metrics are whole (`3`, not `3.0`), and a `_cat` answer (`?format=json`, a bare list) becomes `{total, rows}`. |
| `"format": "wazuh"` | The Wazuh manager's `{data: {affected_items, total_affected_items}}` becomes `{total, rows}` (plus `failed` when some items failed). |
| `"keep": ["rule.level", "policy.states.name"]` | Only these fields of each row (dotted paths, mapped through lists). |
| `"rename": {"rule.level": "level"}` | Shorter names for fields. |
| `"redact": true` | Passwords, keys, tokens and webhook addresses become `[redacted]`, in fields and in text. yes/no switches (`use_password: yes`) stay. |
| `"flatten": false` | Keep nested objects instead of `rule.level`-style keys. |
| `"single": ["description", "rule_id"]` | With `elasticsearch`: these sub-aggregations (size-1 top lists) always come back as just their top key (or `null`), and every other top list always stays a list, so a field never changes type between answers. Without `single`, a one-item list that covers its whole bucket is guessed to be a single value. |
| `"merge": {"sources": ["src_ip", "win_ip"]}` | With `elasticsearch`: these top lists (one per field the same thing can live in) are folded into the named one. Counts are added by key, biggest first, and the list is as long as the longest; the folded-in names go. |

The MCP connector now hands Claude the answer as JSON rather than as a string of JSON, so shaped answers cost far fewer tokens.
- **Recent calls** shows who asked what. A citation in chat opens the exact response the answer was based on, for the person who asked and for admins. That response is stored with the call, so answers from Atera keep ticket text in Mnemerynne's database for `MNEMERYNNE_API_CALLS_DAYS`.

## Actions (operations that change data)

An operation marked **changes data** (close a ticket, resolve an alert, add a note) is never called by the AI. Erynne and Claude **propose** it with the values filled in; the person sees a card (in chat, or on *Changes waiting for you* when Claude proposed it) and presses **Run it**. Only then is it called, after checking again that they may still use it. Actions may use PUT, PATCH or DELETE, are never cached, and are never routed to directly.

**Needs an admin's approval**, on an action, sends the proposal to Access → Approvals instead: the person's Claude is told it's with the admins, an admin approves (it runs on the requester's behalf, with the requester's group checked again) or rejects with a note. See [Approvals](approvals.md).

Keep actions in their own API or template with their own credential and group, separate from the reading operations, so the reading credential stays read-only.

## Atera

The built-in Atera preset holds the reading operations and, since 1.0.1, the changes too: Contact by email (reads personal data), Create ticket, Change ticket status, Change ticket priority, Add internal note and Resolve alert, all actions the person runs. One Atera entry with one credential covers both; delete the change operations from it to keep an entry read-only, and edit Add internal note's body to the technician the notes should come from. An existing Atera API gets the new operations with **Add preset operations**. The `atera-read-write.json` template remains the way to give the changes a separate credential and group; an install that added both "Atera" and "Atera (read-write)" should delete the second once the first has the actions, so Claude sees one entry.

## Fleetboard

Fleetboard (the server-load dashboard on this box, also a Remote app) has a service API for Erynne and Claude, and two built-in presets for it: **Fleetboard** (read-only) and **Fleetboard (changes)**. Pick each in the box above the list and press **Add**; the address `http://127.0.0.1:9281/api/v1` is already right. They are read at start from `api-templates/fleetboard.json` and `fleetboard-actions.json` in the repository, with the same checks as an upload, so an update to those files updates the presets (**Update preset operations** brings an existing entry up to date). An uploaded template can't use either name.

- **Reads** (Fleetboard): Summary (up/down/silent, busiest, fullest disks, issues: the morning brief), Hosts (one lean row per host; `status=silent` lists hosts that went quiet), Host detail (`host=` id, name or hostname), Over thresholds, Disk forecast ("full in about N days", from 90 days of daily history), History (`metric` cpu, mem, load, temp, disk:<mount>, net:<interface>, gpu:<n>, over hours or days), Client audit (where each client listens, its binary's SHA-256, flags for exposed, unexpected or old clients), Activity (Fleetboard's audit log), Thresholds, Token check.
- **Changes** (Fleetboard (changes), proposals the person runs): Add host, Rename host, Pause monitoring, Resume monitoring, Remove host (deletes its history; tick **Needs an admin's approval**), Set threshold (also moves the Wazuh disk and offline alerts). Each needs a reason, which Fleetboard's audit log keeps next to *Mnemerynne/Claude (token …)*. None of them runs anything on a machine: Fleetboard's clients have no write routes.
- **Credentials**: two Fleetboard service tokens, made on the box with `sudo -u fleetboard fleetboard-server token add mnemerynne-read` and `… token add mnemerynne-act --scope act`. Each is printed once and stored in Fleetboard only as a hash. They are not the shared client token. A read token can't make changes even if pasted into the wrong API (Fleetboard answers 403 and logs the refusal). `fleetboard-server token revoke NAME` cuts one off at once; `fleetboard-server calls` lists every call.
- **Address** `http://127.0.0.1:9281/api/v1`, no certificate (loopback). Group: IT.
- **Claude**: the answers hold host names, addresses, OS versions and client hashes, no personal data about people. Turn *Claude can query this* on if that inventory may go to Anthropic.

Fleetboard's own `docs/API.md` lists every route and field.

## UniFi Network and OPNsense

Two read-only built-in presets for the network, read at start from `api-templates/unifi-network.json` and `opnsense.json` with the same checks as an upload (**Update preset operations** brings an entry up to date after those files change). Each entry talks to **one** console or firewall: for another, add the preset again under a new name and address. Both carry a `.example` address, so set the real one. Each template's description has the full setup.

- **UniFi Network** (the console's own Integration API; *UniFi Site Manager* is the cloud one): Application info, Sites, Devices, Device, Device statistics (CPU, memory, uplink, TX retries), Clients. Address `https://<console IP>/proxy/network/integration` (UniFi OS Server, the self-hosted one: `https://<server IP>:11443/proxy/network/integration`), credential an API key from *UniFi Network → Settings → Control Plane → Integrations* (sent as `X-API-KEY`). **Clients leaves out names**, since on a hotel network they're often guests' own devices. Hotspot vouchers aren't included (guest access codes).
- **OPNsense**: System information, Uptime and load, Memory, Firmware status, Gateways, Interfaces, Interface traffic, Firewall rules, Port forwards, Aliases (credentials of URL aliases left out), Services, IDS status, ARP table, Firewall log. Address `https://<firewall IP>/api`, credential **basic** with the API key as the user name and the secret as the password, from a user made just for this (System → Access → Users → + API key). OPNsense privileges mostly allow changes too, so the read-only part is Mnemerynne's fixed GETs.
- **Certificates**: both have self-signed certificates whose name isn't the IP, so the templates turn the host name check off. **Trust (pin) the certificate** on the API: it's still checked on every call.
- **Group** IT. **Claude**: the answers hold internal addresses, MAC addresses and rule text (no guest data); turn *Claude can query this* on only if that may go to Anthropic.

**Batches.** Claude can propose several actions as one proposal (`propose_batch`, at most `MNEMERYNNE_PROPOSAL_BATCH_MAX` = 25): one card, one link, a tick box per change. If any item needs an admin's approval the whole batch goes to Approvals.

## Writing a template

The guide in `api-templates/api-package-guide.md` covers the file format, parameter rules, bodies with `{{name}}` placeholders, actions, and what to put in the description (how to make a read-only credential, how to check the certificate, which group, whether the data may go to Claude). A template is refused if it tries to carry secrets, certificates, the group or the Claude switch: those are always set by hand, on the API.
