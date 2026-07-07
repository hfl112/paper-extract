from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import article as article_mod
from ..article import new_article
from ..collection import CollectionStore
from ..time import utc_now
from .sources import DEFAULT_SOURCES, Source, merge_results


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
    sources: list[Source] | None = None,
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
    results: dict[str, list[dict[str, Any]]] = {}
    for src in (sources if sources is not None else DEFAULT_SOURCES):
        try:
            docs = src.search(query, min_year=min_year, max_year=max_year, max_results=max_results)
        except Exception as e:
            items.append({"source": src.name, "status": "failed", "reason": type(e).__name__})
            continue
        if docs:
            results[src.name] = docs

    docs = merge_results(results)
    succeeded = 0
    for doc in docs:
        article = new_article(doc)
        article_mod.mark_metadata(article, found=True, sources=doc.get("_sources") or ["unknown"])
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
