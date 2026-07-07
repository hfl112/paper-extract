"""
第 1 步(全学科源):从 OpenAlex 检索文献。

OpenAlex 免费、无需 API key、覆盖全学科(2.5 亿+ works),补上 PubMed/Europe PMC
的生物医学局限。normalize() 输出与 europepmc_fetcher 完全一致的统一 dict,交由上层
Source/merge 合并落库。共享 _shared.retry_get(退避重试)。

摘要要点:OpenAlex 的 abstract 以 "倒排索引"(词->位置列表)返回,需还原成正文。
"""

import json
import os
import urllib.parse
from typing import Dict, List, Optional

from ._shared import doc_key, retry_get

BASE_URL = "https://api.openalex.org/works"
USER_AGENT = "paper-extract/1.0 (literature review tool)"
# Polite-pool contact (also used by the fulltext layer); neutral default.
EMAIL = os.environ.get("PAPER_EXTRACT_EMAIL") or "paper-extract@example.com"
PER_PAGE = 200  # OpenAlex per-page cap


def _strip(url: Optional[str], prefix: str) -> str:
    v = (url or "").strip()
    for p in (prefix, prefix.replace("https://", "http://")):
        if v.lower().startswith(p):
            return v[len(p):]
    return v


def abstract_from_inverted(inv: Optional[Dict[str, List[int]]]) -> str:
    """Rebuild plain-text abstract from OpenAlex's inverted index (word -> [positions])."""
    if not inv:
        return ""
    positions: List[tuple] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def normalize(work: Dict) -> Dict:
    """把一条 OpenAlex work 映射为统一 schema(对齐 europepmc_fetcher.normalize)。"""
    ids = work.get("ids") or {}
    doi = _strip(work.get("doi") or ids.get("doi"), "https://doi.org/").lower().strip()
    pmid = _strip(ids.get("pmid"), "https://pubmed.ncbi.nlm.nih.gov/").rstrip("/").strip()
    pmcid = _strip(ids.get("pmcid"), "https://www.ncbi.nlm.nih.gov/pmc/articles/").rstrip("/").strip()

    authors: List[str] = []
    for a in work.get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name") or "").strip()
        if name:
            authors.append(name)

    primary = work.get("primary_location") or {}
    journal = ((primary.get("source") or {}).get("display_name") or "").strip()

    wtype = (work.get("type") or "").strip()
    pub_types = [wtype] if wtype else []
    is_review = "review" in wtype.lower()

    # keywords: prefer topics, fall back to concepts
    keywords: List[str] = []
    for t in (work.get("topics") or []):
        name = (t.get("display_name") or "").strip()
        if name and name not in keywords:
            keywords.append(name)
    if not keywords:
        for c in (work.get("concepts") or []):
            name = (c.get("display_name") or "").strip()
            if name and name not in keywords:
                keywords.append(name)

    oa = work.get("open_access") or {}
    fulltext_urls: List[str] = [oa["oa_url"]] if oa.get("oa_url") else []

    py = work.get("publication_year")
    pub_year = int(py) if isinstance(py, int) or (isinstance(py, str) and py.isdigit()) else None
    cbc = work.get("cited_by_count")
    cited_by_count = cbc if isinstance(cbc, int) else None

    return {
        "doi": doi or None,
        "pmid": pmid or None,
        "pmcid": pmcid or None,
        "title": (work.get("title") or work.get("display_name") or "").strip(),
        "authors": authors,
        "journal": journal,
        "pub_year": pub_year,
        "pub_date": (work.get("publication_date") or "").strip(),
        "language": (work.get("language") or "").strip(),
        "is_open_access": bool(oa.get("is_oa")),
        "is_review": is_review,
        "pub_types": pub_types,
        "cited_by_count": cited_by_count,
        "keywords": keywords,
        "mesh": [],  # OpenAlex has no MeSH
        "affiliations": [],
        "author_orcids": [],
        "grants": [],
        "fulltext_urls": fulltext_urls,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "sections": {"abstract": abstract_from_inverted(work.get("abstract_inverted_index"))},
        "status": "metadata",
    }


def _year_filter(min_year: Optional[str], max_year: Optional[str]) -> str:
    parts = []
    if min_year:
        parts.append(f"from_publication_date:{min_year}-01-01")
    if max_year:
        parts.append(f"to_publication_date:{max_year}-12-31")
    return ",".join(parts)


def search_openalex(query: str, max_results: int = 1000,
                    min_year: Optional[str] = None, max_year: Optional[str] = None) -> List[Dict]:
    """检索 OpenAlex,分页至 max_results,返回归一化文档列表(按 doi/pmid 去重)。"""
    docs: List[Dict] = []
    seen = set()
    page = 1
    yf = _year_filter(min_year, max_year)
    while len(docs) < max_results:
        params = {
            "search": query,
            "per-page": str(min(PER_PAGE, max_results - len(docs))),
            "page": str(page),
            "mailto": EMAIL,
        }
        if yf:
            params["filter"] = yf
        url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
        body = retry_get(url, USER_AGENT)
        data = json.loads(body.decode("utf-8"))
        results = data.get("results") or []
        if not results:
            break
        for raw in results:
            d = normalize(raw)
            k = doc_key(d) or (d.get("title") or "")
            if k in seen:
                continue
            seen.add(k)
            docs.append(d)
        if len(results) < int(params["per-page"]):
            break
        page += 1
    return docs
