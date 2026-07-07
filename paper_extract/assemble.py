"""Turning fetched content into article full text — one deep module.

Both fetch routes (open access in fetch/artifacts, institutional in
library/browser) converge here: given either a built full-text `doc` or a raw
`parsed` result plus provenance, this module gates on quality, writes the body
into the article (via the Article module), and marks sensitive links. The
routes only fetch content; the flatten → build_doc → quality → mark_links
sequence lives once, so a change to how an article absorbs full text is made in
one place and both routes get it.

It also owns the article↔flat-paper translation (`flatten_article`) and the
PMCID identity checks, since those are part of the same article↔doc boundary.

Placed at the top level (not under fetch/) so library/browser can call it
without importing fetch — that back-import was the fetch↔library cycle.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import article as article_mod
from . import links as links_mod
from .sources.fulltext import fulltext_fetcher

# --------------------------------------------------------------------------
# article.json  ->  flat "paper" dict the source adapters expect
# --------------------------------------------------------------------------

def flatten_article(article: dict[str, Any], validate_pmcid: bool = True) -> tuple[dict[str, Any], str]:
    """Build the flat 'paper' dict the source adapters expect from an article.json."""
    ids = article.get("identifiers") or {}
    meta = article.get("metadata") or {}
    pmcid = ids.get("pmcid") or None
    warning = ""
    if validate_pmcid and pmcid and (ids.get("doi") or ids.get("pmid")):
        if not _pmcid_matches(pmcid, ids.get("doi"), ids.get("pmid")):
            warning = f"pmcid_mismatch_cleared:{pmcid}"
            pmcid = None
    sections = article.get("sections") or {}
    out = {
        "doi": ids.get("doi") or None,
        "pmid": ids.get("pmid") or None,
        "pmcid": pmcid,
        "title": meta.get("title", ""),
        "authors": meta.get("authors") or [],
        "journal": meta.get("journal", ""),
        "pub_year": meta.get("pub_year"),
        "pub_date": meta.get("pub_date", ""),
        "language": meta.get("language", ""),
        "pub_types": meta.get("pub_types") or [],
        "keywords": meta.get("keywords") or [],
        "mesh": meta.get("mesh") or [],
        "is_open_access": meta.get("is_open_access"),
        "sections": {"abstract": sections.get("abstract") or ""},
    }
    article_links = article.get("links") or {}
    if article_links.get("publisher", {}).get("pdf"):
        out["pdf_url"] = article_links["publisher"]["pdf"]
    if article_links.get("publisher", {}).get("page"):
        out["land_url"] = article_links["publisher"]["page"]
    return out, warning


def _pmcid_matches(pmcid: str, doi: str | None, pmid: str | None) -> bool:
    xml, _status = fulltext_fetcher.fetch_fulltext_xml(pmcid)
    if not xml:
        return True
    article_ids = _front_article_ids(xml)
    if not article_ids:
        return True
    doi = _clean_doi(doi)
    pmid = (pmid or "").strip()
    if doi and _clean_doi(article_ids.get("doi")) == doi:
        return True
    if pmid and (article_ids.get("pmid") or "").strip() == pmid:
        return True
    if not doi and not pmid:
        target_pmcid = pmcid.upper().replace("PMC", "").strip()
        return (article_ids.get("pmc") or "").upper().replace("PMC", "").strip() == target_pmcid
    return False


def _clean_doi(value: str | None) -> str:
    return (value or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")


def _front_article_ids(xml: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    article = root.find(".//article") or root
    ids = {}
    for aid in article.findall(".//front//article-meta//article-id"):
        id_type = (aid.get("pub-id-type") or "").lower()
        if id_type and aid.text:
            ids[id_type] = aid.text.strip()
    return ids


def _pmcid_from_url(url: str) -> str:
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("id", [""])[0]
    if query_id:
        return f"PMC{query_id.upper().replace('PMC', '').strip()}"
    match = re.search(r"PMC(\d+)", url, flags=re.IGNORECASE)
    if match:
        return f"PMC{match.group(1)}"
    return ""


def doc_matches_article(article: dict[str, Any], doc: dict[str, Any]) -> tuple[bool, str]:
    ids = article.get("identifiers") or {}
    doi = ids.get("doi") or ""
    pmid = ids.get("pmid") or ""
    target_pmcid = (ids.get("pmcid") or "").upper()
    prov = doc.get("provenance") or {}
    source = prov.get("access_source") or doc.get("fulltext_source") or ""
    url_pmcid = _pmcid_from_url(prov.get("fulltext_url") or "")

    if source.startswith(("pmc", "epmc")) and url_pmcid:
        if target_pmcid and target_pmcid != url_pmcid:
            return False, f"fulltext_identity_mismatch:{url_pmcid}"
        if doi or pmid:
            if not _pmcid_matches(url_pmcid, doi, pmid):
                return False, f"fulltext_identity_mismatch:{url_pmcid}"
    return True, ""


# --------------------------------------------------------------------------
# assembly: fetched content -> full text written into the article
# --------------------------------------------------------------------------

def assemble_from_doc(article: dict[str, Any], doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Gate a built full-text doc on quality, write it into the article, mark
    sensitive links. Returns the updated article, or None if there was no doc
    or its quality was rejected."""
    if doc is None:
        return None
    if (doc.get("quality") or {}).get("quality_status") == "reject":
        return None
    updated = article_mod.apply_fulltext(article, doc)
    links_mod.mark_sensitive_links(updated)
    return updated


def assemble_from_parsed(article: dict[str, Any], parsed: dict[str, Any] | None,
                         provenance: dict[str, Any]) -> dict[str, Any] | None:
    """Build a doc from a parse result + provenance, then assemble it into the
    article. Returns the updated article, or None if parse was empty/rejected."""
    if parsed is None:
        return None
    flat, _w = flatten_article(article)
    doc = fulltext_fetcher.build_doc(flat.get("pmcid") or "", parsed, flat, provenance)
    return assemble_from_doc(article, doc)
