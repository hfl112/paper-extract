from __future__ import annotations

import csv
from pathlib import Path

from ..collection import CollectionStore
from .common import citation_view

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
            v = citation_view(article)
            if not v.has_metadata:
                continue
            writer.writerow({
                "title": v.title,
                "authors": "; ".join(v.authors),
                "journal": v.journal,
                "pub_year": v.pub_year if v.pub_year is not None else "",
                "doi": v.doi,
                "pmid": v.pmid,
                "pmcid": v.pmcid,
                "article_kind": v.article_kind,
                "keywords": "; ".join(v.keywords),
                "abstract": v.abstract,
                "url": v.url,
            })
    return path
