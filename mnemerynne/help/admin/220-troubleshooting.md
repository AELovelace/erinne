# Troubleshooting

## Development diagnostics

For your own Claude: name a domain (or Entra) group under **Access → Settings → Development**, and the connector's `diagnose` tool, for the connections of people in that group only, returns raw internals: the three tool-call logs with each failure's error (`calls`, with `failed_only`, `tool`, `since_minutes`), what retrieval scored for a question as you (`search`: every candidate's fused score, keyword and vector ranks, cosine distance, which were chosen, and the not-found gate's decision with its thresholds), a file's indexing state with its jobs and errors (`file`), one passage as indexed (`chunk`), the ingestion queue (`jobs`), the server's last few hundred log lines (`log`) and the effective settings without secrets (`config`). Nothing it returns changes anything. Clear the group when done (no group, no diagnostics); the change is in Recent changes.

## Where to look first

```sh
curl -s http://127.0.0.1:38200/healthz                    # {"status":"ok","schema":NN}
systemctl status mnemerynne-web mnemerynne-worker mnemerynne-generator mnemerynne-embedder
journalctl -u mnemerynne-web -n 50 --no-pager
journalctl -u mnemerynne-worker -f
```

Access → Recent changes for who changed what; Claude activity for Claude; the API's Recent calls for an integration; `agent_calls` in the database for Erynne's tools.

## The web app won't start

- **Secret key**: "refuses to start" with a missing, example or short `MNEMERYNNE_SECRET_KEY` while `PUBLIC_URL` is set or the host isn't loopback. Generate one (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`); note it changes nothing about existing credentials only if it is the *same* key.
- **A migration failed**: the log names the file. The update script's backup under `<data_dir>/update/` is the way back; `mnemerynne-admin update` undoes a failed update by itself.
- **Admin networks typo**: a bad CIDR in `MNEMERYNNE_ADMIN_NETWORKS` fails at start, on purpose.

## Answers

- **"Model server down" / errors on every question**: the generator isn't answering. `journalctl -u mnemerynne-generator`; check VRAM with `nvtop`; with balancer nodes, Access → Settings → Balancer nodes shows which node is out.
- **Replies cut off while thinking**: the log says a reply "hit the …-token limit … thinking". Raise `MNEMERYNNE_LLM_REASONING_TOKENS`, or use the no-think template on the node.
- **Memories and follow-ups broken on a node**: the node runs LFM2.5 with its own thinking template; it needs `lfm2-no-think.jinja` ([Balancer nodes](balancer.md)).
- **"Not found" for things that are there**: the file isn't Ready (Files → Properties), the person can't read it (Who can see what), or the thresholds are off after an embedder change (`eval-retrieval --no-gate`, then `VECTOR_MAX_DISTANCE`).
- **Questions go to the wrong API operation**: example questions overlap; make them distinct, or raise `API_ROUTE_THRESHOLD`.

## Files and indexing

- **Stuck in Queued**: the worker isn't running, or the embedder is down (`journalctl -u mnemerynne-embedder`). Large PDFs with OCR take a long time; `journalctl -u mnemerynne-worker -f` shows progress.
- **Failed**: Properties shows the error. Corrupt PDFs, wrong extensions, non-UTF-8 text. `Retry indexing` after fixing.
- **Stored only**: a kind this version can't read, or over the size limits. After an update that added kinds, `reclassify`.
- **Old .doc/.ppt not searchable**: LibreOffice isn't installed (`dnf install libreoffice-core libreoffice-writer libreoffice-impress`), then `reclassify`.

## The share

- **"The share isn't available right now"**: the marker file `.mnemerynne-share` is missing (unmounted) or the service user can't reach the root. Nothing is mirrored as deleted meanwhile.
- **A folder isn't visible to anyone**: *Unix User\mnemerynne* lost Modify on it. Re-add the ACL (`setfacl` as in [The SMB share](share.md)).
- **People see a library they shouldn't**: permissions are per folder; a restricted file in an open folder is listed. Move it to its own folder.
- **A library is skipped**: its name doesn't suit Windows, or a Mnemerynne-hosted library already has that name; the worker log says which.
- **Someone's domain groups are stale**: the snapshot is older than 30 minutes (`journalctl -u mnemerynne-adsync`); `systemctl start mnemerynne-adsync` runs it now. Never `net cache flush` on a schedule.

## Claude

- **The connector won't register**: `MNEMERYNNE_PUBLIC_URL` must be the public https address (it is the OAuth issuer), and `/.well-known/oauth-*` must be reachable from the internet. Only claude.ai, claude.com and localhost (Claude Code) return addresses are accepted.
- **Claude sees nothing**: the library isn't shared with Claude, the grant's Claude flag is off, or the file was hidden with *Claude can see*. Who can see what shows the Claude column.
- **Claude can't edit**: the token or connection lacks edit; the place doesn't allow AI edits; or the request is waiting for the person or an admin (Approvals).
- **429 from /mcp**: `MNEMERYNNE_MCP_CALLS_PER_MINUTE` per token.
- **Fetch links fail**: *Links Claude can open* is off, the link expired (60 minutes, 100 fetches), or the connection was revoked.

## Microsoft 365

- **"needs linking again"**: Microsoft stopped accepting the refresh token (password change, disabled account, revoked sessions). The person links again.
- **Consent errors on a tenant**: an admin of *that* tenant must open Grant consent; otherwise register a single-tenant app there.
- **SharePoint library empty**: the site permission (`Sites.Selected`) wasn't granted to the app; see the PowerShell in [Microsoft 365](microsoft-365.md).
- **Entra groups not counting**: memberships older than 30 minutes are ignored; check the worker log for the tenant sync, and that the person linked an account in that tenant.

## Backups and restore

- **Backup refuses**: the destination is inside the share, or not writable by `mnemerynne` (`ReadWritePaths` drop-in outside `/srv`).
- **Restore refuses**: the services are still running, or a database exists and `--force` wasn't given.
- **Credentials gone after a restore**: the secret key differs; put the backed-up settings file back.

## Data locations

| What | Where |
|---|---|
| Database | `<data_dir>/mnemerynne.db` |
| Uploaded files (no share) | `<data_dir>/blobs` |
| Versions, proposals, temp | `<data_dir>/versions`, `proposals`, `tmp` |
| Settings | `/etc/mnemerynne/mnemerynne.env` |
| AD snapshot | `/var/lib/mnemerynne-ad/snapshot.txt` |
| Share and its bin | `MNEMERYNNE_SHARE_ROOT`, `<parent>/.mnemerynne-trash` |
| Backups | `MNEMERYNNE_BACKUP_DIR` |
| Update backups | `<data_dir>/update/before-*.db` |
