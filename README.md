# Erinne

A private document inbox with cited question answering. Each person uploads
PDFs and Markdown files to their own inbox; admins publish shared libraries
(brand standards, system manuals). Erinne answers questions from the files a
person can access and cites the file and page (or Markdown heading) for every
claim, or says the answer isn't in their documents. It remembers follow-up
context within a conversation and facts users tell it about themselves.

Everything runs locally: a FastAPI app, one SQLite database (FTS5,
sqlite-vec, LangGraph checkpoints and memory), and llama.cpp `llama-server`
for the Erinne model and embeddings. No document, question or trace leaves
the machine (LangSmith is forced off).

## Status

| Phase | Scope | State |
|---|---|---|
| P0 | Repo, config, database and migrations, accounts, permissions, MicroJames import | done |
| P1 | Uploads, ingestion worker, PDF/OCR/Markdown extraction, chunking, embeddings | done |
| P2 | Permission-filtered hybrid search, synonyms, evaluation harness, embedder bake-off | done; gold set needs staff review |
| P3 | LangGraph answering flow, streaming, citations, conversation memory, feedback | done |
| P3b | Long-term user memory with safety filter and "Your memories" page | done |
| P4 | Fedora pilot next to MicroJames | ready: `deploy/linux/README.md` |
| P5 | Fine-tuning the Erinne model (LFM2.5-2.6B) | pipeline ready: `training/README.md` |

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
  `erinne/synonyms.py`, extendable via `<data_dir>/synonyms.txt`) are searched
  too, fused with reciprocal rank fusion.
* **Answer contract** (`erinne/answer.py`): sources numbered `[n] file · p. x
  · Section`, JSON output `{status, answer, citations}` enforced by
  llama-server's JSON schema; uncited or malformed answers become "not found".
* **Memory:** conversation state per thread (LangGraph checkpointer); facts
  users state about themselves extracted after each answer, filtered for
  secrets and guest details, visible and deletable on **Memories**.

## Development (Windows or Linux)

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/) (on Windows without
`uv` on PATH: `py -3.14 -m uv …`).

```sh
uv sync
cp .env.example .env               # then set ERINNE_SECRET_KEY
scripts/start-llama.ps1            # Windows dev: Qwen3-8B :8081, Qwen3-Embedding :8082
uv run erinne create-user yourname --role admin
uv run erinne serve                # http://127.0.0.1:38200
uv run erinne worker               # separate terminal: processes uploads
uv run pytest
```

### Commands

| Command | Does |
|---|---|
| `erinne serve` / `erinne worker` | web app / ingestion worker |
| `erinne create-user`, `reset-password`, `set-role`, `list-users` | accounts (passwords are prompted) |
| `erinne create-library NAME`, `grant LIBRARY USER [--write]` | shared libraries |
| `erinne import FOLDER --library NAME \| --user NAME` | bulk upload |
| `erinne reindex [--file ID] [--reextract]` | re-chunk (and re-read) files |
| `erinne reembed` | rebuild all vectors after changing the embedder |
| `erinne forget-memories USER` | wipe a user's memories (admins can't read them) |
| `erinne backup --dest DIR` | database snapshot + new files |
| `erinne migrate-microjames --source DIR [--import-files]` | import from MicroJames |
| `erinne draft-gold`, `eval-retrieval`, `eval-answers`, `eval-gate` | evaluation |
| `erinne gen-sft` | fine-tuning data from the teacher |

## Evaluation

`eval/gold/dev.jsonl` holds 200 drafted questions (150 answerable, 20
permission negatives, 30 off-topic). **They are unverified**: a staff member
should check each and set `"verified": true` (fix or delete wrong ones, in
particular "off-topic" items that the documents actually answer).

```sh
uv run erinne eval-retrieval eval/gold/dev.jsonl --verified-only
uv run erinne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8081 --label qwen3-8b --out runs/answers-qwen3.json
```

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
