"""Fast, offline unit tests for pure helper functions."""
from __future__ import annotations

from paper_extract.collection.ids import article_id_from_parts, normalize_doi
from paper_extract.schema import article_kind, merge_article, new_article
from paper_extract.time import stamp_from_iso, utc_now


def test_normalize_doi_strips_prefixes():
    assert normalize_doi("https://doi.org/10.1002/PBC.21508") == "10.1002/pbc.21508"
    assert normalize_doi("doi:10.1/x") == "10.1/x"
    assert normalize_doi("  10.5/Y.  ") == "10.5/y"
    assert normalize_doi(None) == ""


def test_article_id_priority():
    assert article_id_from_parts(doi="10.1002/pbc.21508") == "doi_10_1002_pbc_21508"
    assert article_id_from_parts(pmid="123") == "pmid_123"
    assert article_id_from_parts(pmcid="PMC9") == "pmcid_pmc9"
    assert article_id_from_parts(title="Some Title").startswith("hash_")


def test_article_kind_rules():
    assert article_kind(["Systematic Review"]) == "review"
    assert article_kind(["Meta-Analysis"]) == "review"
    assert article_kind(["Journal Article"]) == "research"
    assert article_kind(["Randomized Controlled Trial"]) == "research"
    assert article_kind(["Letter"]) == "other"
    # Letter alone is "other", but with Journal Article it counts as research.
    assert article_kind(["Letter", "Journal Article"]) == "research"
    assert article_kind([]) == "unknown"


def test_merge_article_prefers_existing_scalar_fills_empty():
    base = new_article({"doi": "10.1/x", "title": "Original"})
    incoming = new_article({"doi": "10.1/x", "title": "Changed", "journal": "Nature"})
    merged = merge_article(base, incoming)
    # existing non-empty scalar kept
    assert merged["metadata"]["title"] == "Original"
    # empty scalar filled from incoming
    assert merged["metadata"]["journal"] == "Nature"


def test_merge_article_unions_lists():
    base = new_article({"doi": "10.1/x", "authors": ["A"]})
    incoming = new_article({"doi": "10.1/x", "authors": ["A", "B"]})
    merged = merge_article(base, incoming)
    assert merged["metadata"]["authors"] == ["A", "B"]


def test_stamp_from_iso():
    assert stamp_from_iso("2026-07-05T19:42:00Z") == "20260705T194200Z"
    assert utc_now().endswith("Z")
