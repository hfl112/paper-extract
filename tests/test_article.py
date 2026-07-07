"""Tests for the Article module — the interface is the test surface.

Covers the state transitions and queries, plus a golden snapshot of new_article
(timestamp normalized) so a schema drift is caught, not silently shipped.
"""
from __future__ import annotations

from paper_extract import article


# --- golden schema snapshot (updated_at normalized) -------------------------

def _norm(a: dict) -> dict:
    a = dict(a)
    a["updated_at"] = "<ts>"
    return a


def test_new_article_golden_shape():
    got = _norm(article.new_article({
        "title": "A Study",
        "authors": ["Doe J", "Roe R"],
        "journal": "J. Test",
        "pub_year": "2021",
        "doi": "https://doi.org/10.1/ABC",
        "pmid": "123",
        "pub_types": ["Journal Article"],
        "abstract": "we did things",
    }))
    assert got == {
        "schema_version": "1.0",
        "article_id": "doi_10_1_abc",
        "identifiers": {"doi": "10.1/abc", "pmid": "123", "pmcid": ""},
        "metadata": {
            "title": "A Study",
            "authors": ["Doe J", "Roe R"],
            "journal": "J. Test",
            "pub_year": 2021,
            "pub_date": "",
            "language": "",
            "pub_types": ["Journal Article"],
            "article_kind": "research",
            "keywords": [],
            "mesh": [],
            "is_open_access": None,
        },
        "links": {"epmc": {}, "pubmed": {}, "pmc": {}, "publisher": {}, "library": {}},
        "sections": {"abstract": "we did things"},
        "files": {"pdf": ""},
        "status": {"metadata": "found", "fulltext": "not_started",
                   "pdf": "not_started", "llm_extract": "not_started"},
        "source": {"metadata": [], "fulltext": "", "pdf": ""},
        "quality": {"status": "", "body_chars": 0, "section_count": 0, "issues": [], "warnings": []},
        "updated_at": "<ts>",
    }


def test_new_article_metadata_not_started_without_title_or_abstract():
    a = article.new_article({"doi": "10.1/x"})
    assert a["status"]["metadata"] == article.NOT_STARTED
    assert not article.metadata_found(a)


# --- transitions ------------------------------------------------------------

def test_mark_metadata_found_sets_status_and_sources():
    a = article.new_article({"doi": "10.1/x"})
    article.mark_metadata(a, found=True, sources=["epmc", "pubmed"])
    assert article.metadata_found(a)
    assert a["source"]["metadata"] == ["epmc", "pubmed"]


def test_mark_metadata_failed():
    a = article.new_article({"title": "T"})
    article.mark_metadata(a, found=False)
    assert not article.metadata_found(a)
    assert a["status"]["metadata"] == article.FAILED


def _doc(sections, quality=None, source="pmc_xml"):
    return {
        "sections": sections,
        "provenance": {"access_source": source, "fulltext_url": "https://x/full"},
        "quality": quality or {"quality_status": "pass", "body_chars": 900,
                               "n_body_sections": 4, "issues": [], "warnings": []},
    }


def test_apply_fulltext_replaces_sections_and_marks_available():
    a = article.new_article({"title": "T", "abstract": "abs"})
    article.apply_fulltext(a, _doc({"abstract": "abs", "intro": "body"}))
    assert article.has_fulltext(a)
    assert a["sections"] == {"abstract": "abs", "intro": "body"}
    assert a["source"]["fulltext"] == "pmc_xml"
    assert a["quality"]["status"] == "pass"
    assert a["quality"]["section_count"] == 4
    assert a["links"]["pmc"]["page"] == "https://x/full"


def test_reset_fulltext_keeps_abstract_and_marks_failed():
    a = article.new_article({"title": "T", "abstract": "abs"})
    article.apply_fulltext(a, _doc({"abstract": "abs", "intro": "body"}))
    article.reset_fulltext(a)
    assert a["sections"] == {"abstract": "abs"}
    assert a["status"]["fulltext"] == article.FAILED
    assert a["source"]["fulltext"] == ""
    assert not article.has_fulltext(a)


def test_record_pdf_and_query():
    a = article.new_article({"title": "T"})
    assert not article.has_pdf(a)
    article.record_pdf(a, "paper.pdf", "open")
    assert article.has_pdf(a)
    assert a["files"]["pdf"] == "paper.pdf"
    assert a["source"]["pdf"] == "open"


def test_mark_pdf_failed():
    a = article.new_article({"title": "T"})
    article.mark_pdf_failed(a)
    assert a["status"]["pdf"] == article.FAILED
    assert not article.has_pdf(a)
