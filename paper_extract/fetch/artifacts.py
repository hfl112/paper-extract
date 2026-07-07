"""Open-access fetch routes.

Thin: each entry point fetches content via the source engine, then hands off to
the shared assembler (paper_extract.assemble) for the flatten → build → quality
→ mark-links sequence. The article↔flat translation and identity checks live in
that assembler, not here.
"""
from __future__ import annotations

from typing import Any

from .. import article as article_mod
from .. import assemble as assemble_mod
from .. import links as links_mod
from ..collection import CollectionStore
from ..sources.fulltext import fulltext_sources


def fetch_json_open(article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Fetch structured full text via open sources. Returns (updated_article|None, reason)."""
    flat, warning = assemble_mod.flatten_article(article)
    doc, reason = fulltext_sources.get_fulltext(flat)
    if doc is None:
        return None, "; ".join(x for x in (warning, reason) if x)
    matches, mismatch_reason = assemble_mod.doc_matches_article(article, doc)
    if not matches:
        return None, "; ".join(x for x in (warning, mismatch_reason) if x)
    if warning:
        article.setdefault("identifiers", {})["pmcid"] = ""
        article.setdefault("links", {}).setdefault("pmc", {}).pop("page", None)
        article.setdefault("links", {}).setdefault("pmc", {}).pop("pdf", None)
    updated = assemble_mod.assemble_from_doc(article, doc)
    return updated, warning


def fetch_pdf_open(store: CollectionStore, article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Download and save a PDF via open sources. Returns (updated_article|None, reason)."""
    flat, warning = assemble_mod.flatten_article(article)
    pdf, url = fulltext_sources.download_pdf(flat)
    if not pdf:
        return None, "; ".join(x for x in (warning, "pdf_download_failed") if x)
    path = store.pdf_path(article["article_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    rel = str(path.relative_to(store.article_dir(article["article_id"])))
    article_mod.record_pdf(article, rel, "open")
    if url:
        article.setdefault("links", {}).setdefault("publisher", {})["pdf"] = url
    links_mod.mark_sensitive_links(article)
    return article, ""
