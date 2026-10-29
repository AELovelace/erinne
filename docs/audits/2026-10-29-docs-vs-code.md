# Audit: the Mnemerynne docs against the code (2026-10-29)

*Written 2026-10-29 by Claude for Doll. Read-only checks of the code on `main` plus branch
`claude/desktop-sync-client-plan-7mui87` (at `109abfc`), against four sets of docs:*
- *the user help (`mnemerynne/help/user`);*
- *the admin help (`mnemerynne/help/admin`);*
- *the READMEs and deploy docs;*
- *the plans in IT-OPS/Mnemerynne.*

*Each finding was checked in the code before it was listed. What each box actually runs wasn't checked. Copy in
IT-OPS/Mnemerynne.*

## Summary

- **The help wiki is mostly right.** Every link and anchor between help pages resolves, and nearly all limits,
  retention periods, labels and CLI flags match the code.
- **What's wrong is drift after the last few weeks' changes:**
  - Everyone now gets Erynne's tools.
  - More data can leave the box than the docs say.
  - Settings boxes were added.
  - Labels were renamed.
- **Three findings matter for more than wording:**
  1. **"Nothing leaves the machine"** (README, admin help *Architecture* and *Security*) is no longer true:
     Claude and ChatGPT connectors, Claude in chat, BFGguf's hosted mode, Microsoft 365, Automator webhooks,
     TMDB/MusicBrainz and the arcade all send data out when turned on. The README's headline promise should
     list them.
  2. **Backups miss data** (admin help *Backups* said nothing about it): `<data_dir>/rooms` (room files) and
     `<data_dir>/theme` (uploaded branding) aren't copied by `mnemerynne backup`. The Feature Audit (7071)
     already lists this as "Fix first" #2; the help now says it, but the code still doesn't copy them.
  3. **The planning docs are behind:** eight commits since the Roadmap's last refresh, including Games,
     desktop backup and screen scaling (migrations 0053–0058), aren't in any plan, and nothing is tagged.
