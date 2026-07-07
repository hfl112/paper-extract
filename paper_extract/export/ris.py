from __future__ import annotations

from pathlib import Path

from ..collection import CollectionStore
from .common import citation_view


def export_ris(store: CollectionStore, output: str | None = None) -> Path:
    """Export a collection to RIS (EndNote / Zotero / Mendeley import format)."""
    path = Path(output) if output else Path.cwd() / f"{store.name}.ris"
    lines: list[str] = []
    for article in store.iter_articles():
        v = citation_view(article)
        if not v.has_metadata:
            continue
        # JOUR covers research/review/other for reference managers.
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {v.title}")
        for author in v.authors:
            lines.append(f"AU  - {author}")
        if v.journal:
            lines.append(f"JO  - {v.journal}")
            lines.append(f"T2  - {v.journal}")
        if v.pub_year:
            lines.append(f"PY  - {v.pub_year}")
        if v.doi:
            lines.append(f"DO  - {v.doi}")
        if v.abstract:
            lines.append(f"AB  - {v.abstract}")
        for kw in v.keywords:
            lines.append(f"KW  - {kw}")
        if v.url:
            lines.append(f"UR  - {v.url}")
        lines.append("ER  - ")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
