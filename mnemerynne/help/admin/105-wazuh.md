# Wazuh

Three built-in presets on **Access → APIs** cover Wazuh. Each has its own address and credential, so add the ones you want as separate APIs:

| Preset | Talks to | What it answers |
|---|---|---|
| **Wazuh** | the indexer, port 9200 | alerts (summary, list, failed logins, one IP, timeline, noise report, agent silence), vulnerabilities, installed packages, indexer health, disk, indices and retention |
| **Wazuh manager** | the manager API, port 55000, read-only | agents and their status, versions, outdated agents, manager configuration, agent and group configuration, SCA (CIS) results, FIM last scan, manager logs, API users and roles, rule details, an agent's packages and open ports, upgrade tasks |
| **Wazuh manager actions** | the manager API, with a read-write user | run an active response (e.g. block an IP), restart an agent, upgrade agents, add an agent to a group or take it out. Proposals only: the person runs each one |

When you pick a preset and press **Add**, Mnemerynne asks for the address. The presets carry example addresses (`wazuh-indexer.example`, `wazuh-manager.example`); type your own.

## The indexer user (preset: Wazuh)

Make a dedicated internal user in the Wazuh dashboard (**Indexer management → Security → Internal users**) and give it a role with:

- **Index permissions** on `wazuh-*`: `read` (covers `search`). This is enough for the alert, vulnerability and package operations.
- **Cluster permissions**: `cluster_monitor` for *Indexer health*, *Indexer disk* and *Indices*, and `cluster:admin/opendistro/ism/policy/search` for *Retention policies*. Leave them out and only those four operations fail.

Never use `admin` or `kibanaserver`. Paste the user's password as the credential (authentication *User name and password*), press **Fetch from server** to trust the indexer's certificate, and untick the name check when you reach it by IP.

## The manager API user (preset: Wazuh manager)

The manager API signs in first: Mnemerynne sends the user name and password to `/security/user/authenticate`, gets a token and reuses it for 10 minutes. That's the **Sign in first at** field on the API's page; the preset fills it in.

Make a dedicated API user (Wazuh dashboard: **Server management → Security → Users**, or `POST /security/users`) and a role whose policies allow only reading:

| Action | Resource | Used by |
|---|---|---|
| `agent:read` | `agent:id:*`, `agent:group:*` | Agents, Agent status counts, Outdated agents, Agent configuration |
| `group:read` | `group:id:*` | Groups, Group members, Group configuration |
| `manager:read` | `*:*:*` | Manager info, Manager status, Manager configuration, Manager logs, Manager log summary |
| `cluster:read` | `node:id:*` | Cluster status |
| `sca:read` | `agent:id:*` | SCA results, SCA checks |
| `syscheck:read` | `agent:id:*` | FIM last scan |
| `syscollector:read` | `agent:id:*` | Agent packages, Agent open ports |
| `rules:read` | `rule:file:*` | Rule details |
| `security:read` | `*:*:*` | API users, API roles |
| `task:status` | `*:*:*` | Upgrade tasks |

No `*:update`, `*:delete`, `*:create`, `active-response:command`, `agent:restart` or `agent:upgrade`. **Never use `wazuh` or `wazuh-wui`**: they are administrators, and *API users* will show them so you can check they've had their passwords changed or been removed.

## The actions user (preset: Wazuh manager actions)

A second API user, with a role that allows only `active-response:command`, `agent:restart`, `agent:upgrade` and `agent:modify_group` (on `agent:id:*`) and `group:modify_assignments` (on `group:id:*`). Keeping it a separate API with its own credential means the read-only *Wazuh manager* entry can never change anything, even by mistake.

Erynne and Claude only **propose** these. The person sees a card and presses **Run it**; tick **Needs an admin's approval** on an action to send its proposals to Access → Approvals instead. *Run active response* uses a command configured in the manager's `active-response` section (e.g. `firewall-drop`), so how long a block lasts is that section's `timeout`; check it with *Manager configuration* → `active-response` before relying on it.

Editing `local_rules.xml` isn't offered: Wazuh's API replaces the whole file, so a single proposal could wipe every custom rule. Use *Noise report* and *Rule details* to decide what to tune, and change the file on the manager.

## What's redacted

*Manager configuration*, *Agent configuration*, *Group configuration* and *Manager logs* hide passwords, keys, tokens and webhook addresses (Slack, PagerDuty and the like) on the server, before the answer is cached, logged, shown on a call's page or sent to Claude. Switches such as `use_password: yes` stay visible, so "is enrollment password-protected?" can still be answered. The enrollment password authd writes to its log becomes `[redacted]`.

## Filters and shapes

The alert operations filter **on the indexer**, so they find a rare alert inside a flood instead of only searching the page that came back: `rule_id` and `exclude_rule_id` (lists like `204,31101`), `rule_group`, `text` (words in the rule's description, ignoring case), `srcip` (an IP or a pattern), `exclude_srcip`, and a level range (`min_level`, `max_level`). `top` sets how long each top-10-style list is (5–100). Totals are exact, not capped at 10,000.

Every Wazuh answer comes back boiled down: alerts as one flat row each (`time, level, rule_id, description, agent, srcip, user, location, …`), counts as plain lists (`[{"key": "site-chan", "count": 21604}]`), manager answers as `{total, rows}`. That's the operation's **response shape**; see [Internal APIs](internal-apis.md#response-shapes).

## Bringing an existing Wazuh API up to date

An API made from the older Wazuh preset (four operations) keeps its address, credential and certificate. On its page:

- **Add preset operations** adds the nine new indexer operations.
- **Update preset operations** also rewrites the four it has (Alert summary, Alerts, Failed logins, Alerts from an IP) to the new versions with the filters and shapes. Edits you made to those four are replaced; whether each is enabled stays as it was, and operations you added yourself aren't touched.

Then add **Wazuh manager** (and, if you want the changes, **Wazuh manager actions**) as new APIs.
