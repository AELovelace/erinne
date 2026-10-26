# Architecture

Mnemerynne is the server behind Erynne. Everything runs on one box your organisation controls; no document, question or answer leaves it unless a person connects Claude or an admin turns on a remote teacher model.

## The parts

| Part | Runs as | Does |
|---|---|---|
| **Web app** | `mnemerynne-web` (uvicorn, port 38200 on loopback) | The pages (Ask, Files, Wiki, Preferences, Access, Help), the JSON API behind them, streamed answers, the MCP endpoint for Claude (`/mcp`) and its OAuth sign-in. |
| **Worker** | `mnemerynne-worker` | Reads uploads into searchable passages (text extraction, OCR, chunking, embeddings), scans the file share every minute, syncs Microsoft 365 groups and SharePoint, and does the hourly housekeeping (recycle bin, versions, proposals, logs). |
| **Generator** | `mnemerynne-generator` (llama-server) | The answer model (LFM2.5-2.6B by default, or a fine-tuned Erynne). Also used for routing, memory extraction and Erynne's tools. Can be replaced or supplemented by [balancer nodes](balancer.md). |
| **Embedder** | `mnemerynne-embedder` (llama-server) | Qwen3-Embedding-0.6B: turns passages and questions into vectors for meaning-based search. Stays on this box, next to the files. |
| **Reverse proxy** | Caddy (or nginx) | HTTPS on 443 in front of the web app; streams answers; Let's Encrypt when public. |
| **Timers** | `mnemerynne-backup.timer`, `mnemerynne-adsync.timer` | Nightly backup; AD group snapshot every 5 minutes. |

## Storage

- **One SQLite database** (`<data_dir>/mnemerynne.db`): users, collections, folders, files, passages with FTS5 full-text index and sqlite-vec vectors, conversations and memories (LangGraph's checkpointer and store), tokens, proposals, logs and settings stored in the `meta` table.
- **File bytes**: either content-addressed blobs under `<data_dir>/blobs` (no share), or the files themselves on the SMB share (`MNEMERYNNE_SHARE_ROOT`), where the share is the source of truth and the database mirrors it.
- **Versions** under `<data_dir>/versions`, **proposals** under `<data_dir>/proposals`, the settings file at `/etc/mnemerynne/mnemerynne.env`.

## How a question is answered

```
question → memories → condense follow-up → route → hybrid search → nothing close? → "not found"
                                             (FTS5 + vectors, jargon variants,        │
                                              the person's readable collections)       ▼
                                                                  model writes a JSON answer citing numbered
                                                                  sources → checks → streamed to the browser
```

- **Search** runs keyword (BM25, section titles weighted) and vector search with the person's readable collections inside every query, plus jargon variants from [synonyms](indexing.md#synonyms), fused by reciprocal rank fusion.
- **The answer contract**: sources are numbered, the model must return JSON with status, answer and citations (enforced by llama-server's JSON schema), and an uncited or malformed answer becomes "not found". That is what keeps Erynne from inventing things.
- **Routing**: a small classifier decides whether a message is a document question, small talk, general help or a question for an internal API; questions close to an API operation's example questions go to the API.
- **People with tools** (the agent group) go through the tool-using agent instead: it searches, reads, browses, queries APIs and drafts proposals, on the same model path.

## Permissions in one paragraph

A person reads their own File box, the libraries granted to them or their groups (with folder-level grants and inheritance), share libraries where their linked domain account may open the folder (POSIX ACLs kept in step with Windows permissions by Samba), and SharePoint files their own Microsoft account may open. Every search, listing, citation and tool call is filtered by that reach. Claude's reach is a subset: only what admins shared with Claude, minus what people hid. See [Libraries, grants and inheritance](libraries-and-grants.md).

## Ports and addresses

| What | Where |
|---|---|
| Web app | 127.0.0.1:38200 (behind the proxy on 443) |
| Generator | 127.0.0.1:8086 (pilot box; the dev default is 8081) |
| Embedder | 127.0.0.1:8085 (pilot box; the dev default is 8082) |
| Balancer nodes | `http://<node>:9180`, set on Access → Settings |
| MCP for Claude | `https://<public url>/mcp`, with OAuth at `/.well-known/oauth-*` and `/oauth/*` |

## Code layout

`mnemerynne/` holds the package: `web/` (routers, templates, static JS and CSS), `ingest/` (extraction and indexing), `clients/` (llama-server and Anthropic clients), `migrations/` (numbered SQL files applied at start), `help/` (this wiki). `deploy/linux/` has the service files, Caddyfiles and scripts; `clients/claude-desktop/` the Desktop extension; `api-templates/` the API template format and examples; `tests/` the pytest suite.
