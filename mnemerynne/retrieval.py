"""Permission-filtered hybrid search.

Keyword (FTS5 bm25) and vector (sqlite-vec) search run with the user's
readable collections and folders (a `Scope`) inside each query, never as a
filter afterwards. Results are fused with reciprocal rank fusion, optionally
reranked, then trimmed to a token budget for the model's context.

sqlite-vec applies `IN` constraints before picking the k nearest vectors but
`OR` only after, which would silently drop allowed results. So a scope is
split into branches that each need only `IN`, every branch gets its own
nearest-neighbour query, and the results are merged by distance.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from mnemerynne import permissions, synonyms
from mnemerynne.clients.embed import pack_vector
from mnemerynne.config import Settings


def _synonym_groups(settings: Settings) -> list[list[str]]:
    return synonyms.load_groups(settings.data_dir / "synonyms.txt")

RRF_K = 60

# Words that carry no search signal; dropped from keyword queries.
_STOPWORDS = frozenset("""
a about after all also am an and any are as at be been before being but by can could did do does
doing for from had has have having he her here hers him his how i if in into is it its just me my
no nor not of on or our ours out over own she should so some such than that the their them then
there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours
""".split())
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    chunk_id: int
    file_id: int
    collection_id: int
    file_name: str
    kind: str                 # 'pdf' | 'md' | 'txt' | 'doc' | 'code'
    page_start: int | None
    page_end: int | None
    anchor: str | None
    section_path: str
    text: str
    tokens: int
    score: float = 0.0        # fused (or reranked) score, higher is better
    fts_rank: int | None = None
    vector_rank: int | None = None
    distance: float | None = None  # cosine distance, lower is closer

    @property
    def location(self) -> str:
        """Human-readable place in the document: 'p. 12-13' or '§ Night Audit'."""
        if self.kind == "pdf" and self.page_start:
            pages = f"p. {self.page_start}" if self.page_end in (None, self.page_start) \
                else f"p. {self.page_start}-{self.page_end}"
            return pages
        return f"§ {self.section_path.split(' > ')[-1]}" if self.section_path else ""

    @property
    def url(self) -> str:
        if self.kind == "pdf":
            return f"/files/{self.file_id}/pdf#page={self.page_start or 1}"
        return f"/files/{self.file_id}/view" + (f"#{self.anchor}" if self.anchor else "")


@dataclass
class SearchResult:
    hits: list[Hit]                       # context for the model, best first
    candidates: list[Hit] = field(default_factory=list)  # everything fused, for evaluation
    lexical_hits: int = 0
    best_distance: float | None = None

    @property
    def found_anything(self) -> bool:
        """False when nothing plausibly relevant exists in the user's documents."""
        return bool(self.hits)


def fts_query(question: str) -> str | None:
    """FTS5 query: OR of meaningful words, plus the whole phrase for a boost."""
    words = [w for w in _WORD.findall(question.lower()) if len(w) > 1 and w not in _STOPWORDS]
    if not words:
        return None
    terms = [f'"{w}"' for w in dict.fromkeys(words)]
    if len(words) > 1:
        terms.append('"' + " ".join(words) + '"')
    return " OR ".join(terms)


def _in(ids) -> tuple[str, tuple]:
    ids = tuple(ids)
    return ",".join("?" * len(ids)), ids


