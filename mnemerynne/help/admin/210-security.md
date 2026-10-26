# Security and internet exposure

## What stays on the box

Documents, questions, answers, memories, mail and the call logs live on this server. The only things that leave it are: what Claude reads, for people who connect Claude and only within what admins shared ([Claude](claude.md)); and document text from `TEACHER_ALLOWED_LIBRARIES` when a remote teacher is used for training data. Tracing to LangSmith is forced off in code.

## Built-in protections

- **Sessions**: a signed cookie, `SameSite=Lax`, HTTPS-only whenever the public URL is https, 24 hours, re-checked against the database on every request (a deactivated account or a password reset ends sessions at once). State-changing requests from other sites are refused (cross-site guard).
- **Headers** on every response: no framing by other sites, no MIME sniffing, no full URLs in Referer, HSTS over HTTPS. The file viewer and help pages carry a strict Content-Security-Policy; file previews are sandboxed and never render SVG inline.
- **Sign-in throttling**: 5 wrong passwords for a username from an address, or 20 across usernames, lock for 5 minutes. Passwords are at least 8 characters, hashed with passlib.
- **Rate limits per client IP** on what anyone can reach: sign-in (30 a minute), app registration (30 an hour), the other OAuth endpoints and Claude's text links; MCP calls per token (60 a minute). Over the limit is a 429 with `Retry-After`. The real client IP comes from the proxy (`FORWARDED_ALLOW_IPS=127.0.0.1`).
- **Admin networks**: with `MNEMERYNNE_ADMIN_NETWORKS` set, admin rights only count from those networks; elsewhere admins are ordinary users (no Access page, no admin API).
- **The secret key**: the web app refuses to start when the public URL is set (or it listens beyond loopback) and `MNEMERYNNE_SECRET_KEY` is missing, the example value, or under 32 characters.
- **Credentials at rest**: API secrets, Microsoft 365 tokens, the balancer key and people's Claude keys are encrypted with a key derived from the secret key and never shown again.
- **The AI never writes a request**: internal APIs are fixed operations with typed, checked parameters; actions are proposals; Claude can't delete or widen its own access; every file change keeps a version.
- **Files**: uploads are sniffed by content; names are cleaned; the share is written through temp files and renames; Office lock files are respected.
- **Logs**: Claude's calls, API calls (with responses), the agent's calls and the access log record who did what; they are pruned by the retention settings.

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

## Cutting someone off

Deactivate the account (Access → Accounts → Can sign in): sessions and Claude connections end at once. Or only **Revoke Claude tokens**. Removing them from a domain group takes effect within 5 minutes through the AD snapshot.
