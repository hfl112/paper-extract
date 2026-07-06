from __future__ import annotations

import re
from pathlib import Path

from ..collection import CollectionStore


def _escape(value: str) -> str:
    return (value or "").replace("{", "\\{").replace("}", "\\}")


def _key(article: dict) -> str:
    aid = article.get("article_id", "article")
    return re.sub(r"[^A-Za-z0-9_:-]+", "_", aid)


def export_bib(store: CollectionStore, output: str | None = None) -> Path:
    path = Path(output) if output else Path.cwd() / f"{store.name}.bib"
    entries = []
    for article in store.iter_articles():
        meta = article.get("metadata") or {}
        ids = article.get("identifiers") or {}
        if not meta.get("title"):
            continue
        fields = {
            "title": meta.get("title", ""),
            "author": " and ".join(meta.get("authors") or []),
            "journal": meta.get("journal", ""),
            "year": str(meta.get("pub_year") or ""),
            "doi": ids.get("doi", ""),
            "pmid": ids.get("pmid", ""),
        }
        body = [f"  {k} = {{{_escape(v)}}}" for k, v in fields.items() if v]
        entries.append("@article{" + _key(article) + ",\n" + ",\n".join(body) + "\n}")
    path.write_text("\n\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
    return path
