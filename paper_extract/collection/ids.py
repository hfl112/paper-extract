from __future__ import annotations

import hashlib
import re
from typing import Mapping, Any


def normalize_doi(doi: str | None) -> str:
    value = (doi or "").strip().lower()
    value = re.sub(r"^(doi:\s*|https?://(?:dx\.)?doi\.org/)", "", value, flags=re.I)
    return value.strip().strip(".")


def article_id_from_parts(
    doi: str | None = None,
    pmid: str | None = None,
    pmcid: str | None = None,
    title: str | None = None,
) -> str:
    doi_norm = normalize_doi(doi)
    if doi_norm:
        safe = re.sub(r"[^a-z0-9]+", "_", doi_norm).strip("_")
        return f"doi_{safe}"
    if pmid:
        return f"pmid_{str(pmid).strip()}"
    if pmcid:
        return f"pmcid_{str(pmcid).strip().lower()}"
    basis = (title or "unknown").strip().lower().encode("utf-8")
    return "hash_" + hashlib.sha1(basis).hexdigest()[:12]


def article_id(doc: Mapping[str, Any]) -> str:
    identifiers = doc.get("identifiers") if isinstance(doc.get("identifiers"), dict) else {}
    return article_id_from_parts(
        doi=doc.get("doi") or identifiers.get("doi"),
        pmid=doc.get("pmid") or identifiers.get("pmid"),
        pmcid=doc.get("pmcid") or identifiers.get("pmcid"),
        title=doc.get("title") or (doc.get("metadata") or {}).get("title"),
    )
