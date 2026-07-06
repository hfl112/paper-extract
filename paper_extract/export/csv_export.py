from __future__ import annotations

import csv
from pathlib import Path

from ..collection import CollectionStore
from .common import clean_url, has_metadata


# Citation-style CSV (richer than the review index articles.csv): includes
# abstract + keywords so it can seed a reference manager or a downstream LLM step.
COLUMNS = [
    "title", "authors", "journal", "pub_year", "doi", "pmid", "pmcid",
    "article_kind", "keywords", "abstract", "url",
]


def export_csv(store: CollectionStore, output: str | None = None) -> Path:
    """Export a collection to a citation CSV (sensitive links excluded)."""
    path = Path(output) if output else Path.cwd() / f"{store.name}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for article in store.iter_articles():
            if not has_metadata(article):
                continue
            meta = article.get("metadata") or {}
            ids = article.get("identifiers") or {}
            writer.writerow({
                "title": meta.get("title", ""),
                "authors": "; ".join(meta.get("authors") or []),
                "journal": meta.get("journal", ""),
                "pub_year": meta.get("pub_year", ""),
                "doi": ids.get("doi", ""),
                "pmid": ids.get("pmid", ""),
                "pmcid": ids.get("pmcid", ""),
                "article_kind": meta.get("article_kind", ""),
                "keywords": "; ".join(meta.get("keywords") or []),
                "abstract": (article.get("sections") or {}).get("abstract") or "",
                "url": clean_url(article),
            })
    return path
