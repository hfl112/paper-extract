"""Offline tests for the arXiv search source (Phase D).

Uses a captured real Atom XML response; retry_get is monkeypatched (no network).
arXiv is opt-in: default searches must NOT include it.
"""
from __future__ import annotations

from pathlib import Path

from paper_extract.search.sources import DEFAULT_SOURCES, select_sources
from paper_extract.sources.search import arxiv_fetcher

_FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query.xml"


def test_arxiv_id_strips_prefix_and_version():
    assert arxiv_fetcher._arxiv_id("http://arxiv.org/abs/2201.00978v1") == "2201.00978"
    assert arxiv_fetcher._arxiv_id("http://arxiv.org/abs/2201.00978v3") == "2201.00978"


def test_search_arxiv_offline_normalizes(monkeypatch):
    body = _FIXTURE.read_bytes()
    calls = []

    def fake_retry_get(url, user_agent, max_retries=5):
        calls.append(url)
        return body

    monkeypatch.setattr(arxiv_fetcher, "retry_get", fake_retry_get)
    docs = arxiv_fetcher.search_arxiv("transformer", max_results=3)
    assert len(docs) == 3
    assert calls and "export.arxiv.org/api/query" in calls[0]

    d = docs[0]
    assert d["title"].startswith("PyramidTNT")
    assert d["journal"] == "arXiv"
    assert d["pub_year"] == 2022
    assert d["pub_types"] == ["preprint"]
    assert d["is_review"] is False
    assert "Kai Han" in d["authors"]
    assert d["keywords"] == ["cs.CV"]
    # arXiv id -> minted DataCite DOI (identity + dedup key)
    assert d["doi"] == "10.48550/arxiv.2201.00978"
    assert d["arxiv_id"] == "2201.00978"
    assert d["fulltext_urls"] == ["https://arxiv.org/pdf/2201.00978v1"]
    assert d["sections"]["abstract"]


def test_arxiv_is_opt_in_only():
    # default run excludes arxiv
    assert "arxiv" not in {s.name for s in DEFAULT_SOURCES}
    assert "arxiv" not in {s.name for s in select_sources(None)}
    # but selectable by explicit name
    chosen = select_sources(["arxiv"])
    assert len(chosen) == 1 and chosen[0].name == "arxiv"


def test_arxiv_year_filter_client_side(monkeypatch):
    monkeypatch.setattr(arxiv_fetcher, "retry_get",
                        lambda *a, **k: _FIXTURE.read_bytes())
    # fixture entries are 2021-2022; filtering to >=2099 yields none
    assert arxiv_fetcher.search_arxiv("transformer", max_results=3, min_year="2099") == []
