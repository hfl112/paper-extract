"""The Article module — the single owner of the article.json shape and its
state transitions.

Every fact about an article's on-disk schema lives here: how a fresh article is
built (`new_article`), how two records merge (`merge_article`), the status
enum values (`FOUND`/`AVAILABLE`/…), the transitions that move an article
between states (`mark_metadata`/`apply_fulltext`/`reset_fulltext`/`record_pdf`/
`mark_pdf_failed`), and the queries that read state back (`metadata_found`/
`has_fulltext`/`has_pdf`). Callers name a transition; they never poke the nested
dict or hardcode a status string. That keeps state logic in one place (locality)
and behind one interface (leverage), and makes the schema the test surface.
"""
from __future__ import annotations

from typing import Any, Mapping

from .collection.ids import article_id as make_article_id, normalize_doi
from .time import utc_now


SCHEMA_VERSION = "1.0"

# Status enum values. This is the ONLY module allowed to spell these literals;
# every other module references them through these names or the query helpers.
NOT_STARTED = "not_started"
FOUND = "found"
FAILED = "failed"
AVAILABLE = "available"


# --------------------------------------------------------------------------
# Schema construction (was schema.py)
# --------------------------------------------------------------------------

def article_kind(pub_types: list[str]) -> str:
    vals = {p.lower() for p in pub_types if p}
    has = lambda needle: any(needle in p for p in vals)
    if has("review") or has("meta-analysis"):
        return "review"
    research_markers = (
        "research-article",
        "journal article",
        "clinical trial",
        "randomized controlled trial",
        "comparative study",
        "observational study",
    )
    if any(any(m in p for p in vals) for m in research_markers):
        return "research"
    other_markers = ("letter", "comment", "editorial", "erratum", "guideline", "video")
    if any(any(m in p for p in vals) for m in other_markers):
        return "other"
    return "unknown"


def _list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        for sep in (";", "|"):
            if sep in value:
                return [part.strip() for part in value.split(sep) if part.strip()]
        return [value.strip()] if value.strip() else []
    return [value]


def _int_or_none(value: Any):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return value


