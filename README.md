# Mnemerynne

Mnemerynne is the server behind **Erynne**, a private document inbox with
cited question answering. Each person keeps
files in their own space; admins publish shared libraries (brand standards,
system manuals). The **Files** page is a full file manager: folders, drag and
drop (files and whole folders), move/copy/rename, a recycle bin and previews.
Any file can be stored. Searchable: PDF, Markdown and text; Word, PowerPoint
and Excel (also CSV, OpenDocument, RTF and HTML); and source code and config
files. Erynne answers questions from the files a person can access and cites
the file and where in it for every claim (page, heading, slide, sheet rows or
code lines), or says the answer isn't in their documents. It remembers follow-up
context within a conversation and facts users tell it about themselves.

**Help** (top right) opens the built-in wiki: a user section for everyone and an
admin section for admins, written as Markdown in `mnemerynne/help/`. The **Wiki**
tab shows the organisation's own wiki, a library called *Wiki* (a folder at the
root of the share), with a browser editor for its pages; the same editor opens
from **Edit** in Files for any text or Markdown file a person may change.

Everything runs locally: a FastAPI app, one SQLite database (FTS5,
sqlite-vec, LangGraph checkpoints and memory), and llama.cpp `llama-server`
for the Erynne model and embeddings. No document, question or trace leaves
the machine (LangSmith is forced off).

## Status

| Phase | Scope | State |
|---|---|---|
| P0 | Repo, config, database and migrations, accounts, permissions, MicroJames import | done |
| P1 | Uploads, ingestion worker, PDF/OCR/Markdown extraction, chunking, embeddings | done |
| P1b | File manager (folders, move/copy, recycle bin, upload queue, previews), library admin page, "search in" a folder | done |
| P2 | Permission-filtered hybrid search, synonyms, evaluation harness, embedder bake-off | done; gold set needs staff review |
| P3 | LangGraph answering flow, streaming, citations, conversation memory, feedback | done |
| P3b | Long-term user memory with safety filter, shown on Preferences | done |
| P4 | Fedora pilot next to MicroJames | ready: `deploy/linux/README.md` |
| P5 | Fine-tuning the Erynne model (LFM2.5-2.6B) | pipeline ready: `training/README.md` |
| P6 | Help wiki (user and admin, `mnemerynne/help`), the organisation **Wiki** tab (a library of Markdown pages) and the browser Markdown editor | done |

### Planned

- **Domain sign-in.** People sign in to Erynne with their AD username and
  password (checked through the box's winbind join) instead of a separate
  Mnemerynne password that an admin links to their domain account. That
  removes the linking step on Access → Accounts. Open questions: what happens
  to existing local accounts, and keeping one local admin for when AD is
  down. Group memberships keep coming from the AD snapshot
  (`deploy/linux/README.md`, 5c), not from sign-in, so changes in AD still
  apply within minutes to sessions that are already signed in and to Claude.
- **Sign in with Microsoft 365.** Single sign-on through Entra ID (OIDC), with
  each Microsoft account mapped to its domain account. A first version was
  built and then set aside in favour of local accounts.
- **Editing any spreadsheet.** Claude and Erynne refuse to change workbooks
  with charts, pivot tables or pictures, because openpyxl drops those parts
  when it saves, and refuse to insert or delete rows in workbooks with
  formulas, because the formulas below wouldn't follow
  (`mnemerynne/editing_formats.py`). The fix is to change only the cells'
  XML inside the .xlsx and leave every other part untouched, and to rewrite
  formula references that point past inserted or deleted rows. About a week,
  most of it testing against real hotel workbooks.

## How it answers

```
question ─► memories ─► condense follow-up ─► hybrid search ─► nothing close? ─► "not found"
                                              (FTS5 + vectors,     │
                                               jargon variants,    ▼
                                               your collections   model writes a JSON answer
                                               only)              citing numbered sources ─► checks ─► stream
```

* **Search:** keyword (FTS5 bm25, section titles weighted) and vector
  (Qwen3-Embedding-0.6B) search run with the user's readable collections
  inside each query. Jargon variants ("night audit" ↔ "End of Day", see
  `mnemerynne/synonyms.py`, extendable via `<data_dir>/synonyms.txt`) are searched
  too, fused with reciprocal rank fusion.
* **Answer contract** (`mnemerynne/answer.py`): sources numbered `[n] file · p. x
  · Section`, JSON output `{status, answer, citations}` enforced by
  llama-server's JSON schema; uncited or malformed answers become "not found".
* **Memory:** conversation state per thread (LangGraph checkpointer); facts
  users state about themselves extracted after each answer, filtered for
  secrets and guest details, visible and deletable on **Preferences**.

## Development (Windows or Linux)

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/) (on Windows without
`uv` on PATH: `py -3.14 -m uv …`).