@dataclass(frozen=True)
class Scope:
    """What a search may look at.

    `whole`: collections searched entirely. `parts`: (collection_id,
    top_level, folder_ids) for collections only partly readable: their
    folders in `folder_ids`, plus files at the top level when `top_level`.
    Chunks keep folder 0 for the top level and collection 0 in the bin, so
    the collection guard in every branch also keeps binned files out.
    """
    whole: frozenset[int] = frozenset()
    parts: tuple[tuple[int, bool, frozenset[int]], ...] = ()

    @classmethod
    def collections(cls, ids, folders: set[int] | None = None) -> "Scope":
        """Whole collections, or (with `folders`) just those folders in them."""
        ids = frozenset(ids)
        if folders is None:
            return cls(whole=ids)
        return cls(parts=tuple((cid, False, frozenset(folders)) for cid in sorted(ids)))

    @classmethod
    def of_reach(cls, reaches) -> "Scope":
        """From permissions.reach() (or claude_reach()) values."""
        whole, parts = set(), []
        for r in reaches:
            if r.everything:
                whole.add(r.collection_id)
            elif r.root_read or r.read:
                parts.append((r.collection_id, r.root_read, frozenset(r.read)))
        return cls(frozenset(whole), tuple(parts))

    def narrow(self, collection_id: int, folders: set[int] | None = None) -> "Scope":
        """Only this collection, and within it only `folders` (a subtree) if given.
        It can only narrow: nothing outside the original scope gets in."""
        part = next((p for p in self.parts if p[0] == collection_id), None)
        if folders is None:
            return Scope(self.whole & {collection_id}, (part,) if part else ())
        if collection_id in self.whole:
            return Scope(parts=((collection_id, False, frozenset(folders)),))
        if part:
            return Scope(parts=((collection_id, False, part[2] & frozenset(folders)),))
        return Scope()

    def branches(self) -> list[tuple[set[int], set[int] | None]]:
        """(collections, folders or None for all) pairs that each need only `IN`."""
        out = []
        if self.whole:
            out.append((set(self.whole), None))
        with_folders = [p for p in self.parts if p[2]]
        if with_folders:
            out.append(({p[0] for p in with_folders}, set().union(*(p[2] for p in with_folders))))
        tops = {p[0] for p in self.parts if p[1]}
        if tops:
            out.append((tops, {0}))
        return out

    def __bool__(self) -> bool:
        return bool(self.branches())


def _as_scope(allowed, folders: set[int] | None = None) -> Scope:
    if isinstance(allowed, Scope):
        return allowed
    return Scope.collections(allowed, folders) if folders != set() else Scope()


def _branch_sql(collections: set[int], folders: set[int] | None, prefix: str) -> tuple[str, tuple]:
    marks, ids = _in(collections)
    sql = f"{prefix}collection_id IN ({marks})"
    if folders is not None:
        folder_marks, folder_ids = _in(folders)
        sql += f" AND {prefix}folder_id IN ({folder_marks})"
        ids += folder_ids
    return sql, ids


def keyword_search(conn: sqlite3.Connection, question: str, allowed, k: int,
                   section_weight: float = 3.0, folders: set[int] | None = None) -> list[int]:
    """`allowed`: a Scope, or a set of collection ids (with optional `folders`)."""
    query = fts_query(question)
    branches = _as_scope(allowed, folders).branches()
    if not query or not branches:
        return []
    parts = [_branch_sql(c, f, "c.") for c, f in branches]   # plain SQL, so OR is fine here
    where = " OR ".join(f"({sql})" for sql, _ in parts)
    rows = conn.execute(
        f"SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
        f"WHERE chunks_fts MATCH ? AND ({where}) ORDER BY bm25(chunks_fts, ?, 1.0) LIMIT ?",
        (query, *[p for _, params in parts for p in params], section_weight, k),
    ).fetchall()
    return [r[0] for r in rows]


def vector_search(conn: sqlite3.Connection, query_vector: list[float], allowed, k: int,
                  folders: set[int] | None = None) -> list[tuple[int, float]]:
    """`allowed`: a Scope, or a set of collection ids (with optional `folders`)."""
    found: list[tuple[int, float]] = []
    vector = pack_vector(query_vector)
    for collections, branch_folders in _as_scope(allowed, folders).branches():
        sql, params = _branch_sql(collections, branch_folders, "")
        found += [(r[0], r[1]) for r in conn.execute(
            f"SELECT rowid, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ? AND {sql} ORDER BY distance",
            (vector, k, *params))]
    return sorted(found, key=lambda r: r[1])[:k]


def _load_hits(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, Hit]:
    if not chunk_ids:
        return {}
    marks, ids = _in(chunk_ids)
    rows = conn.execute(
        f"SELECT c.id, c.file_id, c.collection_id, c.page_start, c.page_end, c.anchor, c.section_path, "
        f"c.text, c.tokens, f.display_name, b.kind FROM chunks c "
        f"JOIN files f ON f.id = c.file_id JOIN blobs b ON b.sha256 = f.blob_sha256 "
        f"WHERE c.id IN ({marks})", ids,
    ).fetchall()
    return {r["id"]: Hit(r["id"], r["file_id"], r["collection_id"], r["display_name"], r["kind"],
                         r["page_start"], r["page_end"], r["anchor"], r["section_path"], r["text"],
                         r["tokens"] or 1) for r in rows}


