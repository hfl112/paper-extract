"""
第 1 步：从 Europe PMC 检索文献的库模块。

提供 search_europepmc()（cursorMark 分页 + 去重）与 normalize()（统一 schema）。
返回每篇一个 dict（含 sections.abstract），由上层 runner 合并落库；本模块不写文件。

仅依赖 Python 标准库；不需要 API key（Europe PMC 免费），不碰数据库。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from ._shared import retry_get

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 1000          # Europe PMC 单页上限
THROTTLE = 0.34           # 每页请求间隔（秒），约 3 req/s，友好限流
USER_AGENT = "paper-extract/1.0 (literature review tool)"


def _request(url: str, max_retries: int = 5) -> dict:
    """带指数退避重试的 GET，返回解析后的 JSON。"""
    return json.loads(retry_get(url, USER_AGENT, max_retries).decode("utf-8"))


def normalize(raw: Dict) -> Dict:
    """把 Europe PMC 的一条原始结果映射为统一的 JSON 文档。"""
    doi = (raw.get("doi") or "").lower().strip()
    pmid = (raw.get("pmid") or "").strip()
    pmcid = (raw.get("pmcid") or "").strip()

    # 作者：优先 authorList.fullName，回退 authorString
    authors: List[str] = []
    for a in raw.get("authorList", {}).get("author", []):
        name = a.get("fullName") or " ".join(
            x for x in (a.get("lastName"), a.get("initials")) if x
        )
        if name:
            authors.append(name)
    if not authors and raw.get("authorString"):
        authors = [x.strip() for x in raw["authorString"].rstrip(".").split(",") if x.strip()]

    # 全文链接
    fulltext_urls: List[str] = []
    for u in raw.get("fullTextUrlList", {}).get("fullTextUrl", []):
        if u.get("url"):
            fulltext_urls.append(u["url"])

    pub_year = raw.get("pubYear")
    pub_year = int(pub_year) if pub_year and str(pub_year).isdigit() else None

    # 期刊名：core 结果在 journalInfo.journal.title
    journal_info = raw.get("journalInfo", {}).get("journal", {})
    journal = (
        journal_info.get("title")
        or journal_info.get("medlineAbbreviation")
        or raw.get("journalTitle")
        or ""
    ).strip()

    # 出版物类型：原始 list 全保留（如 review-article / Review / Journal Article），
    # 并派生 is_review 便于快速筛选综述 vs 研究性论文
    pub_types = [t for t in raw.get("pubTypeList", {}).get("pubType", []) if t]
    is_review = any("review" in t.lower() for t in pub_types)

    # 作者关键词（作者自填，与 MeSH 互补）
    keywords = [k.strip() for k in raw.get("keywordList", {}).get("keyword", []) if k and k.strip()]

    # MeSH：Europe PMC 也返回（此前未抓，导致 EPMC 独有文献丢 mesh，这里补齐）
    mesh = [m.get("descriptorName", "").strip()
            for m in raw.get("meshHeadingList", {}).get("meshHeading", [])]
    mesh = [m for m in mesh if m]

    # 引用数（PubMed 不提供，仅 Europe PMC 有）
    cbc = raw.get("citedByCount")
    cited_by_count = int(cbc) if isinstance(cbc, int) else (
        int(cbc) if isinstance(cbc, str) and cbc.isdigit() else None)

    # 作者单位 + ORCID（去重保序）
    affiliations: List[str] = []
    author_orcids: List[str] = []
    for a in raw.get("authorList", {}).get("author", []):
        for ad in a.get("authorAffiliationDetailsList", {}).get("authorAffiliation", []):
            aff = (ad.get("affiliation") or "").strip()
            if aff and aff not in affiliations:
                affiliations.append(aff)
        aid = a.get("authorId")
        if isinstance(aid, dict) and aid.get("type") == "ORCID" and aid.get("value"):
            v = aid["value"].strip()
            if v and v not in author_orcids:
                author_orcids.append(v)

    # 基金资助
    grants = []
    for g in raw.get("grantsList", {}).get("grant", []):
        grants.append({"agency": (g.get("agency") or "").strip(),
                       "grant_id": (g.get("grantId") or "").strip()})

    return {
        "doi": doi or None,
        "pmid": pmid or None,
        "pmcid": pmcid or None,
        "title": (raw.get("title") or "").strip(),
        "authors": authors,
        "journal": journal,
        "pub_year": pub_year,
        "pub_date": (raw.get("firstPublicationDate") or "").strip(),
        "language": (raw.get("language") or "").strip(),
        "is_open_access": raw.get("isOpenAccess") == "Y",
        "is_review": is_review,
        "pub_types": pub_types,
        "cited_by_count": cited_by_count,
        "keywords": keywords,
        "mesh": mesh,
        "affiliations": affiliations,
        "author_orcids": author_orcids,
        "grants": grants,
        "fulltext_urls": fulltext_urls,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "sections": {"abstract": (raw.get("abstractText") or "").strip()},
        "status": "metadata",
    }


def search_europepmc(
    query: str,
    max_results: int = 1000,
    min_year: Optional[str] = None,
    max_year: Optional[str] = None,
) -> List[Dict]:
    """用 cursorMark 分页检索，按 DOI/PMID 去重，返回统一文档列表。"""
    full_query = query
    if min_year or max_year:
        lo = min_year or "1800"
        hi = max_year or "3000"
        full_query = f"({query}) AND (PUB_YEAR:[{lo} TO {hi}])"

    cursor = "*"
    seen = set()
    docs: List[Dict] = []
    page_size = min(max_results, PAGE_SIZE)

    print(f"检索: {full_query}")
    while len(docs) < max_results:
        params = urllib.parse.urlencode({
            "query": full_query,
            "format": "json",
            "pageSize": page_size,
            "resultType": "core",
            "cursorMark": cursor,
        })
        data = _request(f"{BASE_URL}?{params}")
        results = data.get("resultList", {}).get("result", [])
        if not results:
            break

        for raw in results:
            doc = normalize(raw)
            key = doc["doi"] or doc["pmid"] or doc["pmcid"]
            if not key or key in seen:
                continue
            seen.add(key)
            docs.append(doc)
            if len(docs) >= max_results:
                break

        hit_count = data.get("hitCount", "?")
        print(f"  已抓取 {len(docs)} / 目标 {max_results}（命中总数 {hit_count}）")

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break  # 没有更多页
        cursor = next_cursor
        time.sleep(THROTTLE)

    return docs


