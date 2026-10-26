# Internal APIs

Erynne can answer live questions from internal systems: "did every hotel send its night audit?", "any critical Wazuh alerts?", "what's the status of ticket 4812?". Admins set them up on **Access → APIs**.

## Adding an API

Pick a starting point in the box above the list and press **Add**:

- **Presets**: Wazuh (alert summary, alerts, failed logins, with a time window and a machine) and Atera (tickets, statuses, customers, alerts).
- **Uploaded templates**: a JSON file a project hands you, describing its operations. **Upload template…** adds it to the list; re-uploading a file with the same name replaces it. The format is documented in `api-templates/api-package-guide.md` in the repository.
- **Blank**: define operations by hand.

Then, on the API's page:

1. **Credential**: pasted once, stored encrypted with a key derived from `MNEMERYNNE_SECRET_KEY`, never shown again. Use dedicated **read-only** credentials (a Wazuh user with a read-only role, an Atera token limited to tickets). If the secret key ever changes, every credential must be entered again.
2. **Certificate**: for self-signed servers press *Fetch from server*, compare the SHA-256 fingerprint with the real one, and save it; only that certificate is then trusted. For servers reached by IP, untick *Check the name in the certificate*.
3. **Who**: one AD or Entra group (or several, `;`-separated). People outside it never see the API, in chat or from Claude.
4. **Claude can query this**: off by default. On means whatever Claude asks goes to the API and the response goes to Anthropic; read the template's description for whether the data is safe to send.
5. **One at a time**: for servers that answer only one request at a time (429 while another runs); calls are then queued.

## Operations

Each operation is a fixed request: a path, GET or a POST with a fixed body, and **typed parameters** (int with limits, date, enum from a list, short string, free text for actions) that the model fills in. The model never writes a request itself; it picks an operation and fills its parameters, which are checked against the types and limits and escaped into the body. Redirects aren't followed, responses are capped at 256 KB, calls time out after 20 seconds, are cached briefly (`cache_seconds`) and logged.

- **Example questions** decide routing: a chat question goes to the operation whose examples are closest in meaning, if at least `MNEMERYNNE_API_ROUTE_THRESHOLD` (0.80) close. Give each operation 4 to 6 examples phrased the way staff ask, distinct from other operations' examples.
- **Test** calls the operation now and lists the fields in the response. For actions it asks for confirmation, because it really makes the change.
- **Add preset operations** on an existing API adds the preset's operations whose names it doesn't have yet; existing ones aren't changed.
- **Recent calls** shows who asked what. A citation in chat opens the exact response the answer was based on, for the person who asked and for admins. That response is stored with the call, so answers from Atera keep ticket text in Mnemerynne's database for `MNEMERYNNE_API_CALLS_DAYS`.

## Actions (operations that change data)

An operation marked **changes data** (close a ticket, resolve an alert, add a note) is never called by the AI. Erynne and Claude **propose** it with the values filled in; the person sees a card (in chat, or on *Changes waiting for you* when Claude proposed it) and presses **Run it**. Only then is it called, after checking again that they may still use it. Actions may use PUT, PATCH or DELETE, are never cached, and are never routed to directly.

**Needs an admin's approval**, on an action, sends the proposal to Access → Approvals instead: the person's Claude is told it's with the admins, an admin approves (it runs on the requester's behalf, with the requester's group checked again) or rejects with a note. See [Approvals](approvals.md).

Keep actions in their own API or template with their own credential and group, separate from the reading operations, so the reading credential stays read-only.

## Atera today

The built-in Atera preset holds the reading operations; the actions (create ticket, change status or priority, add an internal note, resolve an alert) come from the `atera-read-write.json` template. Until they are folded into one preset, an install that added both shows two Atera entries to Claude, with some reading operations under each; that clean-up is on the 1.0.1 roadmap.

## Writing a template

The guide in `api-templates/api-package-guide.md` covers the file format, parameter rules, bodies with `{{name}}` placeholders, actions, and what to put in the description (how to make a read-only credential, how to check the certificate, which group, whether the data may go to Claude). A template is refused if it tries to carry secrets, certificates, the group or the Claude switch: those are always set by hand, on the API.
