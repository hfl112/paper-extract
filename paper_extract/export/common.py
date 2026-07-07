from __future__ import annotations

from typing import Any

from ..links import is_sensitive_url


def clean_url(article: dict[str, Any]) -> str:
    """Pick a non-sensitive canonical URL for citation exports.

    Prefer the DOI resolver, then any non-sensitive publisher/pmc page.
    """
    ids = article.get("identifiers") or {}
    doi = (ids.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    for bucket in ("publisher", "pmc", "epmc", "pubmed"):
        page = ((article.get("links") or {}).get(bucket) or {}).get("page")
        if page and not is_sensitive_url(page):
            return page
    return ""


def has_metadata(article: dict[str, Any]) -> bool:
    return bool((article.get("metadata") or {}).get("title"))
