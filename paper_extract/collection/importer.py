from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..schema import new_article
from ..sources.search import europepmc_fetcher
from ..time import utc_now
from .store import CollectionStore


def _read_input(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("10."):
            rows.append({"doi": value})
        elif value.isdigit():
            rows.append({"pmid": value})
        else:
            rows.append({"title": value})
    return rows


def _read_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("articles"), list):
        return data["articles"]
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"Unsupported JSON shape: {path}")


def _enrich(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("title") and (row.get("doi") or row.get("pmid")):
        return row
    doi = (row.get("doi") or "").strip()
    pmid = (row.get("pmid") or "").strip()
    query = ""
    if doi:
        query = f'DOI:"{doi}"'
    elif pmid:
        query = f'EXT_ID:{pmid}'
    if not query:
        return row
    try:
        docs = europepmc_fetcher.search_europepmc(query, max_results=1)
    except Exception:
        return row
    if not docs:
        return row
    enriched = dict(row)
    for k, v in docs[0].items():
        if v not in (None, "", [], {}):
            enriched.setdefault(k, v)
            if not enriched.get(k):
                enriched[k] = v
    return enriched


def import_articles(
    store: CollectionStore,
    *,
    input_path: str | None = None,
    input_json: str | None = None,
    input_doi: list[str] | None = None,
    input_pmid: list[str] | None = None,
) -> Path:
    started = utc_now()
    rows: list[dict[str, Any]] = []
    if input_path:
        rows.extend(_read_input(Path(input_path)))
    if input_json:
        rows.extend(_read_json(Path(input_json)))
    for doi in input_doi or []:
        rows.append({"doi": doi})
    for pmid in input_pmid or []:
        rows.append({"pmid": pmid})
    if not rows:
        raise ValueError("No import input provided")

    items = []
    succeeded = failed = 0
    for row in rows:
        try:
            enriched = _enrich(row)
            article = new_article(enriched)
            if article["status"]["metadata"] == "not_started":
                article["status"]["metadata"] = "failed"
            if article["status"]["metadata"] == "found":
                article["source"]["metadata"] = ["import"]
            store.upsert_article(article)
            status = "succeeded" if article["status"]["metadata"] == "found" else "failed"
            succeeded += int(status == "succeeded")
            failed += int(status == "failed")
            items.append({"article_id": article["article_id"], "status": status, "attempts": []})
        except Exception as e:
            failed += 1
            items.append({"article_id": "", "status": "failed", "reason": type(e).__name__, "attempts": []})
    articles = store.iter_articles()
    store.write_articles_csv(articles)
    store.update_stats(articles)
    return store.write_log(
        "import",
        {"input": input_path or "", "input_json": input_json or "", "input_doi": input_doi or [], "input_pmid": input_pmid or []},
        {"total": len(rows), "succeeded": succeeded, "failed": failed, "skipped": 0},
        items,
        started,
    )