- **Fixed during this audit** (desktop backup's own docs, on the branch: `89c4747`, `b1ce095`, `b544873`):
  - the README 5m box name, log check and `BACKUP_URL` rules;
  - `mnemerynne-backup sync -v` now works;
  - the packaging README's install command and builds folder;
  - Versions and Preferences pages mention the backup app;
  - the Access → Settings list;
  - what backups leave out;
  - what **Revoke all** ends: it signs out the person's backup apps too.
- **Everything else below is open.**

## 1. User help (`mnemerynne/help/user`)

| # | Page | Says | Actually | Fix |
|---|---|---|---|---|
| 1 | 130-memories:17 | Older memories make room for new ones | At the cap (50) new facts are dropped (memory.py:78) | "Nothing new is saved until you forget some" |
| 2 | 130-memories:21 | Memories are never sent to Claude | They go along with **Use External API** (claude_chat.py:8) | Say the exception |
| 3 | 30-ask:27, 170-troubleshooting:17 | Advanced settings only for the agent group | Everyone gets the agent (agent.py:684); the group widens edits | Rewrite both |
| 4 | 30-ask:23, 170-troubleshooting:13 | 👍/👎 with an optional comment | The UI sends only the rating (chat.js:243) | Drop the comment, or add a box |
| 5 | 50-tools-and-proposals:47 | A *Use tools* switch | No such control | Delete the sentence |
| 6 | 70-previews:30, 40-scopes:38, 170:25 | Status **Stored** | Shown as **Not searchable** (files.js:14) | Rename |
| 7 | 180-remote:10 | ⌂ ↻ ⧉ ⛶ in the title bar | Three window dots, ↻, ⧉, ▤ Resources (remote.html:10) | Describe the new bar |
| 8 | 215-phone:14 | Tabs in a swipeable strip | Tabs are in the ☰ menu on phones | Rewrite |
| 9 | 200-rooms:3 | Badge on the Rooms tab | The count is on ✉ (app.py:282) | Fix |
| 10 | 60-files:8 | Columns include kind | No Kind column; Type is a sort | Drop "kind" |
| 11 | 20-sign-in:14 | Sakura or Neon | Any theme on offer, or the enforced one | Generalise |
| 12 | 150-microsoft-365:7,9 | **Link**, "needs linking again" | "Link your … account", "Link again", a longer message | Current labels |
| 13 | 210-media:69 | **Pair a TV** | **Pair a TV or remote** | Fix |
| 14 | 10-welcome:9 | Tab table | Missing Media, Rooms, Automator | Add rows |

**Undocumented:**
- the 🧠 BFGguf toggle on Ask;
- Movies search and sort;
- Files shown on a TV;
- Automator system messages in the Inbox;
- media keys `m` (mute) and `s` (slideshow);
- in Files, Backspace (up a folder), the arrow keys, and Ctrl-drag to copy.

## 2. Admin help (`mnemerynne/help/admin`)

| # | Page | Says | Actually | Fix |
|---|---|---|---|---|
| 1 | 180-updating:15, 30-configuration:19 | `UPDATE_REPO` steers `update` | update.sh always uses `origin`; UPDATE_REPO only affects the Settings check | Say so |
| 2 | 10-architecture:3, 210-security:5 | Only Claude's reads and teacher text leave | See Summary 1 | List every outbound path |
| 3 | 40-access-page:61, 90-claude:11, 220:49 | No ChatGPT; only Claude/localhost may register | ChatGPT connector exists (off by default), its callbacks register when allowed | Add a ChatGPT section |
| 4 | 170-backups:48 | Retention table | Also messages 365, announcements 90, automation runs 90, scratchpad 90, room messages and files 365, chat events 7 days | Add rows |
| 5 | 190-indexing:28 | Restart after editing synonyms | Read on every search (retrieval.py:263) | "Applies at once" |
| 6 | 200-cli:17 | `list-users` shows domain account | Username, role, disabled | Fix |
| 7 | 70-libraries:27, 90-claude:23 | **Share with Claude** | **Shared with Claude** (admin.js:312) | Fix label |
| 8 | 220-troubleshooting:5 (and mcp_server.py:1429) | Settings → **Development** | Settings → Experimental → **Development diagnostics** | Fix both |
| 9 | 100-internal-apis:40 | Atera "Contact by email" is an action | A read operation that comes with the changes (apis.py:805) | Fix |
| 10 | 90-claude:59 | Tool list | Missing messages, rooms (4 tools in no page at all), briefing, security feeds, updates, M365 security, Automator, diagnose | Add or link |

**Undocumented:**
- BFGguf (what it is, where it runs, node roles);
- `mnemerynne-share-acl.service`;
- `MEDIA_FFMPEG`, `MEDIA_FFPROBE`, `MEDIA_CACHE_DIR`, `MEDIA_VAAPI_DEVICE` and `MEDIA_PROBE_TIMEOUT`;
- settings that 30-configuration leaves out:
  - `BACKUP_URL`, `MAX_SYNC_FILE_BYTES`, `SYNC_VERSION_MAX_BYTES`;
  - `ROOM_MESSAGE_DAYS`, `ROOM_FILE_BYTES`;
  - `TIMEZONE`, `AUTOMATION_RUN_DAYS`;
- `--password-stdin`;
- the agent's "Context per request" limit;
- `rooms`, `theme`, `media-cache` and `backup-client` in the data-locations table.

## 3. READMEs and deploy docs

| # | Where | Says | Actually | Fix |
|---|---|---|---|---|
| 1 | deploy README:221, :1038 | schema v6 | 58 migrations | `vNN` / 58 |
| 2 | README:21 | Nothing leaves the machine | See Summary 1 | Rewrite |
| 3 | README:65 | "How it answers" diagram | Router → agent first; cited JSON is the fallback | Redraw |
| 4 | deploy 5g:605 | Only the group gets tools | Everyone does; the group widens edits | Fix |
| 5 | deploy 5i:846 | Mail never goes to Claude | The "Mail and calendar for Claude" group gets mail tools | Name the exception |
| 6 | training README:130 | `GENERATOR_MODEL`, `restart llama-generator` | mnemerynne-generator.service hard-codes the model; llama-generator is MicroJames's | A drop-in for mnemerynne-generator |
| 7 | training README:109 | A literal `\n` in a command | Breaks the command | Real line break |
| 8 | deploy section 0/1 | preinstall.sh fetches "the models" | Only the embedder; the LFM2.5 generator must be copied by hand | Say so |
| 9 | deploy :283, :1071, :1181; claude-desktop README | `/var/lib/mnemerynne/...` | setup.sh puts data on the storage drive (`MNEMERYNNE_DATA_DIR`) | Name the setting |
| 10 | deploy 5j:881 | Copy the public Caddyfile | It carries an `arcade.example.org` block (setup.sh copies it too) | Delete or edit it |
| 11 | deploy :1072 | Fast-forwards to `main` | `MNEMERYNNE_UPDATE_BRANCH` | Fix |

**Undocumented:**
- `MEDIA_*` settings;
- env.example lacks `TIMEZONE`, `UPDATE_BRANCH`/`UPDATE_REPO`, `MAX_FILE_BYTES` (keep in step with the proxies), `ADMIN_USERS` and
  `SESSION_HTTPS_ONLY`;
- the README command table lacks `restore`, `reclassify`, `share-*`, `delete-user`, `revoke-tokens`, `init-db`,
  `router-data` and `train-router`;
- the backup app's README lacks `logout`, `remove`, `list`, `pause`, `resume` and `exclude`;
- no README links `deploy/windows/` or `deploy/m365/`.

## 4. IT-OPS plans

**Stale (the work is done):**
- **Roadmap (7057):**
  - Entertainment and desktop sync are built. Its migration number for Games is wrong: 0056, not 0055.
  - "Still untagged: nothing" is wrong; media, games, desktop backup and screen scaling are untagged.
- **Entertainment Proxy Plan (7072):** steps 2–4 are committed, and the ticket route is `/api/arcade/ticket`.
- **Feature Audit (7071):** TV Shows is committed, tested and documented, and the schema is 58.
- **1.0 Roadmap (3430):** D8 Health is done.
- **API Upgrade Plan (3422):** `create_folder`, binary files, move/rename, `list_changes`, editable libraries,
  `not_shown` and `same_content_as` are done.
- **Desktop Sync (7088) and the repo copy:** brought up to date during this audit.

**Wrong or contradictory:**
- **Roadmap D11:** it says "leaves the machine" is gone from the README; it isn't.
- **Roadmap:** in-chat approval and the Wazuh/O365/Gasco wishlist items are marked done.
  - Approval through elicitation exists but is inert, because `/mcp` is stateless.
  - The wishlist items aren't built.
- **RD Web (7061):** its open questions say *Connect to a remote PC* is styled; the code and help keep it hidden.

**Superseded (can be archived):**
- the 2026-10-25 Desktop Sync plan (3429);
- the 1.0 Roadmap (3430) apart from D11 and the gold-set review, and the 1.0.1 Roadmap (3431);
- Feedback Fix Plan (3423), What Needs You (3424), Usage Feedback (3419), Chat Screenshots (3420) and
  Set Top Box Notes (7058).

## 5. Open work across the docs (deduplicated)

**Security (Security Audit Plan 7070):**
1. Radio relay SSRF (1.1, "do first"): `follow_redirects=True` in `web/media.py:370` and `media/live.py:167`.
2. Also from Phase 1:
   - **1.5:** the API path filter.
   - **1.7:** pairing QR codes and rate limits.
   - **1.8:** PUBLIC_URL is only warned about.
   - **1.9:** exception text is shown to users (`web/chat.py:188`).
   - **1.10:** tool-call logs redact only mail.
3. Phase 2:
   - **2.1:** `send_message` isn't a write tool.
   - **2.3:** no `script-src` CSP.
   - **2.4:** passwords are pbkdf2 only.
   - **2.5:** no key rotation.
   - **2.8:** storage quotas, now with 50 GB desktop backups and no quota.

**Data loss and correctness (Feature Audit 7071):**
4. Room purge deletes attachments of messages it keeps; backups miss `rooms/` and `theme/`.
5. Agent answers are always "answered" and not streamed.
6. `list_changes` accuracy.

**TV and media:**
7. TV command classes, set-top questions Q1/Q2/Q5, cast volume 0 → 50%, subtitles and audio tracks, music
   queue, the games' TV bar, screens registered to users.

**Connector:**
8. These tools are missing:
   - `copy_item`;
   - reading and restoring versions;
   - resolving comments and finding open ones;
   - reading PDF text;
   - creating tables in Word documents;
   - filters for `find_files`;
   - `list_changes` across libraries;
   - batch file operations;
   - a recycle-bin tool;
   - letting `create_file` overwrite.
9. Metadata and search filters, freshness and conflicts, archive, permission visibility, audit export,
   webhooks, and making elicitation work.

**Platform and ops:**
10. Domain sign-in and M365 SSO; an OCR worker; multi-worker state.
11. Ops checks:
    - a monitor on `/healthz/full`;
    - the gold-set review;
    - the collab-pc RD Web check;
    - the RD Gateway domain move;
    - tagging and the version table.

**Desktop backup:**
12. The by-hand pilot checks; shadow copies (stretch goal); Linux name mapping; macOS.

**Docs:**
13. Sections 1–3 of this audit.

## Suggested order
1. **Security 1.1** (SSRF) and **backups missing `rooms/` and `theme/`**: small fixes, real risk.
2. **The "leaves the machine" wording** in README and help: it's a promise people rely on.
3. **One docs pass** for sections 1–3 (an evening): the label and drift fixes are mechanical.
4. **Refresh the Roadmap** for the eight commits, tag a release, and archive the superseded plans.
