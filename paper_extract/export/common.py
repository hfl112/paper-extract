from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class CitationView:
    """The citation fields of an article, extracted once. Every exporter (bib /
    ris / csv) reads from this instead of re-digging the nested article dict, so
    field-selection logic lives in one place. Fields are kept raw (authors and
    keywords as lists, pub_year as its stored value); each format does its own
    joining/stringifying."""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    pub_year: Any = None
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    article_kind: str = ""
    keywords: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""

    @property
    def has_metadata(self) -> bool:
        return bool(self.title)


def citation_view(article: dict[str, Any]) -> CitationView:
    """Extract the citation fields from an article.json (the single place this
    mapping is defined). `url` is the non-sensitive canonical URL."""
    meta = article.get("metadata") or {}
    ids = article.get("identifiers") or {}
    return CitationView(
        title=meta.get("title", ""),
        authors=list(meta.get("authors") or []),
        journal=meta.get("journal", ""),
        pub_year=meta.get("pub_year"),
        doi=ids.get("doi", ""),
        pmid=ids.get("pmid", ""),
        pmcid=ids.get("pmcid", ""),
        article_kind=meta.get("article_kind", ""),
        keywords=list(meta.get("keywords") or []),
        abstract=(article.get("sections") or {}).get("abstract") or "",
        url=clean_url(article),
    )
