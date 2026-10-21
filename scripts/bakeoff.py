"""Compare embedders (and rerankers) on the gold set without touching the live DB.

    python scripts/bakeoff.py --name nomic --embed-url http://127.0.0.1:8090 --dim 768 \
        --query-prefix "search_query: " --doc-prefix "search_document: " eval/gold/dev.jsonl

Copies the database with SQLite's backup API into runs/bakeoff/<name>/, re-embeds
every chunk there with the candidate embedder, then runs the retrieval evaluation
(with the not-found gate disabled, so recall is measured on its own). Pass
--reuse to skip re-embedding an existing copy (e.g. to try rerankers).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mnemerynne import db  # noqa: E402
from mnemerynne.clients.embed import Embedder  # noqa: E402
from mnemerynne.clients.rerank import Reranker  # noqa: E402
from mnemerynne.config import get_settings  # noqa: E402
from mnemerynne.evaluation import load_gold  # noqa: E402
from mnemerynne.evaluation.retrieval_eval import evaluate  # noqa: E402
from mnemerynne.ingest.reembed import reembed_all  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("gold", nargs="+")
    p.add_argument("--name", required=True)
    p.add_argument("--embed-url", required=True)
    p.add_argument("--dim", type=int, required=True)
    p.add_argument("--query-prefix", default="")
    p.add_argument("--doc-prefix", default="")
    p.add_argument("--rerank-url", default="")
    p.add_argument("--reuse", action="store_true", help="Reuse an already re-embedded copy")
    p.add_argument("--label", default="", help="Report suffix, e.g. the reranker name")
    args = p.parse_args()

    settings = get_settings()
    work = Path("runs/bakeoff") / args.name
    work.mkdir(parents=True, exist_ok=True)
    copy_path = work / "mnemerynne.db"
    settings.embed_url, settings.embed_dim = args.embed_url, args.dim
    settings.embed_query_prefix, settings.embed_doc_prefix = args.query_prefix, args.doc_prefix
    settings.embed_model = args.name
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0  # no not-found gate

    if not (args.reuse and copy_path.exists()):
        copy_path.unlink(missing_ok=True)
        src = sqlite3.connect(settings.db_path)
        dst = sqlite3.connect(copy_path)
        src.backup(dst)
        src.close()
        dst.close()
        conn = db.connect(copy_path)
        db.migrate(conn, args.dim, ensure_vectors=False)
        embedder = Embedder(settings)
        started = time.perf_counter()
        count = reembed_all(conn, settings, embedder,
                            progress=lambda m: print(m) if m.endswith("chunks") and "000/" in m else None)
        print(f"Re-embedded {count} chunks in {time.perf_counter() - started:.0f}s")
    else:
        conn = db.connect(copy_path)
        embedder = Embedder(settings)

    items = load_gold([Path(g) for g in args.gold])
    reranker = Reranker(args.rerank_url) if args.rerank_url else None
    report = evaluate(conn, settings, embedder, items, reranker)
    report["config"] = {"name": args.name, "dim": args.dim, "query_prefix": args.query_prefix,
                        "doc_prefix": args.doc_prefix, "rerank": args.rerank_url or None}
    out = work / f"report{('-' + args.label) if args.label else ''}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
