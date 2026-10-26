# Remote apps

**Access → Remote** lists LAN-only web apps that people may open through Mnemerynne at `https://<your host>/remote/<slug>/`: the balancer's control page, Cockpit, Wazuh, a NAS, a dashboard. Mnemerynne proxies them behind its own sign-in, for the group you pick, and the Remote tab shows them in a window.

## Adding an app

1. On Access → Remote enter a **slug** (lower-case letters, digits and dashes: it becomes the address, `/remote/balancer/`), a **name**, and the **address on the network** as this server reaches it (`http://10.0.5.20:9180`, `https://127.0.0.1:9090`, with an optional base path).
2. Pick **who may open it**: an AD or Entra group (several separated by `;`). Admins always can; with no group, only admins.
3. For a server with a self-signed certificate, **Fetch from server**, compare the SHA-256 fingerprint with the real one, and **Save certificate**: only that certificate is then trusted for this app. Untick *Check the name in the certificate* for a server reached by IP.
4. Choose how the Remote tab shows it: **in the window** (the default) or **in a new tab** (for apps that refuse to work inside a frame).
5. **Enabled** takes an app out of everyone's list without deleting it.

Every change is in Recent changes (`remote.*`).

## Base paths and link rewriting

The app is served under a prefix, so links it writes as `/static/app.js` would miss. Most apps can be told their prefix; Mnemerynne also sends `X-Forwarded-Prefix: /remote/<slug>` with every request, which some honour on their own.

| App | Setting |
|---|---|
| Cockpit | `/etc/cockpit/cockpit.conf`: `[WebService]` `UrlRoot=/remote/cockpit`, `Origins=https://<your host>`, `ProtocolHeader=X-Forwarded-Proto` |
| Wazuh dashboard / OpenSearch Dashboards / Kibana | `server.basePath: "/remote/wazuh"`, `server.rewriteBasePath: true` |
| Grafana | `root_url = https://<your host>/remote/grafana/`, `serve_from_sub_path = true` |
| The balancer page | relative links; nothing to set |

For apps that can't be told, switch on **Rewrite absolute links in its pages**: Mnemerynne then rewrites `href`, `src`, `action`, `srcset`, `url()` and the plainest `fetch("/…")` calls in HTML pages. It is best effort: URLs an app builds at runtime from `location.origin`, service workers and links inside JSON stay as they are. Prefer the app's own setting; use rewriting as a fallback and test the app.

## What the proxy does

- Streams requests and responses both ways, websockets included (Cockpit's terminal, live dashboards), and never follows redirects itself: redirects, `Set-Cookie` paths and absolute links on the app's own host are rewritten to the prefix.
- Never sends Mnemerynne's session cookie to the app, strips hop-by-hop and spoofable forwarding headers, and sets `X-Forwarded-For`, `-Proto`, `-Host` and `-Prefix`.
- Drops the app's `X-Frame-Options` and `frame-ancestors` so the Remote window can show it; Mnemerynne's own headers then allow framing by Mnemerynne only. Cookies named like Mnemerynne's own are dropped.
- Someone opening an app's address while signed out signs in and returns to it. Group membership is checked on every request, so removing someone from the group cuts them off within the snapshot's few minutes.

## Trust

A proxied app's pages run on Mnemerynne's own address, so scripts in them could call Mnemerynne's API as the person who opened them. List only the organisation's own tools, for the groups that should have them; a tool you don't control belongs in a new tab on its own address, not here.

## Behind nginx

Caddy passes websockets through as it is. With nginx, use the `/remote/` block in `deploy/linux/nginx-mnemerynne.conf` (HTTP/1.1, `Upgrade` and `Connection` headers, no buffering, a long read timeout).

## When it doesn't work

- **"didn't answer"**: the address is wrong, the server is down, or this box can't reach it (firewall, SELinux `httpd_can_network_connect` is for Caddy, not Mnemerynne). Try `curl` from the box as the service user.
- **Certificate errors**: pin the certificate, or untick the hostname check for a server reached by IP.
- **The app loads but looks broken**: it needs its base path set (table above) or link rewriting; or it refuses to be framed: switch it to *new tab*.
- **A websocket app disconnects at once**: the app checks `Origin` (Cockpit's `Origins=`), or nginx lacks the upgrade block.
