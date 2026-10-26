# Indexing, search tuning and evaluation

## The pipeline

An uploaded or discovered file goes through the worker:

1. **Receive**: hashed, kind sniffed from content (not just the extension), stored as a blob or placed on the share, recorded with status `queued`, and a job enqueued. Files whose kind can't be read (archives, images, old .doc without the converter) are `stored` and go no further.
2. **Extract**: PDF text page by page (with bookmarks as sections); OCR through `ocrmypdf` for pages with less than `OCR_MIN_CHARS` of text, keeping a searchable copy that the viewer serves; Markdown by heading; Word by heading, PowerPoint by slide, Excel and CSV by sheet rows, OpenDocument, RTF, HTML, and code and config files by lines. Old `.doc`/`.ppt` are converted by LibreOffice when it is installed.
3. **Chunk**: passages of about `CHUNK_TARGET_TOKENS` (350) up to `CHUNK_MAX_TOKENS` (512) with `CHUNK_OVERLAP_TOKENS` (50) overlap, each carrying its location (page, heading path with anchor, slide, rows, lines) for citations.
4. **Embed**: vectors from the embedder in batches of `EMBED_BATCH_SIZE`.
5. **Ready**: the file's status; the FTS5 index and the vector table now include it. Failures retry up to `JOB_MAX_ATTEMPTS`; a job stuck longer than `JOB_STALE_MINUTES` is retried.

Limits: `MAX_FILE_BYTES` (kept), `MAX_PDF_BYTES`/`MAX_PDF_PAGES`, `MAX_MD_BYTES`, `MAX_DOC_BYTES` (indexed). Bigger files are stored only.

## Search

Keyword search (FTS5 BM25, section titles weighted `FTS_SECTION_WEIGHT`) and vector search run with the person's readable collections inside each query, for the question as typed plus up to three jargon variants, fused by reciprocal rank fusion; `RETRIEVAL_K` candidates from each, `CONTEXT_CHUNKS` within `CONTEXT_TOKENS` handed to the model. The **not-found shortcut** answers without the model when the closest passage is farther than `VECTOR_MAX_DISTANCE` (or `VECTOR_MAX_DISTANCE_NO_KEYWORDS` when no keyword matched). A reranker (`RERANK_URL`) is supported but off: in the bake-off it lowered in-context recall and added latency.

### Synonyms

Staff say "night audit", the manual says "End of Day". Defaults cover common hotel jargon (check-in/arrival, folio/guest bill, OTA, PMS, no-show, comp, walk-in, rate parity). Add your own in `<data_dir>/synonyms.txt`, one group per line, terms separated by `=`:

```
night audit = end of day
# comments and blank lines are ignored
```

It is read at start; restart the web app after editing. The file is included in backups.

## Commands

| Command | When |
|---|---|
| `reindex [--file ID] [--reextract]` | Re-chunk and re-embed from the stored text; `--reextract` re-reads the files (after extraction changes). All files unless `--file`. |
| `reembed` | Rebuild every vector after changing the embedding model (`EMBED_MODEL`, `EMBED_DIM`); the vector table is recreated. |
| `reclassify` | Make stored-only files searchable that this version can read (after an update added file types). |
| Files → **Re-embed** | The same for a selection, a folder or a library, from the browser; anyone in their own File box, admins in any library. |
| Files → **Retry indexing** | One failed file. |

## Routing

`router.json` in the data directory is a small classifier trained with `train-router` from data drafted by `router-data`: it decides whether a message is a document question, small talk, general help or something else. Without it everything is treated as search. Questions close to an internal API operation's example questions (`API_ROUTE_THRESHOLD`) go to the API instead.

## Evaluation

`eval/gold/dev.jsonl` holds drafted questions (answerable, permission negatives, off-topic) that staff should verify (`"verified": true`).

```sh
uv run mnemerynne eval-retrieval eval/gold/dev.jsonl --verified-only
uv run mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8081 --label qwen3-8b --out runs/a.json
uv run mnemerynne eval-answers eval/gold/dev.jsonl --agent --as rob --label agent --out runs/b.json
uv run mnemerynne eval-gate runs/baseline.json runs/candidate.json
```

`eval-retrieval` measures recall and MRR with or without the not-found gate (`--no-gate` when re-tuning the distance thresholds after an embedder change). `eval-answers` measures the cited-JSON flow; `--agent` measures what chat actually does for the agent group, read-only. `eval-gate` passes or fails a candidate against a baseline. `draft-gold` and `gen-sft` use the teacher model to draft questions and fine-tuning data; a remote teacher only ever sees text from `TEACHER_ALLOWED_LIBRARIES`.

The embedder bake-off (October 2026) chose Qwen3-Embedding-0.6B (recall@5 0.96) over jina-v2 (0.92) and nomic-v1.5 (0.91); its llama-server needs `--pooling last`.

## Fine-tuning

`training/README.md` in the repository covers fine-tuning the Erynne model (LFM2.5-2.6B) from `gen-sft` data; `eval-gate` decides whether a fine-tune ships.
