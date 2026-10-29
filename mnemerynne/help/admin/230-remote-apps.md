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

Two rules for an app that is told its prefix:

- **The slug and the prefix are the same word**: slug `cockpit`, prefix `/remote/cockpit`.
- **The app's address in Mnemerynne ends with that prefix** (`https://127.0.0.1:9090/remote/cockpit`), because the app now expects the prefix on its own side too. The address is the app's base, never a page inside it: with `/app/login` on the end, every path the browser asks for is appended to the login page and the app redirects in a loop.

| App | Setting |
|---|---|
| Cockpit | `/etc/cockpit/cockpit.conf`: `[WebService]` `UrlRoot=/remote/cockpit`, `Origins=https://<your host>`, `ProtocolHeader=X-Forwarded-Proto` |
| Wazuh dashboard / OpenSearch Dashboards / Kibana | `server.basePath: "/remote/wazuh"`, `server.rewriteBasePath: true` |
| Grafana | `root_url = https://<your host>/remote/grafana/`, `serve_from_sub_path = true` |
| The balancer page | relative links; nothing to set |

### Cockpit, step by step

Cockpit on the Mnemerynne box itself (port 9090), as set up on 2026-10-28:

1. On the box, put this in `/etc/cockpit/cockpit.conf` (the file is empty by default), with `Origins` exactly what people type for Mnemerynne, scheme and host; several separated by a space:
   ```ini
   [WebService]
   UrlRoot=/remote/cockpit
   Origins=https://mnemerynne.example.com
   ProtocolHeader=X-Forwarded-Proto
   ```
   then `sudo systemctl restart cockpit.socket cockpit`.
2. Check that the prefix took: `curl -sk https://127.0.0.1:9090/remote/cockpit/ | grep base` prints `<base href="/remote/cockpit/">`, and `curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:9090/remote/cockpit/static/login.js` prints `200`.
3. Access → Remote → add: slug `cockpit`, address `https://127.0.0.1:9090/remote/cockpit`, **Fetch from server**, **Save certificate**, untick *Check the name in the certificate*, *Rewrite absolute links* off, in the window.
4. Remote tab → Cockpit shows the login form. A sign-in that spins is the websocket being refused: `Origins` doesn't match the address bar.

### Wazuh dashboard, step by step

The dashboard on another box (port 443):

1. On the Wazuh box, add to `/etc/wazuh-dashboard/opensearch_dashboards.yml` (spaces, not tabs):
   ```yaml
   server.basePath: "/remote/wazuh"
   server.rewriteBasePath: true
   ```
   then `sudo systemctl restart wazuh-dashboard`.
2. Check from the Mnemerynne box: `curl -sk -o /dev/null -w '%{http_code} -> %{redirect_url}\n' https://<wazuh ip>/remote/wazuh/` prints `302 -> https://<wazuh ip>/remote/wazuh/app/login`. A `000` is the Wazuh host's firewall not allowing this box on 443; a `404` means the two lines aren't in effect.
3. Access → Remote → add: slug `wazuh`, address `https://<wazuh ip>/remote/wazuh` (the base, not `/app/login`), Fetch and Save the certificate, untick the name check, rewriting off.
4. Remote tab → Wazuh shows the sign-in form; its session cookie comes back scoped to `/remote/wazuh/`.

For apps that can't be told, switch on **Rewrite absolute links in its pages**: Mnemerynne then rewrites `href`, `src`, `action`, `srcset`, `url()` and the plainest `fetch("/…")` calls in HTML pages. It is best effort: URLs an app builds at runtime from `location.origin`, service workers and links inside JSON stay as they are. Prefer the app's own setting; use rewriting as a fallback and test the app.

## Look: Erynne's style for RD Web Access

With **Rewrite absolute links** on, RD Web Access's pages are XML that Mnemerynne transforms itself (Firefox draws them blank in a frame, and browsers are dropping XSLT).

**Look** sets how those pages are drawn:
- **The app's own:** Microsoft's stylesheet, as RD Web sends it.
- **Erynne (RD Web Access):** a stylesheet that comes with Mnemerynne (`mnemerynne/remote_overrides/rdweb/Site.xsl`), covering sign-in, password and error pages, and the app list: a card for each app and folder, sorted by name, with a Launch button that downloads the app's `.rdp`. It works on phones too.

How the Erynne look behaves:
- RD Web still signs people in and hands out the `.rdp` files, and nothing changes on the RD Web server. Its own scripts still run, so the idle sign-out and the sign-in checks work as before.
- **Launch** downloads the `.rdp`; opening it starts the app in Remote Desktop Connection. The old Internet Explorer launcher (ActiveX) isn't offered.
- *Connect to a remote PC* stays hidden, as RD Web hides it itself outside Internet Explorer.
- **Theme** follows each viewer's Erynne theme, or fixes it to Default or Neon. Default (Sakura) also follows the device's light or dark mode. Themes built on other bases draw as Default.
- If the Erynne look ever fails on a page, that page falls back to the app's own stylesheet.

A look needs link rewriting on; the setting refuses otherwise.

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

The browser only sees `<app> didn't answer: ConnectError`; the reason is in the web service's journal:

```
sudo journalctl -u mnemerynne-web -g "didn't answer" -n 3 --no-pager
```

| Symptom | Cause | Where to look |
|---|---|---|
| `didn't answer: ConnectError`, journal says `CERTIFICATE_VERIFY_FAILED` | the certificate isn't saved; or `IP address mismatch`: the name check is on for a server reached by IP | Access → Remote → the app: Fetch, Save, untick the name check |
| `didn't answer`, journal says `Connection refused` or a timeout | wrong port, the server is down, or its firewall doesn't allow this box (SELinux's `httpd_can_network_connect` is for Caddy, not Mnemerynne) | `curl -sk` from the Mnemerynne box |
| A blank white page | the app's prefix setting isn't in effect (Cockpit's login page stays hidden until its script loads), or you opened the app directly without the trailing slash | the app's curl check above |
| "The page isn't redirecting properly" | the address in Mnemerynne ends with a page path, or the prefix setting is missing on the app | the `302 ->` curl line |
| The app loads but looks broken | it needs its base path set (table above) or link rewriting; or it refuses to be framed: switch it to *new tab* | Firefox F12 → Console |
| A websocket app disconnects at once | the app checks `Origin` (Cockpit's `Origins=`), or nginx lacks the upgrade block | F12 → Network → WS |
| Signed out of the app on every reload | Mnemerynne opened over plain `http://`, so the app's `Secure` cookie is dropped by the browser | use the https address |
