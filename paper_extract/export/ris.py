from __future__ import annotations

from pathlib import Path

from ..collection import CollectionStore
from .common import clean_url, has_metadata


def _kind_ty(article: dict) -> str:
    kind = (article.get("metadata") or {}).get("article_kind", "")
    return "JOUR"  # journal article covers research/review/other for reference managers


def export_ris(store: CollectionStore, output: str | None = None) -> Path:
    """Export a collection to RIS (EndNote / Zotero / Mendeley import format)."""
    path = Path(output) if output else Path.cwd() / f"{store.name}.ris"
    lines: list[str] = []
    for article in store.iter_articles():
        if not has_metadata(article):
            continue
        meta = article.get("metadata") or {}
        ids = article.get("identifiers") or {}
        lines.append(f"TY  - {_kind_ty(article)}")
        lines.append(f"TI  - {meta.get('title', '')}")
        for author in meta.get("authors") or []:
            lines.append(f"AU  - {author}")
        if meta.get("journal"):
            lines.append(f"JO  - {meta['journal']}")
            lines.append(f"T2  - {meta['journal']}")
        if meta.get("pub_year"):
            lines.append(f"PY  - {meta['pub_year']}")
        if ids.get("doi"):
            lines.append(f"DO  - {ids['doi']}")
        abstract = (article.get("sections") or {}).get("abstract") or ""
        if abstract:
            lines.append(f"AB  - {abstract}")
        for kw in meta.get("keywords") or []:
            lines.append(f"KW  - {kw}")
        url = clean_url(article)
        if url:
            lines.append(f"UR  - {url}")
        lines.append("ER  - ")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
