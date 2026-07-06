from __future__ import annotations

from typing import Any, Mapping

from .collection.ids import article_id as make_article_id, normalize_doi
from .time import utc_now


SCHEMA_VERSION = "1.0"


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
            "metadata": "found" if (seed.get("title") or abstract) else "not_started",
            "fulltext": "not_started",
            "pdf": "not_started",
            "llm_extract": "not_started",
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
    out["status"] = {**existing.get("status", {}), **{k: v for k, v in incoming.get("status", {}).items() if v and v != "not_started"}}
    out["updated_at"] = utc_now()
    return out
