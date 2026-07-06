from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..collection import CollectionStore
from ..schema import new_article
from ..sources.search import compare_sources, europepmc_fetcher, pubmed_fetcher
from ..time import utc_now


def query_from_plan(plan: dict[str, Any]) -> str:
    queries = plan.get("queries") or {}
    return queries.get("epmc") or queries.get("pubmed") or ""


def run_search(
    store: CollectionStore,
    *,
    query: str | None = None,
    plan_path: str | None = None,
    min_year: str | None = None,
    max_year: str | None = None,
    max_results: int = 1000,
) -> Path:
    started = utc_now()
    plan = None
    if not query:
        if plan_path:
            plan_file = Path(plan_path)
        else:
            current = store.read_collection().get("current_plan")
            if not current:
                raise ValueError("No --query, --plan, or collection current_plan available")
            plan_file = store.root / current
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        query = query_from_plan(plan)
        min_year = min_year or (plan.get("year_filter") or {}).get("min_year")
        max_year = max_year or (plan.get("year_filter") or {}).get("max_year")
    if not query:
        raise ValueError("Search query is empty")

    items = []
    epmc_docs = []
    pubmed_docs = []
    try:
        epmc_docs = europepmc_fetcher.search_europepmc(query, max_results=max_results, min_year=min_year, max_year=max_year)
    except Exception as e:
        items.append({"source": "epmc", "status": "failed", "reason": type(e).__name__})
    try:
        pubmed_fetcher.load_env()
        api_key = os.environ.get("NCBI_API_KEY", "")
        pubmed_docs = pubmed_fetcher.search_pubmed(query, api_key=api_key, max_results=max_results, min_year=min_year, max_year=max_year)
    except Exception as e:
        items.append({"source": "pubmed", "status": "failed", "reason": type(e).__name__})

    if epmc_docs and pubmed_docs:
        docs = compare_sources.compare_and_merge(epmc_docs, pubmed_docs)
    else:
        docs = epmc_docs or pubmed_docs

    default_sources = ["epmc", "pubmed"] if epmc_docs and pubmed_docs else (["epmc"] if epmc_docs else ["pubmed"])
    succeeded = 0
    for doc in docs:
        article = new_article(doc)
        article["source"]["metadata"] = doc.get("_sources") or default_sources
        article["status"]["metadata"] = "found"
        store.upsert_article(article)
        items.append({"article_id": article["article_id"], "status": "succeeded", "attempts": []})
        succeeded += 1
    articles = store.iter_articles()
    store.write_articles_csv(articles)
    store.update_stats(articles)
    return store.write_log(
        "search",
        {"query": query, "min_year": min_year, "max_year": max_year, "max": max_results, "plan": plan_path or ""},
        {"total": len(docs), "succeeded": succeeded, "failed": len([i for i in items if i.get("status") == "failed"]), "skipped": 0},
        items,
        started,
    )
