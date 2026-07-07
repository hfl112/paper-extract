"""Search sources behind one interface.

A Source is anything that answers a query with normalized docs and names itself.
run_search iterates a registry of Sources instead of hard-wiring Europe PMC and
PubMed in its body, so a new source is a registered adapter (not an edit to the
runner) and run_search is testable with fake Sources. Provenance is carried by
`Source.name` and stamped onto each doc's `_sources` during merge — no private
key convention shared by side channel.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from ..sources.search import compare_sources, europepmc_fetcher, openalex_fetcher, pubmed_fetcher
from ..sources.search._shared import doc_key


class Source(Protocol):
    name: str

    def search(self, query: str, *, min_year: str | None = None,
               max_year: str | None = None, max_results: int = 1000) -> list[dict[str, Any]]:
        ...


class EuropePMCSource:
    name = "epmc"

    def search(self, query, *, min_year=None, max_year=None, max_results=1000):
        return europepmc_fetcher.search_europepmc(
            query, max_results=max_results, min_year=min_year, max_year=max_year)


class PubMedSource:
    name = "pubmed"

    def search(self, query, *, min_year=None, max_year=None, max_results=1000):
        pubmed_fetcher.load_env()
        api_key = os.environ.get("NCBI_API_KEY", "")
        return pubmed_fetcher.search_pubmed(
            query, api_key=api_key, max_results=max_results, min_year=min_year, max_year=max_year)


class OpenAlexSource:
    name = "openalex"

    def search(self, query, *, min_year=None, max_year=None, max_results=1000):
        return openalex_fetcher.search_openalex(
            query, max_results=max_results, min_year=min_year, max_year=max_year)


DEFAULT_SOURCES: list[Source] = [EuropePMCSource(), PubMedSource(), OpenAlexSource()]


def select_sources(names: list[str] | None) -> list[Source]:
    """Return the sources to search: all defaults if names is falsy, else the
    subset whose name matches (order follows names). Raises ValueError on an
    unknown name."""
    if not names:
        return DEFAULT_SOURCES
    by_name = {s.name: s for s in DEFAULT_SOURCES}
    chosen: list[Source] = []
    for n in names:
        if n not in by_name:
            raise ValueError(f"Unknown source: {n!r} (available: {', '.join(sorted(by_name))})")
        chosen.append(by_name[n])
    return chosen


def _dedup_union(streams: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Dedup docs across all streams by `doc_key`, unioning `_sources`. First
    occurrence of a key wins its position; later duplicates only contribute their
    source names. Docs with no key (no DOI/PMID) are kept as-is."""
    seen: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for docs in streams:
        for d in docs:
            d.setdefault("_sources", [])
            k = doc_key(d)
            if k and k in seen:
                existing = seen[k]
                for s in d["_sources"]:
                    if s not in existing["_sources"]:
                        existing["_sources"].append(s)
                continue
            if k:
                seen[k] = d
            out.append(d)
    return out


def merge_results(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge per-source doc lists into one, deduping by `doc_key` and unioning
    `_sources` provenance across every source.

    Europe PMC + PubMed additionally get a field-level enrichment merge first
    (`compare_sources.compare_and_merge` — unions MeSH/pub_types/... and stamps
    `_sources`). Its output then flows through the same dedup loop as every other
    source, so a third source sharing a DOI is deduped against it rather than
    duplicated. `results` maps source name -> docs and preserves source order."""
    streams: list[list[dict[str, Any]]] = []
    handled: set[str] = set()
    if "epmc" in results and "pubmed" in results:
        streams.append(compare_sources.compare_and_merge(results["epmc"], results["pubmed"]))
        handled = {"epmc", "pubmed"}
    for name, docs in results.items():
        if name in handled:
            continue
        for d in docs:
            d.setdefault("_sources", [name])
        streams.append(docs)
    return _dedup_union(streams)
