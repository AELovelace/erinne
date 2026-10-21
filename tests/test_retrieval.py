import io

import pytest

from mnemerynne import db, permissions, retrieval, users, worker
from mnemerynne.ingest import reembed, store
from fakes import FakeReranker, KeywordEmbedder
from pdfgen import make_pdf


@pytest.fixture
def embedder():
    return KeywordEmbedder()


@pytest.fixture
def world(conn, settings, embedder):
    """alice and bob with private notes; an Oracle library granted to alice only."""
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, oracle, alice)

    def add(data, name, collection):
        store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, None)

    add(b"# Secret\n\nAlice's secret pool code is 4411.\n", "alice.md", permissions.private_collection_id(conn, alice))
    add(b"# Secret\n\nBob's secret pool code is 9922.\n", "bob.md", permissions.private_collection_id(conn, bob))
    add(make_pdf([["Night Audit", "Run the night audit after midnight to roll the date."],
                  ["Pool Hours", "The pool is open from 8am to 10pm."],
                  ["Parking", "Parking is in lot B for registered guests."]],
                 [("Night Audit", 0, 0), ("Pool Hours", 1, 0), ("Parking", 2, 0)]),
        "OPERA.pdf", oracle)
    while worker.run_one(conn, settings, embedder):
        pass
    return {"alice": alice, "bob": bob, "oracle": oracle}


def _texts(result):
    return [h.text for h in result.hits]


def test_fts_query_building():
    assert retrieval.fts_query("How do I run the night audit?") == '"run" OR "night" OR "audit" OR "run night audit"'
    assert retrieval.fts_query("what's \"the\" (pool)?") == '"pool"'
    assert retrieval.fts_query("is it the") is None


def test_private_files_never_cross_users_in_either_branch(conn, settings, embedder, world):
    alice = retrieval.search_for_user(conn, settings, embedder, world["alice"], "secret pool code")
    bob = retrieval.search_for_user(conn, settings, embedder, world["bob"], "secret pool code")
    assert any("4411" in t for t in _texts(alice)) and not any("9922" in h.text for h in alice.candidates)
    assert any("9922" in t for t in _texts(bob)) and not any("4411" in h.text for h in bob.candidates)
    # Bob has no access to the Oracle library at all.
    assert all(h.file_name != "OPERA.pdf" for h in bob.candidates)


def test_library_results_carry_page_and_section(conn, settings, embedder, world):
    result = retrieval.search_for_user(conn, settings, embedder, world["alice"], "night audit")
    top = result.hits[0]
    assert (top.file_name, top.page_start, top.section_path) == ("OPERA.pdf", 1, "Night Audit")
    assert top.fts_rank == 1 and top.vector_rank is not None
    assert top.url == f"/files/{top.file_id}/pdf#page=1" and top.location == "p. 1"


def test_markdown_hits_link_to_anchor(conn, settings, embedder, world):
    hit = retrieval.search_for_user(conn, settings, embedder, world["alice"], "secret code").hits[0]
    assert hit.kind == "md" and hit.url.endswith("/view#secret") and hit.location == "§ Secret"


def test_not_found_gate(conn, settings, embedder, world):
    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    nothing = retrieval.search_for_user(conn, settings, embedder, world["alice"], "wifi password reset")
    assert nothing.hits == [] and nothing.lexical_hits == 0
    # A keyword match always gets through the gate.
    assert retrieval.search_for_user(conn, settings, embedder, world["alice"], "lot B").hits


def test_context_limits(conn, settings, embedder, world):
    settings.context_chunks = 1
    assert len(retrieval.search_for_user(conn, settings, embedder, world["alice"], "pool").hits) == 1


def test_reranker_reorders(conn, settings, embedder, world):
    plain = retrieval.search_for_user(conn, settings, embedder, world["alice"], "pool parking")
    reranked = retrieval.search_for_user(conn, settings, embedder, world["alice"], "pool parking",
                                         reranker=FakeReranker("lot b"))
    assert reranked.hits[0].section_path == "Parking"
    assert {h.chunk_id for h in plain.hits} == {h.chunk_id for h in reranked.hits}


def test_synonym_variants_find_documents_using_other_words(conn, settings, embedder, world):
    store.add_file(conn, settings, store.receive(io.BytesIO(b"# Pets\n\nPets are welcome with a deposit.\n"),
                                                 "pets.md", settings),
                   permissions.private_collection_id(conn, world["alice"]), None)
    while worker.run_one(conn, settings, embedder):
        pass
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    before = retrieval.search_for_user(conn, settings, embedder, world["alice"], "are dogs allowed")
    assert all("Pets are welcome" not in h.text for h in before.hits[:1])

    (settings.data_dir / "synonyms.txt").write_text("dogs = pets\n", encoding="utf-8")
    after = retrieval.search_for_user(conn, settings, embedder, world["alice"], "are dogs allowed")
    assert "Pets are welcome" in after.hits[0].text and after.hits[0].fts_rank == 1


def test_user_without_collections_gets_nothing(conn, settings, embedder):
    assert retrieval.search(conn, settings, embedder, "pool", set()).hits == []


def test_reembed_changes_dimension(conn, settings, world):
    before = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    settings.embed_dim = 6
    six = KeywordEmbedder(dim=6)
    assert reembed.reembed_all(conn, settings, six, progress=lambda *_: None) == before
    assert conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()[0] == "6"
    assert db.migrate(conn, 6) >= 2  # consistent now
    result = retrieval.search_for_user(conn, settings, six, world["alice"], "parking lot")
    assert result.hits[0].section_path == "Parking"
    # Deleting still cleans up vectors through the recreated trigger.
    conn.execute("DELETE FROM chunks")
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0
