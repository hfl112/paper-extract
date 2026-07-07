"""
第 1 步(预印本源,选用):从 arXiv 检索文献(物理/CS/数学/quant-bio 等)。

arXiv 返回 Atom XML。normalize() 输出与 europepmc_fetcher 一致的统一 dict。arXiv 论文
自 DataCite 起有稳定 DOI(10.48550/arXiv.<id>),这里据 id 铸造该 DOI 作为身份/去重键。

默认不参与检索(非生物医学),仅在 `search --source arxiv` 时启用。共享 _shared.retry_get。
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import quote

from ._shared import retry_get

BASE_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "paper-extract/1.0 (literature review tool)"
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _arxiv_id(raw_id: str) -> str:
    """http://arxiv.org/abs/2201.00978v1 -> 2201.00978 (strip prefix + version)."""
    tail = (raw_id or "").rstrip("/").split("/")[-1]
    return re.sub(r"v\d+$", "", tail)


def normalize(entry: ET.Element) -> Dict:
    """把一条 arXiv Atom <entry> 映射为统一 schema。"""
    arxiv_id = _arxiv_id(entry.findtext("a:id", default="", namespaces=_NS))
    authors = [
        (a.findtext("a:name", default="", namespaces=_NS) or "").strip()
        for a in entry.findall("a:author", _NS)
    ]
    authors = [a for a in authors if a]
    published = (entry.findtext("a:published", default="", namespaces=_NS) or "").strip()
    pub_year = int(published[:4]) if published[:4].isdigit() else None
    keywords = [c.get("term") for c in entry.findall("a:category", _NS) if c.get("term")]

    pdf_url = ""
    for link in entry.findall("a:link", _NS):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href") or ""
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    explicit_doi = (entry.findtext("arxiv:doi", default="", namespaces=_NS) or "").strip().lower()
    doi = explicit_doi or (f"10.48550/arxiv.{arxiv_id}" if arxiv_id else "")

    return {
        "doi": doi or None,
        "pmid": None,
        "pmcid": None,
        "arxiv_id": arxiv_id or None,
        "title": " ".join((entry.findtext("a:title", default="", namespaces=_NS) or "").split()),
        "authors": authors,
        "journal": "arXiv",
        "pub_year": pub_year,
        "pub_date": published,
        "language": "",
        "is_open_access": True,
        "is_review": False,
        "pub_types": ["preprint"],
        "cited_by_count": None,
        "keywords": keywords,
        "mesh": [],
        "affiliations": [],
        "author_orcids": [],
        "grants": [],
        "fulltext_urls": [pdf_url] if pdf_url else [],
        "pubmed_url": "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "sections": {"abstract": " ".join((entry.findtext("a:summary", default="", namespaces=_NS) or "").split())},
        "status": "metadata",
    }


def search_arxiv(query: str, max_results: int = 1000,
                 min_year: Optional[str] = None, max_year: Optional[str] = None) -> List[Dict]:
    """检索 arXiv,返回归一化文档列表(按 arxiv_id 去重,年份客户端过滤)。"""
    url = f"{BASE_URL}?search_query=all:{quote(query)}&start=0&max_results={max_results}"
    body = retry_get(url, USER_AGENT)
    root = ET.fromstring(body)
    docs: List[Dict] = []
    seen = set()
    for entry in root.findall("a:entry", _NS):
        d = normalize(entry)
        key = d.get("arxiv_id") or d.get("doi") or d.get("title")
        if key in seen:
            continue
        seen.add(key)
        if min_year and d["pub_year"] and int(d["pub_year"]) < int(min_year):
            continue
        if max_year and d["pub_year"] and int(d["pub_year"]) > int(max_year):
            continue
        docs.append(d)
    return docs
