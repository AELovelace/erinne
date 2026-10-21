"""Command-line administration: `erinne <command>` or `python -m erinne <command>`.

Passwords are prompted for (or read from stdin with --password-stdin), never
taken as arguments, so they stay out of shell history and process lists.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from erinne import db, permissions, users
from erinne.config import get_settings


def _connect():
    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    return conn


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def cmd_init_db(args):
    conn = _connect()
    print(f"Database ready at {get_settings().db_path} (schema v{db.schema_version(conn)}).")


def cmd_create_user(args):
    conn = _connect()
    users.create_user(conn, args.username, _read_password(args), role=args.role)
    print(f"Created {args.role} '{args.username.lower()}' with a private inbox.")


def cmd_reset_password(args):
    conn = _connect()
    if not users.set_password(conn, args.username, _read_password(args)):
        raise SystemExit(f"No user '{args.username}'.")
    print(f"Password updated for '{args.username.lower()}'.")


def cmd_set_role(args):
    conn = _connect()
    if not users.set_role(conn, args.username, args.role):
        raise SystemExit(f"No user '{args.username}'.")
    print(f"'{args.username.lower()}' is now {args.role}.")


def cmd_list_users(args):
    conn = _connect()
    for u in users.list_users(conn):
        state = "" if u["active"] else "  (disabled)"
        print(f"{u['username']:<30} {u['role']}{state}")


def cmd_create_library(args):
    conn = _connect()
    lib_id = permissions.create_library(conn, args.name)
    print(f"Created library '{args.name}' (id {lib_id}).")


def cmd_grant(args):
    conn = _connect()
    lib = permissions.get_library(conn, args.library)
    user = users.get_user(conn, args.username)
    if lib is None or user is None:
        raise SystemExit("Unknown library or user.")
    permissions.grant_user(conn, lib["id"], user["id"], can_write=args.write)
    print(f"Granted '{user['username']}' {'read/write' if args.write else 'read'} access to '{lib['name']}'.")


def cmd_revoke_tokens(args):
    from erinne import api_tokens

    conn = _connect()
    user = users.get_user(conn, args.username)
    if user is None:
        raise SystemExit("Unknown user.")
    count = api_tokens.revoke_all(conn, user["id"])
    print(f"Revoked {count} Claude token(s) for '{user['username']}'.")


def cmd_worker(args):
    from erinne import worker

    worker.run(get_settings(), once=args.once)


def cmd_import(args):
    from pathlib import Path

    conn = _connect()
    settings = get_settings()
    if args.library:
        target = permissions.get_library(conn, args.library)
        if target is None:
            raise SystemExit(f"No library '{args.library}' (create it with create-library).")
        collection_id, owner = target["id"], None
    else:
        user = users.get_user(conn, args.user)
        if user is None:
            raise SystemExit(f"No user '{args.user}'.")
        collection_id, owner = permissions.private_collection_id(conn, user["id"]), user["id"]
    import_folder(conn, settings, Path(args.path), collection_id, owner, flat=args.flat)
    print("Queued; run `erinne worker` (or the erinne-worker service) to process them.")


_JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


def import_folder(conn, settings, folder, collection_id, owner, *, flat: bool = False) -> tuple[int, int, int]:
    """Add every file under `folder`, keeping its subfolders (unless `flat`).

    PDF, Markdown and text files are queued for indexing; anything else is
    stored as not searchable. Returns (added, existing, rejected).
    """
    from erinne import fs
    from erinne.ingest import store

    added = existing = rejected = 0
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if path.name.lower() in _JUNK_FILES or path.name.startswith(("~$", ".")):
            continue
        parts = [] if flat else list(path.relative_to(folder).parent.parts)
        try:
            _, created = store.add_path(conn, settings, path, collection_id, owner, folder_parts=parts)
        except (store.UploadError, fs.FsError) as exc:
            rejected += 1
            print(f"  rejected {path.name}: {exc}")
            continue
        added += created
        existing += not created
        print(f"  {'added' if created else 'already there'}: {path.relative_to(folder)}")
    print(f"{added} added, {existing} already present, {rejected} rejected.")
    return added, existing, rejected


def cmd_purge_trash(args):
    from erinne import fs

    conn = _connect()
    settings = get_settings()
    days = settings.trash_days if args.days is None else args.days
    removed = fs.purge_expired(conn, settings, days)
    print(f"Deleted {removed} item(s) that were in a recycle bin for more than {days} day(s).")


def cmd_reindex(args):
    from erinne import jobs

    conn = _connect()
    where, params = ("WHERE id = ?", (args.file,)) if args.file else ("", ())
    ids = [r[0] for r in conn.execute(f"SELECT id FROM files {where}", params)]
    with db.transaction(conn):
        if args.reextract:
            # Drop cached text so extraction (and OCR) runs again with current code.
            conn.execute(f"DELETE FROM blob_units WHERE sha256 IN (SELECT blob_sha256 FROM files {where})", params)
        for file_id in ids:
            conn.execute("UPDATE files SET status = 'queued', progress = NULL, error = NULL WHERE id = ?", (file_id,))
            jobs.enqueue(conn, "ingest", file_id)
    print(f"Queued {len(ids)} file(s) for re-chunking and re-embedding.")


def cmd_reembed(args):
    from erinne.clients.embed import Embedder
    from erinne.ingest.reembed import reembed_all

    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim, ensure_vectors=False)
    print(f"Rebuilding all vectors with {settings.embed_model} ({settings.embed_dim}-dim) "
          f"from {settings.embed_url}. Search returns fewer results until this finishes.")
    embedder = Embedder(settings)
    try:
        count = reembed_all(conn, settings, embedder)
    finally:
        embedder.close()
    print(f"Done: {count} vectors.")


def cmd_draft_gold(args):
    import asyncio
    from pathlib import Path

    from erinne.clients.llm import LLM
    from erinne.evaluation import dump_gold
    from erinne.evaluation.draft import draft

    settings = get_settings()
    conn = _connect()
    out = Path(args.out)
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; pass --force to overwrite (it may hold verified items).")

    async def run():
        from erinne.teacher import allowed_collections
        llm = LLM.teacher(settings, base_url=args.teacher_url)
        allowed = allowed_collections(conn, settings, llm) if llm.is_remote else None
        try:
            return await draft(conn, llm, teacher_allowed=allowed, n_answerable=args.n, n_offtopic=args.offtopic,
                               n_scope=args.scope, as_user=args.as_user, outsider=args.outsider or None)
        finally:
            await llm.aclose()

    items = asyncio.run(run())
    dump_gold(items, out)
    counts = {}
    for i in items:
        key = ("answerable" if i.answerable else "unanswerable") + "/" + i.split
        counts[key] = counts.get(key, 0) + 1
    print(f"Wrote {len(items)} draft items to {out}: {counts}. Review them and set \"verified\": true.")


def cmd_eval_retrieval(args):
    import json
    import time
    from pathlib import Path

    from erinne.clients.embed import Embedder
    from erinne.clients.rerank import Reranker
    from erinne.evaluation import load_gold
    from erinne.evaluation.retrieval_eval import evaluate

    settings = get_settings()
    if args.no_gate:
        settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    conn = _connect()
    items = load_gold([Path(p) for p in args.gold], split=args.split, verified_only=args.verified_only)
    embedder = Embedder(settings)
    reranker = Reranker(args.rerank_url) if args.rerank_url else None
    try:
        report = evaluate(conn, settings, embedder, items, reranker)
    finally:
        embedder.close()
    report["config"] = {"embed_model": settings.embed_model, "rerank": args.rerank_url or None,
                        "vector_max_distance": settings.vector_max_distance, "split": args.split}
    for key, value in report["summary"].items():
        print(f"{key:>28}: {value}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report: {out}")


def cmd_eval_answers(args):
    import asyncio
    import json
    from pathlib import Path

    from erinne.answer import PROMPT_VERSION
    from erinne.clients.embed import Embedder
    from erinne.clients.llm import LLM
    from erinne.clients.rerank import Reranker
    from erinne.evaluation import load_gold
    from erinne.evaluation.answer_eval import evaluate_answers

    settings = get_settings()
    conn = _connect()
    items = load_gold([Path(p) for p in args.gold], split=args.split, verified_only=args.verified_only)
    if args.limit:
        items = items[: args.limit]

    async def run():
        restrict = None
        if args.generator_openai:
            # The remote API under test: teacher settings (key/model), and only the
            # allowed libraries' text may appear in its prompts.
            from erinne.teacher import allowed_collections
            settings.teacher_flavor = "openai"
            if args.generator_model:
                settings.teacher_model = args.generator_model
            generator = LLM.teacher(settings)
            restrict = allowed_collections(conn, settings, generator)
        else:
            generator = LLM(args.generator or settings.llm_url, timeout=settings.llm_timeout)
        judge = None if args.no_judge else LLM(args.judge or settings.llm_url, timeout=settings.llm_timeout)
        embedder = Embedder(settings)
        reranker = Reranker(args.rerank_url) if args.rerank_url else None
        try:
            return await evaluate_answers(conn, settings, embedder, generator, judge, items, reranker,
                                          restrict_to=restrict)
        finally:
            embedder.close()
            await generator.aclose()
            if judge:
                await judge.aclose()

    report = asyncio.run(run())
    report["config"] = {"generator": args.generator or settings.llm_url, "label": args.label,
                        "prompt_version": PROMPT_VERSION, "split": args.split, "rerank": args.rerank_url or None}
    for key, value in report["summary"].items():
        print(f"{key:>20}: {value}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report: {out}")


def cmd_backup(args):
    from pathlib import Path

    from erinne.backup import backup

    result = backup(get_settings(), Path(args.dest), keep=args.keep)
    print(f"Snapshot {result['snapshot']}; {result['blobs_copied']} new file(s) copied; "
          f"pruned {len(result['pruned'])} old snapshot(s).")


def cmd_forget_memories(args):
    import sqlite3

    from langgraph.store.sqlite import SqliteStore

    from erinne.graph import memory_namespace

    settings = get_settings()
    conn = _connect()
    user = users.get_user(conn, args.username)
    if user is None:
        raise SystemExit(f"No user '{args.username}'.")
    store_conn = sqlite3.connect(settings.db_path, isolation_level=None, check_same_thread=False)
    store = SqliteStore(store_conn)
    store.setup()
    items = store.search(memory_namespace(user["id"]), limit=10_000)
    for item in items:  # deleted without displaying them: admins can wipe, not read
        store.delete(memory_namespace(user["id"]), item.key)
    print(f"Forgot {len(items)} memory item(s) for '{user['username']}'.")


def run_migrate_microjames(argv: list[str]) -> None:
    """`erinne migrate-microjames ...`: everything after the command goes to the script."""
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_microjames.py"
    spec = importlib.util.spec_from_file_location("migrate_microjames", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_microjames"] = module
    spec.loader.exec_module(module)
    sys.argv = ["erinne migrate-microjames", *argv]
    module.main()


def cmd_gen_sft(args):
    import asyncio
    from pathlib import Path

    from erinne.clients.embed import Embedder
    from erinne.clients.llm import LLM
    from erinne.evaluation import load_gold
    from erinne.sft import generate

    settings = get_settings()
    conn = _connect()
    gold = load_gold([Path(p) for p in args.gold]) if args.gold else []

    async def run():
        teacher = LLM.teacher(settings, base_url=args.teacher_url)
        if args.budget:
            teacher.budget_usd = args.budget
        embedder = Embedder(settings)
        try:
            return await generate(conn, settings, embedder, teacher, gold, Path(args.out),
                                  concurrency=args.concurrency, rounds=args.rounds,
                                  n_answer=args.answer, n_near_miss=args.near_miss, n_offtopic=args.offtopic,
                                  n_condense=args.condense, n_memory=args.memory, n_general=args.general,
                                  router_messages=Path(args.router_messages))
        finally:
            embedder.close()
            await teacher.aclose()

    counts = asyncio.run(run())
    print(f"Appended to {args.out}: {counts}")


def cmd_eval_gate(args):
    import json
    from pathlib import Path

    from erinne.evaluation.gate import compare

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    ok, lines = compare(baseline, candidate, args.tolerance)
    print(f"baseline:  {baseline.get('config', {}).get('label') or args.baseline}")
    print(f"candidate: {candidate.get('config', {}).get('label') or args.candidate}")
    print("\n".join(lines))
    print("PASS: the candidate may be deployed." if ok else "FAIL: keep the current model.")
    raise SystemExit(0 if ok else 1)


def cmd_router_data(args):
    import asyncio
    from pathlib import Path

    from erinne.clients.llm import LLM
    from erinne.router_data import generate

    settings = get_settings()
    conn = _connect()

    async def run():
        llm = LLM.teacher(settings, base_url=args.teacher_url)
        try:
            return await generate(conn, llm, Path(args.out), per_route=args.per_route, settings=settings)
        finally:
            await llm.aclose()

    print(f"Wrote {args.out}: {asyncio.run(run())}")


def cmd_train_router(args):
    import json
    from pathlib import Path

    from erinne import router as router_mod
    from erinne.clients.embed import Embedder
    from erinne.router_data import load

    settings = get_settings()
    texts, labels = load(Path(args.data))
    embedder = Embedder(settings)
    try:
        vectors = embedder.embed_queries(texts)
    finally:
        embedder.close()
    thresholds = {label: args.threshold for label in set(labels) if label != router_mod.DEFAULT_ROUTE}
    model = router_mod.train(vectors, labels, embed_model=settings.embed_model, thresholds=thresholds)
    out = Path(settings.data_dir) / "router.json"
    model.save(out)
    print(json.dumps(model.metrics, indent=2))
    print(f"Saved {out}. Restart erinne-web to use it.")


def cmd_serve(args):
    import uvicorn

    from erinne.web.app import create_app

    settings = get_settings()
    # proxy_headers + FORWARDED_ALLOW_IPS let login throttling see real client
    # IPs behind nginx.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="erinne", description="Erinne administration")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create or migrate the database").set_defaults(func=cmd_init_db)
    sub.add_parser("serve", help="Run the web app").set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="Run the ingestion worker")
    p.add_argument("--once", action="store_true", help="Exit when no jobs are due")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("import", help="Add a folder of files (subfolders kept)")
    p.add_argument("path")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--library", help="Library name")
    target.add_argument("--user", help="Import into this user's inbox")
    p.add_argument("--flat", action="store_true", help="Put every file at the top level, ignoring subfolders")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("purge-trash", help="Empty recycle-bin items older than the retention period")
    p.add_argument("--days", type=int, default=None, help="Default: ERINNE_TRASH_DAYS (30)")
    p.set_defaults(func=cmd_purge_trash)

    p = sub.add_parser("draft-gold", help="Draft evaluation questions with the teacher model")
    p.add_argument("--out", default="eval/gold/dev.jsonl")
    p.add_argument("--n", type=int, default=150, help="Answerable questions")
    p.add_argument("--offtopic", type=int, default=30)
    p.add_argument("--scope", type=int, default=20, help="Scope-excluded (permission) negatives")
    p.add_argument("--as-user", default="rob", help="User the answerable questions are asked as")
    p.add_argument("--outsider", default="test", help="User without access, for scope negatives ('' = none)")
    p.add_argument("--teacher-url", default=None, help="Teacher llama-server (default ERINNE_LLM_URL)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_draft_gold)

    p = sub.add_parser("eval-retrieval", help="Measure retrieval against a gold set")
    p.add_argument("gold", nargs="+")
    p.add_argument("--split", default=None, help="dev or test (default: all)")
    p.add_argument("--verified-only", action="store_true")
    p.add_argument("--rerank-url", default="")
    p.add_argument("--no-gate", action="store_true", help="Disable the not-found shortcut")
    p.add_argument("--out", default=None, help="Write the full JSON report here")
    p.set_defaults(func=cmd_eval_retrieval)

    sub.add_parser("migrate-microjames", add_help=False,
                   help="Import users, libraries, grants (and files) from MicroJames; see --help")

    p = sub.add_parser("router-data", help="Draft labelled messages for the router with the teacher")
    p.add_argument("--out", default="data/router/messages.jsonl")
    p.add_argument("--per-route", type=int, default=400)
    p.add_argument("--teacher-url", default=None)
    p.set_defaults(func=cmd_router_data)

    p = sub.add_parser("train-router", help="Train the message router (search / smalltalk / general / ...)")
    p.add_argument("--data", default="data/router/messages.jsonl")
    p.add_argument("--threshold", type=float, default=0.85,
                   help="Probability a non-search route needs before it wins (cautious by default)")
    p.set_defaults(func=cmd_train_router)

    p = sub.add_parser("eval-gate", help="Pass/fail a candidate model's answer report against a baseline")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.add_argument("--tolerance", type=float, default=0.02)
    p.set_defaults(func=cmd_eval_gate)

    p = sub.add_parser("gen-sft", help="Generate fine-tuning data with the teacher model")
    p.add_argument("--out", default="training/out/sft.jsonl", help="Appended to, so runs can resume")
    p.add_argument("--gold", nargs="*", default=["eval/gold/dev.jsonl"],
                   help="Gold sets whose source chunks are excluded")
    p.add_argument("--teacher-url", default=None)
    p.add_argument("--answer", type=int, default=2000)
    p.add_argument("--near-miss", type=int, default=500)
    p.add_argument("--offtopic", type=int, default=200)
    p.add_argument("--condense", type=int, default=400)
    p.add_argument("--memory", type=int, default=300)
    p.add_argument("--general", type=int, default=400, help="Small-talk / general-help replies")
    p.add_argument("--router-messages", default="data/router/messages.jsonl",
                   help="Labelled messages (from router-data) used for the general examples")
    p.add_argument("--budget", type=float, default=0,
                   help="Stop cleanly once a remote teacher has cost this many USD (0 = no limit)")
    p.add_argument("--concurrency", type=int, default=2, help="Parallel teacher calls (remote APIs: 8-16)")
    p.add_argument("--rounds", type=int, default=4,
                   help="Passes over all tasks; stopping early still leaves a balanced set")
    p.set_defaults(func=cmd_gen_sft)

    p = sub.add_parser("backup", help="Snapshot the database and copy new files")
    p.add_argument("--dest", required=True)
    p.add_argument("--keep", type=int, default=14, help="Daily database snapshots to keep")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("forget-memories", help="Delete everything Erinne remembers about a user")
    p.add_argument("username")
    p.set_defaults(func=cmd_forget_memories)

    p = sub.add_parser("eval-answers", help="End-to-end answer evaluation against a gold set")
    p.add_argument("gold", nargs="+")
    p.add_argument("--generator", default=None, help="Model being evaluated (default ERINNE_LLM_URL)")
    p.add_argument("--generator-openai", action="store_true",
                   help="Evaluate the OpenAI API model from the ERINNE_TEACHER_* settings; only questions answered by ERINNE_TEACHER_ALLOWED_LIBRARIES are used")
    p.add_argument("--generator-model", default=None, help="Override ERINNE_TEACHER_MODEL for this run")
    p.add_argument("--judge", default=None, help="Judge model, e.g. the teacher (default ERINNE_LLM_URL)")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--label", default="")
    p.add_argument("--split", default=None)
    p.add_argument("--verified-only", action="store_true")
    p.add_argument("--rerank-url", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_eval_answers)

    sub.add_parser(
        "reembed", help="Rebuild all vectors after changing the embedding model (ERINNE_EMBED_*)"
    ).set_defaults(func=cmd_reembed)

    p = sub.add_parser("reindex", help="Re-chunk and re-embed files from their stored text")
    p.add_argument("--file", type=int, help="Only this file id (default: all files)")
    p.add_argument("--reextract", action="store_true",
                   help="Also re-read the files (after extraction changes); slower")
    p.set_defaults(func=cmd_reindex)
    sub.add_parser("list-users", help="List users").set_defaults(func=cmd_list_users)

    p = sub.add_parser("create-user", help="Create a user (prompts for the password)")
    p.add_argument("username")
    p.add_argument("--role", choices=users.ROLES, default="user")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("reset-password", help="Set a new password (prompts for it)")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("set-role", help="Make a user an admin or a regular user")
    p.add_argument("username")
    p.add_argument("role", choices=users.ROLES)
    p.set_defaults(func=cmd_set_role)

    p = sub.add_parser("create-library", help="Create a shared library")
    p.add_argument("name")
    p.set_defaults(func=cmd_create_library)

    p = sub.add_parser("grant", help="Give a user access to a library")
    p.add_argument("library")
    p.add_argument("username")
    p.add_argument("--write", action="store_true", help="Also allow uploading to the library")
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser("revoke-tokens", help="Revoke all of a user's Claude (API) tokens")
    p.add_argument("username")
    p.set_defaults(func=cmd_revoke_tokens)
    return parser


def main(argv: list[str] | None = None) -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["migrate-microjames"]:
        run_migrate_microjames(argv[1:])
        return
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc
