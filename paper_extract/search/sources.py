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

from ..sources.search import compare_sources, europepmc_fetcher, pubmed_fetcher
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


DEFAULT_SOURCES: list[Source] = [EuropePMCSource(), PubMedSource()]


def merge_results(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge per-source doc lists into one, stamping `_sources` provenance.

    Europe PMC + PubMed use the field-level enrichment merge (compare_sources);
    any other combination is a dedup-by-key union. `results` maps source name ->
    docs and preserves source order."""
    if "epmc" in results and "pubmed" in results:
        merged = compare_sources.compare_and_merge(results["epmc"], results["pubmed"])
        for name, docs in results.items():
            if name in ("epmc", "pubmed"):
                continue
            for d in docs:
                d.setdefault("_sources", [name])
            merged.extend(docs)
        return merged

    seen: dict[str, dict] = {}
    out: list[dict[str, Any]] = []
    for name, docs in results.items():
        for d in docs:
            d.setdefault("_sources", [name])
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
