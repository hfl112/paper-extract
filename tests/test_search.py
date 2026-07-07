"""Offline tests for run_search via the Source port (Phase 5).

Fake Sources let run_search's orchestration — fan-out, per-source failure
isolation, merge, provenance, persistence — run with no network.
"""
from __future__ import annotations

import json

import pytest

from paper_extract.collection.store import CollectionStore
from paper_extract.search.runner import run_search
from paper_extract.search.sources import merge_results


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paper_extract.collection.store.collections_root", lambda: tmp_path / "collections"
    )
    return CollectionStore.open("s")


class FakeSource:
    def __init__(self, name, docs):
        self.name = name
        self._docs = docs
        self.queries = []

    def search(self, query, *, min_year=None, max_year=None, max_results=1000):
        self.queries.append(query)
        return [dict(d) for d in self._docs]


class BoomSource:
    name = "boom"

    def search(self, *a, **k):
        raise RuntimeError("network down")


def test_run_search_persists_docs_from_injected_sources(store):
    s1 = FakeSource("a", [{"doi": "10.1/x", "title": "X", "pub_types": ["Journal Article"]}])
    s2 = FakeSource("b", [{"doi": "10.2/y", "title": "Y"}])
    run_search(store, query="cancer", sources=[s1, s2])
    arts = {a["identifiers"]["doi"]: a for a in store.iter_articles()}
    assert set(arts) == {"10.1/x", "10.2/y"}
    assert arts["10.1/x"]["status"]["metadata"] == "found"
    assert arts["10.1/x"]["source"]["metadata"] == ["a"]
    assert s1.queries == ["cancer"]  # the query reached the source


def test_run_search_dedups_same_doi_and_unions_provenance(store):
    s1 = FakeSource("a", [{"doi": "10.1/x", "title": "X"}])
    s2 = FakeSource("b", [{"doi": "10.1/x", "title": "X again"}])
    run_search(store, query="q", sources=[s1, s2])
    arts = list(store.iter_articles())
    assert len(arts) == 1
    assert set(arts[0]["source"]["metadata"]) == {"a", "b"}


def test_run_search_isolates_source_failure(store):
    ok = FakeSource("ok", [{"doi": "10.1/z", "title": "Z"}])
    log_path = run_search(store, query="q", sources=[BoomSource(), ok])
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["summary"]["failed"] == 1
    assert data["summary"]["succeeded"] == 1
    assert any(i.get("source") == "boom" and i["status"] == "failed" for i in data["items"])
    # the healthy source still persisted its doc
    assert len(list(store.iter_articles())) == 1


def test_run_search_requires_a_query(store):
    with pytest.raises(ValueError):
        run_search(store, query="", sources=[FakeSource("a", [])])


def test_merge_results_stamps_provenance_by_source_name():
    merged = merge_results({"a": [{"doi": "10.1/x", "title": "X"}]})
    assert merged[0]["_sources"] == ["a"]
