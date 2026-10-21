import pytest

from erinne.ingest import Segment, markdown, pdf
from erinne.ingest.chunk import chunk_segments, embed_text
from pdfgen import make_pdf


# --------------------------------------------------------------------- PDF
def _read(tmp_path, pages, outline=()):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf(pages, outline))
    return pdf.read_pdf(path)


def test_pdf_segments_follow_bookmarks_and_skip_contents(tmp_path):
    doc = _read(tmp_path,
                [["Contents", "1 Getting Started 2"],
                 ["Getting Started", "Welcome to the system.", "Prerequisites", "You need an account."],
                 None,
                 ["Night Audit", "Run it nightly."]],
                [("Contents", 0, 0), ("Getting Started", 1, 0), ("Prerequisites", 1, 1), ("Night Audit", 3, 0)])
    segs = pdf.segments(doc)
    assert [(s.section_path, s.page_no) for s in segs] == [
        ("Getting Started", 2), ("Getting Started > Prerequisites", 2), ("Night Audit", 4)]
    assert segs[1].text.startswith("Prerequisites\nYou need an account.")


def test_pdf_without_bookmarks_has_empty_section(tmp_path):
    segs = pdf.segments(_read(tmp_path, [["Just some text on page one."], ["And page two."]]))
    assert [(s.section_path, s.page_no) for s in segs] == [("", 1), ("", 2)]


def test_running_headers_and_footers_removed(tmp_path):
    topics = ["keys", "parking", "pools", "pets", "billing", "audits"]
    pages = [["ACME Front Desk Manual", f"About {t}.", f"Step {i} of the procedure.",
              "Same body line on every page.", f"End of {t}.", f"Page {i} of 6"]
             for i, t in enumerate(topics, start=1)]
    texts = [s.text for s in pdf.segments(_read(tmp_path, pages))]
    assert all("ACME Front Desk Manual" not in t and "Page" not in t for t in texts)
    # Body lines stay, even ones repeated on every page or differing only by a number.
    assert texts[2] == "About pools.\nStep 3 of the procedure.\nSame body line on every page.\nEnd of pools."


def test_unreadable_pdf_is_a_pdf_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(pdf.PdfError):
        pdf.page_count(bad)


# ---------------------------------------------------------------- Markdown
MD = """---
title: Front desk notes
---
Intro before any heading.

# Night Audit

Run the audit after midnight.

## Running the audit

1. Close the day.

```
# not a heading, just a comment in code
```

## Running the audit

Duplicate heading text gets a numbered anchor.

# Late Checkout & Fees!

Fees apply after noon.
"""


def test_markdown_segments_paths_and_anchors():
    segs = markdown.segments(MD)
    assert [(s.section_path, s.anchor) for s in segs] == [
        ("", None),
        ("Night Audit", "night-audit"),
        ("Night Audit > Running the audit", "running-the-audit"),
        ("Night Audit > Running the audit", "running-the-audit-1"),
        ("Late Checkout & Fees!", "late-checkout--fees"),
    ]
    assert "title:" not in segs[0].text  # front matter removed
    assert "# not a heading" in segs[2].text  # code block kept as text, not a heading


def test_markdown_without_headings_is_one_segment():
    assert markdown.segments("Just a note.\n\nTwo paragraphs.") == [Segment("Just a note.\n\nTwo paragraphs.")]


def test_rendered_ids_match_segment_anchors():
    html = markdown.render_html(MD)
    for seg in markdown.segments(MD):
        if seg.anchor:
            assert f'id="{seg.anchor}"' in html


def test_render_escapes_html_and_blocks_script_links():
    html = markdown.render_html(
        "<script>alert(1)</script>\n\n[bad](javascript:alert(1)) [ok](https://example.com) [mail](mailto:a@b.c)")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html.replace("[bad](javascript:alert(1))", "")
    assert 'href="https://example.com"' in html and 'href="mailto:a@b.c"' in html


@pytest.mark.parametrize("data", [b"\xff\xfe\x00b\x00a\x00d", "café".encode("latin-1"), b"ok\x00binary"])
def test_markdown_decode_rejects_non_utf8_or_binary(data):
    with pytest.raises(markdown.MarkdownError):
        markdown.decode(data)


def test_markdown_decode_strips_bom():
    assert markdown.decode("﻿# Hi".encode("utf-8")) == "# Hi"


# ---------------------------------------------------------------- chunking
def test_chunks_respect_sections_and_track_pages():
    long_para = " ".join(f"Sentence number {i} about room keys." for i in range(120))
    segs = [
        Segment("Short intro.", "Intro", page_no=1),
        Segment(long_para, "Keys", page_no=2),
        Segment(long_para, "Keys", page_no=3),
        Segment("Pool hours are 8 to 10.", "Pool", page_no=3),
    ]
    chunks = chunk_segments(segs, target_tokens=200, max_tokens=300, overlap_tokens=30)
    assert [c.section_path for c in chunks][0] == "Intro"
    assert chunks[-1].section_path == "Pool" and chunks[-1].text == "Pool hours are 8 to 10."
    keys = [c for c in chunks if c.section_path == "Keys"]
    assert len(keys) > 2
    assert all(len(c.text) <= 300 * 4 for c in chunks)
    assert keys[0].page_start == 2 and keys[-1].page_end == 3
    # Overlap: each Keys chunk after the first starts with the end of the previous one.
    for prev, cur in zip(keys, keys[1:]):
        assert cur.text.split("\n")[0] in prev.text


def test_short_text_across_a_page_break_stays_one_chunk():
    chunks = chunk_segments([Segment("Key cards are encoded at check-in.", "Keys", page_no=2),
                             Segment("Lost cards must be deactivated.", "Keys", page_no=3)])
    assert [(c.page_start, c.page_end) for c in chunks] == [(2, 3)]


def test_markdown_chunks_carry_anchor():
    chunks = chunk_segments(markdown.segments(MD))
    assert {c.anchor for c in chunks} == {None, "night-audit", "running-the-audit", "running-the-audit-1",
                                          "late-checkout--fees"}


def test_embed_text_includes_title_and_section():
    chunk = chunk_segments([Segment("Run it nightly.", "Night Audit", page_no=4)])[0]
    assert embed_text("OPERA Manual", chunk) == "OPERA Manual > Night Audit\nRun it nightly."
