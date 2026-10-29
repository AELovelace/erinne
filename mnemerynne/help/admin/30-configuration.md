# Configuration reference

Every setting is read from the environment or from the settings file (`/etc/mnemerynne/mnemerynne.env` on the server, `.env` in development), prefixed `MNEMERYNNE_`. Values below are the defaults. The web app and the worker read the file at start, so **a change needs a restart** of `mnemerynne-web` and `mnemerynne-worker`, except the few settings kept in the database (marked *Access page*), which apply at once.

No inline comments in the file: systemd would keep them as part of the value.

## Storage, web and sessions

| Setting | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data` (`/var/lib/mnemerynne` on the server) | Database, blobs, versions, proposals, temp files, `synonyms.txt`, `router.json`, `mnemerynne.mcpb`. |
| `HOST`, `PORT` | `127.0.0.1`, `38200` | Where the web app listens. Keep loopback behind the proxy. |
| `SECRET_KEY` | empty | Signs sessions and encrypts stored credentials (API secrets, Microsoft 365 links, balancer key, Claude keys). Required, 32+ characters, whenever `PUBLIC_URL` is set or the host isn't loopback. Changing it loses every stored credential. Keep a copy off the box. |
| `SESSION_MAX_AGE` | 86400 | Seconds a browser stays signed in. |
| `SESSION_HTTPS_ONLY` | unset | Cookie only over HTTPS. Unset: on whenever `PUBLIC_URL` is https. |
| `PUBLIC_URL` | empty | The address people use, e.g. `https://erynne.example.internal`. Links handed to Claude and the OAuth issuer are built on it. |
| `ARCADE_URL` | empty | The arcade's own address for Games, e.g. `https://arcade.beepsquad.wtf`: a different host from `PUBLIC_URL` (it refuses to start otherwise), with its own DNS record and certificate. The games site is proxied at its root, so the games' scripts never run on Mnemerynne's origin. Empty: no Games. |
| `ADMIN_NETWORKS` | empty | Comma-separated CIDRs admin rights count from (`192.168.1.0/24,10.8.0.0/24`). Elsewhere admins are ordinary users. Empty: anywhere. |
| `UPDATE_REPO`, `UPDATE_BRANCH` | empty, `main` | Where the update check and `mnemerynne-admin update` get new versions (owner/name on GitHub; empty = the checkout's origin). |
| `FORWARDED_ALLOW_IPS` | | Not a Mnemerynne setting but uvicorn's: `127.0.0.1` trusts the proxy's `X-Forwarded-For`, so throttling sees real client addresses. |

## Sign-in throttling

| Setting | Default | Meaning |
|---|---|---|
| `LOGIN_MAX_FAILURES` | 5 | Wrong passwords for one username from one address before a lockout. |
| `LOGIN_IP_MAX_FAILURES` | 20 | Failures across any usernames from one address (password spraying). |
| `LOGIN_LOCKOUT_SECONDS` | 300 | How long a lockout lasts. |

## Identity

| Setting | Default | Meaning |
|---|---|---|
| `ADMIN_USERS` | empty | Usernames that are always admins, comma-separated; bootstrap only. |
| `AD_DOMAIN` | empty | NetBIOS name of the domain the box is joined to (e.g. `ETERNALHOTELS`). Enables linking users to domain accounts, the admin group, share permissions, API groups. |
| `AD_SNAPSHOT` | `/var/lib/mnemerynne-ad/snapshot.txt` | Group memberships written by the `mnemerynne-adsync` timer; winbind is used while the file is missing or older than 30 minutes. |
| `M365_CLIENT_ID`, `M365_CLIENT_SECRET` | empty | The multitenant app registration people link Microsoft accounts through. Empty: no Microsoft 365. |
| `M365_SYNC_SECONDS` | 300 | How often the worker refreshes Entra groups and SharePoint libraries. |

Also in the database (Access → Settings): the **admin group** (default `Domain Admins`), the **agent group**, the **Claude group**, the **balancer nodes and key**, the **Claude links** policy, the **wiki library**, and each API's group.

## The SMB share

| Setting | Default | Meaning |
|---|---|---|
| `SHARE_ROOT` | unset | Libraries are `<root>/<Name>` and inboxes `<root>/<users folder>/<username>`. Unset: files live inside Mnemerynne. |
| `SHARE_TRASH` | `<root's parent>/.mnemerynne-trash` | The recycle bin beside the share, hidden from SMB. |
| `USERS_FOLDER` | `Users` | The folder holding inboxes. |
| `SHARE_SCAN_SECONDS` | 60 | How often the worker scans the share. |
| `SHARE_SETTLE_SECONDS` | 10 | Files changed within this many seconds wait for the next scan (still being copied). |

## Models

| Setting | Default | Meaning |
|---|---|---|
| `LLM_URL` | `http://127.0.0.1:8081` | The generator (llama-server). The fallback when no balancer node answers. |
| `LLM_MODEL` | `qwen3-8b-q4_k_m` | Label recorded with each answer; the server decides what runs. |
| `LLM_TIMEOUT` | 120 | Seconds per model call. |
| `LLM_REASONING_TOKENS` | 1024 | Extra tokens a thinking model (LFM2.5) may spend before answering; 0 for models that don't think. Raise it if the log says a reply hit the limit while thinking. |
| `MAX_QUESTION_CHARS` | 2000 | Longest question. |
| `EMBED_URL` | `http://127.0.0.1:8082` | The embedder (llama-server with `--pooling last` for Qwen3-Embedding). |
| `EMBED_MODEL`, `EMBED_DIM` | `qwen3-embedding-0.6b-q8_0`, 1024 | The embedding model and its vector size (fixes the vector table's shape: changing the model means `mnemerynne reembed`). |
| `EMBED_QUERY_PREFIX`, `EMBED_DOC_PREFIX` | an instruction, empty | Qwen3-Embedding is instruction-aware: questions get the instruction, documents don't. |
| `EMBED_BATCH_SIZE`, `EMBED_TIMEOUT` | 32, 120 | Batching and timeout for embedding calls. |
| `RERANK_URL`, `RERANK_CANDIDATES` | empty, 20 | An optional llama-server reranking endpoint. Off by default: the bake-off showed it lowered recall. |

## Uploads and limits

| Setting | Default | Meaning |
|---|---|---|
| `MAX_FILE_BYTES` | 500 MB | Largest upload; anything up to it is kept. |
| `MAX_PDF_BYTES`, `MAX_PDF_PAGES` | 100 MB, 5000 | PDFs bigger than this are stored but not indexed. |
| `MAX_MD_BYTES` | 5 MB | Markdown, text and code. |
| `MAX_DOC_BYTES` | 50 MB | Office, OpenDocument, RTF, HTML, CSV. |
| `LIBREOFFICE_COMMAND`, `LIBREOFFICE_TIMEOUT` | `soffice`, 300 | Converts old .doc/.ppt when present on PATH. |
| `TRASH_DAYS` | 30 | Recycle-bin items are deleted for good after this. |

## Editing and versions

| Setting | Default | Meaning |
|---|---|---|
| `EDIT_MAX_BYTES` | 20 MB | Largest file Claude, Erynne or the browser editor may change. |
| `EDIT_MAX_CELLS` | 200000 | Spreadsheet cells, all sheets together. |
| `EDIT_MAX_OPERATIONS` | 50 | Edits per call. |
| `VERSION_DAYS`, `VERSION_KEEP` | 30, 20 | How long earlier versions are kept, and how many per file. |
| `PROPOSAL_DAYS` | 7 | A proposal nobody applies, dismisses or withdraws within this many days expires. |
| `PROPOSAL_BATCH_MAX` | 25 | API actions one `propose_batch` call may hold. |
| `MESSAGE_DAYS` | 365 | Notes and system messages in people's inboxes are deleted after this. |
| `SCRATCHPAD_DAYS` | 90 | Notes on the agents' scratchpad nobody touched for this long are deleted, open or not ([Claude](claude.md#the-agents-scratchpad)). |
| `SCRATCHPAD_CLAIM_MINUTES` | 120 | A claim on a scratchpad note lapses after this, so a session that stopped doesn't block the next. |
| `ANNOUNCEMENT_DAYS` | 90 | Expired announcements are deleted this long after their expiry. |

## Backups and retention

| Setting | Default | Meaning |
|---|---|---|
| `BACKUP_DIR` | `/var/backups/mnemerynne` | Where `backup` writes and `restore` reads. On a disk that outlives the boot drive; never inside the share. |
| `BACKUP_ENV_FILE` | `/etc/mnemerynne/mnemerynne.env` | Copied beside each snapshot (it holds the secret key). Empty: not copied. |
| `MCP_CALLS_DAYS`, `API_CALLS_DAYS`, `AGENT_CALLS_DAYS` | 90 | Logs of Claude's tool calls, internal API calls and Erynne's tool calls; 0 keeps them forever. |
| `GRAPH_RUNS_DAYS` | 30 | Per-step timings of answers. |
| `OAUTH_CLIENT_DAYS` | 30 | Claude connections unused this long are removed. |

## MCP

| Setting | Default | Meaning |
|---|---|---|
| `MCP_CALLS_PER_MINUTE` | 60 | Per token or connection. |
| `MCP_MAX_TOKENS` | 4000 | Passage text returned by one search. |

## Ingestion and retrieval

| Setting | Default | Meaning |
|---|---|---|
| `OCR_COMMAND`, `OCR_MIN_CHARS`, `OCR_TIMEOUT` | `ocrmypdf`, 25, 1800 | OCR runs when present; pages with less text than this count as scanned. |
| `CHUNK_TARGET_TOKENS`, `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS` | 350, 512, 50 | Passage size. Changing them means `mnemerynne reindex`. |
| `JOB_MAX_ATTEMPTS`, `JOB_STALE_MINUTES` | 3, 30 | Retries for failed ingestion; running jobs older than this are retried. |
| `RETRIEVAL_K` | 30 | Candidates from each of keyword and vector search. |
| `CONTEXT_CHUNKS`, `CONTEXT_TOKENS` | 6, 3000 | Passages handed to the model, within this budget. |
| `FTS_SECTION_WEIGHT` | 3.0 | Weight of section titles against body text in keyword search. |
| `VECTOR_MAX_DISTANCE`, `VECTOR_MAX_DISTANCE_NO_KEYWORDS` | 0.50, 0.42 | The "not found" shortcut: answer without the model when the closest passage is farther than this. Tuned for Qwen3-Embedding; re-tune with `eval-retrieval --no-gate` after changing the embedder. |
| `API_ROUTE_THRESHOLD` | 0.80 | How close a question must be to an operation's example questions to go to that API. |
| `API_HINT_THRESHOLD` | 0.65 | A document search that finds nothing suggests the closest API operation when at least this close. |

## Memory

| Setting | Default | Meaning |
|---|---|---|
| `MEMORY_MODE` | `auto` | `auto` extracts facts people state about themselves, `explicit` only saves on "remember that…", `off` disables it. |
| `MEMORY_MAX_PER_USER` | 50 | Memories per person. |

## Teacher (training and evaluation data only)

`TEACHER_FLAVOR` (`llamacpp` = the local generator, `openai` = a remote OpenAI-compatible API), `TEACHER_URL`, `TEACHER_MODEL`, `TEACHER_API_KEY`, `TEACHER_PRICE_IN`/`OUT` (USD per million tokens, for the spend tracker), `TEACHER_ALLOWED_LIBRARIES` (comma-separated; a remote teacher only ever sees text from these, `*` for all, empty for none). Only `draft-gold`, `router-data` and `gen-sft` use it; nothing in normal operation does.