def new_article(seed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    seed = seed or {}
    doi = normalize_doi(seed.get("doi"))
    pmid = str(seed.get("pmid") or "").strip()
    pmcid = str(seed.get("pmcid") or "").strip()
    pub_types = _list(seed.get("pub_types"))
    abstract = ""
    if isinstance(seed.get("sections"), dict):
        abstract = seed["sections"].get("abstract") or ""
    abstract = seed.get("abstract") or abstract
    aid = make_article_id({"doi": doi, "pmid": pmid, "pmcid": pmcid, "title": seed.get("title")})
    now = utc_now()
    article = {
        "schema_version": SCHEMA_VERSION,
        "article_id": aid,
        "identifiers": {"doi": doi or "", "pmid": pmid or "", "pmcid": pmcid or ""},
        "metadata": {
            "title": seed.get("title") or "",
            "authors": _list(seed.get("authors")),
            "journal": seed.get("journal") or "",
            "pub_year": _int_or_none(seed.get("pub_year")),
            "pub_date": seed.get("pub_date") or "",
            "language": seed.get("language") or "",
            "pub_types": pub_types,
            "article_kind": article_kind(pub_types),
            "keywords": _list(seed.get("keywords")),
            "mesh": _list(seed.get("mesh")),
            "is_open_access": seed.get("is_open_access"),
        },
        "links": {
            "epmc": {},
            "pubmed": {},
            "pmc": {},
            "publisher": {},
            "library": {},
        },
        "sections": {"abstract": abstract or ""},
        "files": {"pdf": ""},
        "status": {
            "metadata": FOUND if (seed.get("title") or abstract) else NOT_STARTED,
            "fulltext": NOT_STARTED,
            "pdf": NOT_STARTED,
            "llm_extract": NOT_STARTED,
        },
        "source": {"metadata": [], "fulltext": "", "pdf": ""},
        "quality": {"status": "", "body_chars": 0, "section_count": 0, "issues": [], "warnings": []},
        "updated_at": now,
    }
    if seed.get("pubmed_url"):
        article["links"]["pubmed"]["page"] = seed["pubmed_url"]
    if seed.get("doi_url"):
        article["links"]["publisher"]["page"] = seed["doi_url"]
    for url in seed.get("fulltext_urls") or []:
        if "pmc" in url.lower():
            article["links"]["pmc"].setdefault("page", url)
        else:
            article["links"]["publisher"].setdefault("page", url)
    if pmcid:
        article["links"]["pmc"].setdefault("page", f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/")
        article["links"]["pmc"].setdefault("pdf", f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/")
    return article


def merge_article(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = dict(existing)
    out["identifiers"] = {**existing.get("identifiers", {}), **{k: v for k, v in incoming.get("identifiers", {}).items() if v}}
    meta = dict(existing.get("metadata", {}))
    for k, v in incoming.get("metadata", {}).items():
        if isinstance(v, list):
            seen = list(meta.get(k) or [])
            for item in v:
                if item and item not in seen:
                    seen.append(item)
            meta[k] = seen
        elif v not in (None, "", []) and not meta.get(k):
            meta[k] = v
    meta["article_kind"] = article_kind(list(meta.get("pub_types") or []))
    out["metadata"] = meta
    for bucket, links in incoming.get("links", {}).items():
        out.setdefault("links", {}).setdefault(bucket, {})
        out["links"][bucket].update({k: v for k, v in links.items() if v})
    sections = dict(existing.get("sections") or {})
    for k, v in (incoming.get("sections") or {}).items():
        if v and not sections.get(k):
            sections[k] = v
    out["sections"] = sections
    out["status"] = {**existing.get("status", {}), **{k: v for k, v in incoming.get("status", {}).items() if v and v != NOT_STARTED}}
    out["updated_at"] = utc_now()
    return out


# --------------------------------------------------------------------------
# State transitions (were scattered across search/import/fetch + fetch/quality)
# --------------------------------------------------------------------------

def mark_metadata(article: dict[str, Any], *, found: bool, sources: list[str] | None = None) -> dict[str, Any]:
    """Set the metadata status (found/failed) and, when given, its provenance."""
    article.setdefault("status", {})["metadata"] = FOUND if found else FAILED
    if sources is not None:
        article.setdefault("source", {})["metadata"] = list(sources)
    return article


def apply_fulltext(article: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Write a fetched full-text doc's sections/quality/source into the article.

    Sections are replaced wholesale (not merged) so stale body sections from a
    prior fetch cannot survive a successful re-fetch.
    """
    article["sections"] = doc.get("sections") or article.get("sections") or {}
    prov = doc.get("provenance") or {}
    source = prov.get("access_source") or doc.get("fulltext_source") or "unknown"
    article.setdefault("source", {})["fulltext"] = source
    article.setdefault("status", {})["fulltext"] = AVAILABLE
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
    article.setdefault("status", {})["fulltext"] = FAILED
    article.setdefault("source", {})["fulltext"] = ""
    article["quality"] = {"status": "", "body_chars": 0, "section_count": 0, "issues": [], "warnings": []}


def record_pdf(article: dict[str, Any], rel_path: str, source: str) -> dict[str, Any]:
    """Record a saved PDF: its collection-relative path, availability, provenance."""
    article.setdefault("files", {})["pdf"] = rel_path
    article.setdefault("status", {})["pdf"] = AVAILABLE
    article.setdefault("source", {})["pdf"] = source
    return article


def mark_pdf_failed(article: dict[str, Any]) -> None:
    article.setdefault("status", {})["pdf"] = FAILED


# --------------------------------------------------------------------------
# State queries — "what state is this article in" answered in one place
# --------------------------------------------------------------------------

def _status(article: Mapping[str, Any], key: str) -> str:
    return (article.get("status") or {}).get(key, "")


def metadata_found(article: Mapping[str, Any]) -> bool:
    return _status(article, "metadata") == FOUND


def has_fulltext(article: Mapping[str, Any]) -> bool:
    return _status(article, "fulltext") == AVAILABLE


def has_pdf(article: Mapping[str, Any]) -> bool:
    return _status(article, "pdf") == AVAILABLE
