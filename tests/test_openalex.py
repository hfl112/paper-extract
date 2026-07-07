"""Offline tests for the OpenAlex search source (Phase C).

Uses a captured real /works response (tests/fixtures/openalex_works.json) plus a
deterministic synthetic inverted index for the abstract-reconstruction assertion.
retry_get is monkeypatched, so nothing here touches the network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_extract.search.sources import DEFAULT_SOURCES, select_sources
from paper_extract.sources.search import openalex_fetcher

_FIXTURE = Path(__file__).parent / "fixtures" / "openalex_works.json"


def test_abstract_from_inverted_reconstructs_word_order():
    inv = {"Base": [0], "editing": [1], "corrects": [2], "the": [3], "gene": [4]}
    assert openalex_fetcher.abstract_from_inverted(inv) == "Base editing corrects the gene"


def test_abstract_from_inverted_handles_repeats_and_empty():
    inv = {"the": [0, 3], "cat": [1], "sat": [2], "mat": [4]}
    assert openalex_fetcher.abstract_from_inverted(inv) == "the cat sat the mat"
    assert openalex_fetcher.abstract_from_inverted(None) == ""
    assert openalex_fetcher.abstract_from_inverted({}) == ""


def test_normalize_real_work_maps_to_shared_shape():
    work = json.loads(_FIXTURE.read_text())["results"][0]
    d = openalex_fetcher.normalize(work)
    assert d["doi"] == "10.1038/s41586-021-03534-y"  # https://doi.org/ stripped, lowered
    assert d["pmid"] == "34012082"                    # pubmed url stripped
    assert d["journal"] == "Nature"
    assert d["pub_year"] == 2021
    assert d["pub_types"] == ["article"]
    assert "Kiran Musunuru" in d["authors"]
    assert d["doi_url"] == "https://doi.org/10.1038/s41586-021-03534-y"
    assert d["status"] == "metadata"
    assert "abstract" in d["sections"]


def test_search_openalex_offline_via_monkeypatched_retry(monkeypatch):
    body = _FIXTURE.read_bytes()
    calls = []

    def fake_retry_get(url, user_agent, max_retries=5):
        calls.append(url)
        return body

    monkeypatch.setattr(openalex_fetcher, "retry_get", fake_retry_get)
    docs = openalex_fetcher.search_openalex("crispr base editing", max_results=3)
    assert len(docs) == 3
    assert all(d["status"] == "metadata" for d in docs)
    assert calls and "api.openalex.org/works" in calls[0] and "mailto=" in calls[0]


def test_select_sources_default_and_filter_and_error():
    assert len(select_sources(None)) == 3
    assert [s.name for s in DEFAULT_SOURCES] == ["epmc", "pubmed", "openalex"]
    only = select_sources(["openalex"])
    assert len(only) == 1 and only[0].name == "openalex"
    with pytest.raises(ValueError):
        select_sources(["bogus"])
