from __future__ import annotations

from typing import Any


def apply_fulltext_doc(article: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Write a fetched full-text doc's sections/quality/source into the article.

    Sections are replaced wholesale (not merged) so stale body sections from a
    prior fetch cannot survive a successful re-fetch.
    """
    article["sections"] = doc.get("sections") or article.get("sections") or {}
    prov = doc.get("provenance") or {}
    source = prov.get("access_source") or doc.get("fulltext_source") or "unknown"
    article.setdefault("source", {})["fulltext"] = source
    article.setdefault("status", {})["fulltext"] = "available"
    quality = doc.get("quality") or {}
    article["quality"] = {
        "status": quality.get("quality_status") or quality.get("status") or "",
        "body_chars": quality.get("body_chars", 0),
        "section_count": quality.get("n_body_sections") or quality.get("n_sections") or len(article["sections"]),
        "issues": quality.get("issues") or [],
        "warnings": quality.get("warnings") or [],
    }
    url = prov.get("fulltext_url")
    if url:
        article.setdefault("links", {}).setdefault(source.split("_")[0], {})["page"] = url
    return article


def reset_fulltext(article: dict[str, Any]) -> None:
    """Drop any full-text body, keeping only the abstract. Marks fulltext failed.

    Only call this when the article had NO prior successful full text; never use
    it to overwrite an existing 'available' full text on a failed re-fetch.
    """
    sections = article.get("sections") or {}
    abstract = sections.get("abstract") or ""
    article["sections"] = {"abstract": abstract}
    article.setdefault("status", {})["fulltext"] = "failed"
    article.setdefault("source", {})["fulltext"] = ""
    article["quality"] = {"status": "", "body_chars": 0, "section_count": 0, "issues": [], "warnings": []}
