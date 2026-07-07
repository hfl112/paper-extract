from __future__ import annotations

import re
from pathlib import Path

from ..collection import CollectionStore
from .common import citation_view


def _escape(value: str) -> str:
    return (value or "").replace("{", "\\{").replace("}", "\\}")


def _key(article: dict) -> str:
    aid = article.get("article_id", "article")
    return re.sub(r"[^A-Za-z0-9_:-]+", "_", aid)


def export_bib(store: CollectionStore, output: str | None = None) -> Path:
    path = Path(output) if output else Path.cwd() / f"{store.name}.bib"
    entries = []
    for article in store.iter_articles():
        v = citation_view(article)
        if not v.has_metadata:
            continue
        fields = {
            "title": v.title,
            "author": " and ".join(v.authors),
            "journal": v.journal,
            "year": str(v.pub_year or ""),
            "doi": v.doi,
            "pmid": v.pmid,
        }
        body = [f"  {k} = {{{_escape(val)}}}" for k, val in fields.items() if val]
        entries.append("@article{" + _key(article) + ",\n" + ",\n".join(body) + "\n}")
    path.write_text("\n\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
    return path
