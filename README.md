# Erinne

A private document inbox with cited question answering. Each person uploads
PDFs and Markdown files to their own inbox; admins publish shared libraries
(brand standards, system manuals). Erinne answers questions from the files a
person can access and cites the file and page (or Markdown heading) for every
claim, or says the answer isn't in their documents.

Everything runs locally: a FastAPI app, one SQLite database (with FTS5 and
sqlite-vec), and llama.cpp `llama-server` for the Erinne model and embeddings.
No document, question or trace leaves the machine.

## Status

| Phase | Scope | State |
|---|---|---|
| P0 | Repo, config, database and migrations, accounts, permissions, MicroJames import | done |
| P1 | Uploads, ingestion worker, PDF/OCR/Markdown extraction, chunking, embeddings | next |
| P2 | Permission-filtered hybrid search, evaluation harness | |
| P3 | LangGraph answering flow, streaming, citations, conversation memory | |
| P3b | Long-term user memory | |
| P4 | Fedora pilot | |
| P5 | Fine-tuning the Erinne model (LFM2.5-2.6B) | |

## Development (Windows or Linux)

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                     # create .venv with pinned dependencies
cp .env.example .env        # then set ERINNE_SECRET_KEY
uv run erinne init-db
uv run erinne create-user yourname --role admin    # prompts for a password
uv run erinne serve         # http://127.0.0.1:38200
uv run pytest
```

On Windows without `uv` on PATH, use `py -3.14 -m uv …`.

### Administration

```sh
uv run erinne list-users
uv run erinne reset-password yourname
uv run erinne set-role someone admin
uv run erinne create-library "Oracle"
uv run erinne grant "Oracle" someone [--write]
```

### Importing from MicroJames

```sh
uv run python scripts/migrate_microjames.py --source C:\Scripts\MicroJames --dry-run
uv run python scripts/migrate_microjames.py --source C:\Scripts\MicroJames
```

Users keep their passwords and roles, top-level `docs/` folders become
libraries, and read grants carry over. Document import follows in P1.

## Data and privacy

`data/` (database, uploaded files), `eval/gold/`, `runs/` and `training/out/`
hold confidential content and are git-ignored. LangSmith tracing is never
enabled; traces are logged to the local `graph_runs` table instead.
