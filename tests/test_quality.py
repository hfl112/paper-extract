"""Quality-check regressions for the HTML-extraction path."""
from __future__ import annotations

from paper_extract.sources.fulltext.fulltext_fetcher import quality_block


def _doc(sections):
    return {"status": "fulltext", "sections": sections, "references": []}


def test_references_text_section_counts_as_references():
    # HTML extraction yields a "References" TEXT section but an empty parsed list;
    # this must NOT warn no_references.
    doc = _doc({
        "abstract": "abs",
        "Introduction": "x" * 500,
        "Materials And Methods": "y" * 500,
        "Result": "z" * 500,
        "Discussion": "w" * 500,
        "References": "1. Foo et al. 2. Bar et al.",
    })
    q = quality_block(doc)
    assert "no_references" not in q["warnings"], q["warnings"]
    assert q["issues"] == []


def test_no_references_still_warns_when_truly_absent():
    doc = _doc({"abstract": "abs", "Introduction": "x" * 500, "Result": "y" * 500})
    q = quality_block(doc)
    assert "no_references" in q["warnings"]