def search_for_user(conn: sqlite3.Connection, settings: Settings, embedder, user_id: int, question: str,
                    reranker=None, scope: dict | None = None) -> SearchResult:
    """Hybrid search over exactly what `user_id` may read.

    `scope` ({"collection_id", "folders"}) narrows it to one collection and,
    optionally, a set of folder ids; it can only narrow, never widen.
    """
    allowed = Scope.of_reach(permissions.reach(conn, user_id).values())
    if scope:
        folders = set(scope["folders"]) if scope.get("folders") is not None else None
        allowed = allowed.narrow(scope["collection_id"], folders)
    return search(conn, settings, embedder, question, allowed, reranker)


def search(conn: sqlite3.Connection, settings: Settings, embedder, question: str, allowed,
           reranker=None, folders: set[int] | None = None) -> SearchResult:
    """Hybrid search restricted to `allowed` (a Scope, or a set of collection
    ids plus optional `folders`), applied inside both queries.

    The question is also searched with known jargon swapped (synonyms.py), and
    every keyword and vector result list is fused with reciprocal rank fusion.
    """
    allowed = _as_scope(allowed, folders)
    queries = [question] + synonyms.variants(question, _synonym_groups(settings))
    lexical_lists = [keyword_search(conn, q, allowed, settings.retrieval_k, settings.fts_section_weight)
                     for q in queries]
    vector_lists = [vector_search(conn, embedder.embed_query(q), allowed, settings.retrieval_k)
                    for q in queries] if allowed else []

    # RRF within each phrasing (keyword + vector), then each chunk keeps its best
    # phrasing's score: phrasings are alternatives, so a chunk that matches one of
    # them perfectly must not lose to chunks that match all of them loosely.
    fused: dict[int, float] = {}
    for i in range(len(queries)):
        per_query: dict[int, float] = {}
        branches = [lexical_lists[i]] + ([[c for c, _ in vector_lists[i]]] if vector_lists else [])
        for ranked in branches:
            for rank, chunk_id in enumerate(ranked, start=1):
                per_query[chunk_id] = per_query.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for chunk_id, score in per_query.items():
            fused[chunk_id] = max(fused.get(chunk_id, 0.0), score)

    hits_by_id = _load_hits(conn, list(fused))
    for ranked in lexical_lists:  # best rank across query variants
        for rank, chunk_id in enumerate(ranked, start=1):
            hit = hits_by_id[chunk_id]
            hit.fts_rank = rank if hit.fts_rank is None else min(hit.fts_rank, rank)
    for ranked in vector_lists:
        for rank, (chunk_id, distance) in enumerate(ranked, start=1):
            hit = hits_by_id[chunk_id]
            hit.vector_rank = rank if hit.vector_rank is None else min(hit.vector_rank, rank)
            hit.distance = distance if hit.distance is None else min(hit.distance, distance)
    for chunk_id, score in fused.items():
        hits_by_id[chunk_id].score = score
    candidates = sorted(hits_by_id.values(), key=lambda h: h.score, reverse=True)

    distances = [v[0][1] for v in vector_lists if v]
    best_distance = min(distances) if distances else None
    lexical_count = len({c for ranked in lexical_lists for c in ranked})
    result = SearchResult(hits=[], candidates=candidates, lexical_hits=lexical_count, best_distance=best_distance)
    lexical = lexical_count > 0

    # Nothing relevant: the closest chunk is far away (stricter when no keyword matched).
    if best_distance is None and not lexical:
        return result
    if best_distance is not None:
        limit = settings.vector_max_distance if lexical else settings.vector_max_distance_no_keywords
        if best_distance > limit:
            return result

    ranked = candidates
    if reranker is not None and candidates:
        top = candidates[: settings.rerank_candidates]
        scores = reranker.scores(question, [f"{h.section_path}\n{h.text}" for h in top])
        for hit, score in zip(top, scores):
            hit.score = score
        ranked = sorted(top, key=lambda h: h.score, reverse=True) + candidates[settings.rerank_candidates:]

    budget = settings.context_tokens
    for hit in ranked:
        if len(result.hits) >= settings.context_chunks:
            break
        if hit.tokens > budget and result.hits:
            continue
        result.hits.append(hit)
        budget -= hit.tokens
    return result