```sh
uv sync
cp .env.example .env               # then set MNEMERYNNE_SECRET_KEY
scripts/start-llama.ps1            # Windows dev: Qwen3-8B :8081, Qwen3-Embedding :8082
uv run mnemerynne create-user yourname --role admin
uv run mnemerynne serve                # http://127.0.0.1:38200
uv run mnemerynne worker               # separate terminal: processes uploads
uv run pytest
```

### Commands

| Command | Does |
|---|---|
| `mnemerynne serve` / `mnemerynne worker` | web app / ingestion worker |
| `mnemerynne create-user`, `reset-password`, `set-role`, `list-users` | accounts (passwords are prompted) |
| `mnemerynne create-library NAME`, `grant LIBRARY USER [--write]` | shared libraries (or the **Libraries** page, for admins) |
| `mnemerynne import FOLDER --library NAME \| --user NAME [--flat]` | bulk upload, keeping subfolders |
| `mnemerynne purge-trash [--days N]` | empty old recycle-bin items now (the worker does this hourly) |
| `mnemerynne reindex [--file ID] [--reextract]` | re-chunk (and re-read) files |
| `mnemerynne reembed` | rebuild all vectors after changing the embedder |
| `mnemerynne forget-memories USER` | wipe a user's memories (admins can't read them) |
| `mnemerynne backup --dest DIR` | database snapshot + new files |
| `mnemerynne migrate-microjames --source DIR [--import-files]` | import from MicroJames |
| `mnemerynne draft-gold`, `eval-retrieval`, `eval-answers`, `eval-gate` | evaluation |
| `mnemerynne gen-sft` | fine-tuning data from the teacher |

## Evaluation

`eval/gold/dev.jsonl` holds 200 drafted questions (150 answerable, 20
permission negatives, 30 off-topic). **They are unverified**: a staff member
should check each and set `"verified": true` (fix or delete wrong ones, in
particular "off-topic" items that the documents actually answer).

```sh
uv run mnemerynne eval-retrieval eval/gold/dev.jsonl --verified-only
uv run mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8081 --label qwen3-8b --out runs/answers-qwen3.json
uv run mnemerynne eval-answers eval/gold/dev.jsonl --agent --as rob --label agent-lfm2.5 --out runs/answers-agent.json
```

`eval-answers` alone measures the cited-JSON flow, which chat only uses when
the agent can't answer. `--agent` measures what chat actually does: each
question goes through the tool-using agent as that person (`--as`, or each
item's `as_user`), on the same model path as the web app (balancer nodes,
then `MNEMERYNNE_LLM_URL`; `--agent-url` for one server). It is read-only:
tools that draft changes are switched off and the agent group's extra reach
is never used, but tool calls are logged in `agent_calls`
(`eval:<item id>`) and read-only API operations really run. Items whose
supporting files that person can't read are skipped and counted. Besides the
usual scores it reports tool calls, model turns, fallbacks (the agent model
didn't answer) and uncited answers. An answer counts as a refusal when it
cites nothing and says it couldn't find it. `eval-gate` only compares agent
reports with agent reports.

Embedder bake-off (2026-10-20, unverified gold, no gate):

| Embedder | recall@1 | recall@5 | recall@10 | MRR | in context |
|---|---|---|---|---|---|
| jina-embeddings-v2-base-en | 0.68 | 0.92 | 0.97 | 0.78 | 93% |
| nomic-embed-text-v1.5 | 0.71 | 0.91 | 0.96 | 0.79 | 93% |
| **Qwen3-Embedding-0.6B** | **0.75** | **0.96** | **0.99** | **0.84** | **97%** |

A CPU reranker (jina-reranker-v1-turbo) lowered in-context recall to 81% and
added ~600 ms, so there is no reranker.

## Data and privacy

`data/` (database, uploaded files), `eval/gold/`, `runs/` and
`training/out/` hold confidential content and are git-ignored.
