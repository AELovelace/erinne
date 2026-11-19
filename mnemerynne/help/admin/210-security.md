# Security and internet exposure

## What stays on the box

Documents, questions, answers, memories, mail and the call logs live on this server. The only things that leave it are: what Claude reads, for people who connect Claude and only within what admins shared ([Claude](claude.md)); and document text from `TEACHER_ALLOWED_LIBRARIES` when a remote teacher is used for training data. Tracing to LangSmith is forced off in code.

## Built-in protections

- **Sessions**: a signed cookie, `SameSite=Lax`, HTTPS-only whenever the public URL is https, 24 hours, re-checked against the database on every request (a deactivated account or a password reset ends sessions at once). State-changing requests from other sites are refused (cross-site guard). Signing in returns people to the page that asked for it.
- **Claude's API links**: the link Claude gets with an API answer carries a key signed with `MNEMERYNNE_SECRET_KEY`. It opens that one call's page without signing in, because Claude Desktop often opens links in a browser that isn't signed in. It shows only what Claude was already sent, and stops working if the person's account is deactivated or the secret key changes.
- **Headers** on every response: no framing by other sites, no MIME sniffing, no full URLs in Referer, HSTS over HTTPS. The file viewer and help pages carry a strict Content-Security-Policy; file previews are sandboxed and never render SVG inline.
- **Sign-in throttling**: 5 wrong passwords for a username from an address, or 20 across usernames, lock for 5 minutes. Passwords are at least 8 characters, hashed with passlib.
- **Rate limits per client IP** on what anyone can reach: sign-in (30 a minute), app registration (30 an hour), the other OAuth endpoints and Claude's text links; MCP calls per token (60 a minute). Over the limit is a 429 with `Retry-After`. The real client IP comes from the proxy (`FORWARDED_ALLOW_IPS=127.0.0.1`).
- **Admin networks**: with `MNEMERYNNE_ADMIN_NETWORKS` set, admin rights only count from those networks; elsewhere admins are ordinary users (no Access page, no admin API).
- **The secret key**: the web app refuses to start when it is reachable from outside — the public URL is set, it listens beyond loopback, **or it runs behind a reverse proxy (`FORWARDED_ALLOW_IPS` set, as the front door sets it)** — and `MNEMERYNNE_SECRET_KEY` is missing, the example value, or under 32 characters. So a front-door install that bound to `127.0.0.1` but forgot `PUBLIC_URL` is still protected.
- **Credentials at rest**: API secrets, Microsoft 365 tokens, the balancer key and people's Claude keys are encrypted with a key derived from the secret key and never shown again.
- **The AI never writes a request**: internal APIs are fixed operations with typed, checked parameters; actions are proposals; Claude can't delete or widen its own access; every file change keeps a version.
- **Files**: uploads are sniffed by content; names are cleaned; the share is written through temp files and renames; Office lock files are respected.
- **Logs**: Claude's calls, API calls (with responses), the agent's calls and the access log record who did what; they are pruned by the retention settings.
- **Remote apps** ([Remote apps](remote-apps.md)) run on Mnemerynne's own address for whoever opens them, so their scripts could call Mnemerynne's API as that person. The proxy never forwards the session cookie upstream and checks the group on every request, but the app itself is trusted: list only the organisation's own tools, for the groups that should have them.

## Opening the box to the internet

The claude.ai connector needs `/mcp`, `/.well-known/oauth-*` and `/oauth/*` reachable from Anthropic's servers, and with them the sign-in page and everything behind it.

1. A public hostname pointing at the router's public address, ports 80 and 443 forwarded (80 only for Let's Encrypt's check; Caddy redirects it).
2. Use `deploy/linux/Caddyfile-mnemerynne-public`: it drops the internal CA (Caddy fetches a Let's Encrypt certificate) and answers 404 to the admin console from outside the admin networks.
   ```sh
   sed -e 's/erynne.example.org/YOUR-HOST/' -e 's#192.168.1.0/24#YOUR-ADMIN-NETWORKS#' \
     /opt/mnemerynne/deploy/linux/Caddyfile-mnemerynne-public > /tmp/mnemerynne-public.caddy
   sudoedit /etc/caddy/Caddyfile      # replace the old Mnemerynne site with it
   sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
   sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
   ```
3. In the settings file: `MNEMERYNNE_PUBLIC_URL=https://YOUR-HOST`, `MNEMERYNNE_ADMIN_NETWORKS=` the same networks, and remove any `MNEMERYNNE_SESSION_HTTPS_ONLY=false`. Restart `mnemerynne-web`.
4. Check from outside: `curl -sI https://YOUR-HOST/login` shows `strict-transport-security` and `x-frame-options`, and `/admin/libraries` is a 404.

Staff on the LAN who open the public hostname may arrive from the router's address (hairpin NAT) and lose admin rights; point the public hostname at the box's LAN address in internal DNS.

## Internal CA instead

On a LAN-only install Caddy's `tls internal` is its own CA. Install its root certificate (`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`) on staff PCs as a trusted root (Group Policy for many), and give it to the Claude Desktop extension as `ca.pem` when packing.

## Shares and links from Files

People can share files and folders from Files (**Share**: [Files](../user/files.md#sharing)) with a person, a Mnemerynne or domain group, everyone with an account, or anyone with a link. These live in the database (`item_shares`, `mnemerynne/sharing.py`) and never change the share's permissions on disk.

- **A share only works while its maker could still do it themselves.** If their account is switched off or they lose access to the item, every share and link they made stops at once, and comes back if they get access back. Nothing is re-shared: what reaches someone through a share can't be shared on.
- **Links** (`/s/<token>`) need no sign-in, so they reach the internet wherever the box does. The token is 256 bits made from `MNEMERYNNE_SECRET_KEY` and a random value saved with the share. The database keeps only its SHA-256, so a copy of the database alone can't open any link. **Changing the secret key turns every link off**, which is also the way to end them all at once. Links are always view-only and always download (nothing a person uploaded is shown as a page), with `no-referrer`, `noindex` and no caching. Every dead link (wrong, expired, turned off, binned) is the same bare 404, and `/s/` allows 60 asks a minute per address.
- To end someone's links without switching their account off, remove them in Files' Share dialog (anyone who can change the item can). To see every link, query `SELECT * FROM item_shares WHERE kind = 'link'` on the box.

## Cutting someone off

Deactivate the account (Access → Accounts → Can sign in): sessions and Claude connections end at once, and every share and link they made stops working. Or only **Revoke Claude tokens**. Removing them from a domain group takes effect within 5 minutes through the AD snapshot.
