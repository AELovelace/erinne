from mnemerynne import synonyms


def test_variants_swap_jargon():
    assert synonyms.variants("How do I run the night audit?", synonyms.DEFAULT_GROUPS) == [
        "How do I run the end of day?"]
    v = synonyms.variants("Late checkout fee?", synonyms.DEFAULT_GROUPS)
    assert v == ["Late check-out fee?", "Late departure fee?"]


def test_no_partial_word_matches_and_no_variants_without_jargon():
    assert synonyms.variants("Is the compressor broken?", synonyms.DEFAULT_GROUPS) == []  # not "comp"
    assert synonyms.variants("Where is the pool?", synonyms.DEFAULT_GROUPS) == []


def test_custom_groups_from_file(tmp_path):
    path = tmp_path / "synonyms.txt"
    path.write_text("# ours\nPMS = Opera\n\nbad line without equals\nGM = general manager\n", encoding="utf-8")
    groups = synonyms.load_groups(path)
    assert ["gm", "general manager"] in groups
    assert synonyms.variants("Ask the GM", groups) == ["Ask the general manager"]


def test_variant_limit():
    q = "night audit checkout check-in folio"
    assert len(synonyms.variants(q, synonyms.DEFAULT_GROUPS)) == synonyms.MAX_VARIANTS
