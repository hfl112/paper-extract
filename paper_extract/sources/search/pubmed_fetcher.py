"""
第 1 步（备用源）：从 NCBI PubMed 检索文献，返回与 europepmc_fetcher.py 完全一致的
归一化 dict（每篇一个），便于两个来源相互核对（召回差异、metadata 一致性）与合并落库。

相比 Europe PMC，PubMed 的优势是 MeSH automatic term mapping（检索召回更全更准）；
劣势是不直接返回 OA 状态与全文链接 —— 这里用 PMCID 推断（有 PMC 全文≈可免费获取）。

用 NCBI E-utilities + history server 分页支持上千篇；NCBI_API_KEY 提速（3→10 req/s）。
从 .env 读取 key（无需第三方依赖）。
"""

import urllib.request
import urllib.parse
import urllib.error
import http.client
import xml.etree.ElementTree as ET
import json
import os
import time
from typing import List, Dict, Optional

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
BATCH = 200                # 每次 efetch 取多少篇（XML 较大，保守取值）
USER_AGENT = "paper-extract/1.0 (literature review tool)"


def load_env(filename: str = ".env") -> None:
    """从脚本所在目录逐级向上查找 .env 并加载（不引入第三方依赖）。
    这样脚本放在子目录（如 step1_search/）也能读到根目录的 .env。"""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        path = os.path.join(d, filename)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            return
        parent = os.path.dirname(d)
        if parent == d:    # 到达文件系统根
            return
        d = parent


def _request(url: str, max_retries: int = 5) -> bytes:
    """带指数退避重试的 GET，返回原始 bytes。"""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                print(f"  HTTP {e.code}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                print(f"  网络错误 {e.reason}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (http.client.IncompleteRead, ConnectionError, TimeoutError) as e:
            # 连接层读取中断（大响应体常见），同样退避重试
            if attempt < max_retries - 1:
                print(f"  读取中断 {type(e).__name__}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("超过最大重试次数")


def _text(node: Optional[ET.Element]) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""


def normalize(article: ET.Element) -> Dict:
    """把一条 PubmedArticle XML 映射为与 Europe PMC 一致的统一文档。"""
    medline = article.find("MedlineCitation")
    art = medline.find("Article") if medline is not None else None
    if art is None:
        return {}

    pmid = _text(medline.find("PMID"))

    # DOI / PMCID 在 PubmedData/ArticleIdList，DOI 也可能在 ELocationID
    doi, pmcid = "", ""
    for aid in article.findall(".//ArticleIdList/ArticleId"):
        idtype = aid.get("IdType")
        if idtype == "doi" and not doi:
            doi = (aid.text or "").lower().strip()
        elif idtype == "pmc":
            pmcid = (aid.text or "").strip()
    if not doi:
        for eloc in art.findall("ELocationID"):
            if eloc.get("EIdType") == "doi":
                doi = (eloc.text or "").lower().strip()
                break

    title = _text(art.find("ArticleTitle"))

    # 摘要：多段 AbstractText（可能带 Label）拼接
    parts = []
    for node in art.findall(".//AbstractText"):
        label = node.get("Label", "")
        txt = _text(node)
        parts.append(f"**{label}**: {txt}" if label else txt)
    abstract = " ".join(p for p in parts if p)

    # 作者（顺带收集单位 + ORCID，去重保序）
    authors: List[str] = []
    affiliations: List[str] = []
    author_orcids: List[str] = []
    author_list = art.find("AuthorList")
    if author_list is not None:
        for a in author_list.findall("Author"):
            last = a.find("LastName")
            initials = a.find("Initials")
            collective = a.find("CollectiveName")
            if last is not None:
                authors.append(f"{last.text} {initials.text}" if initials is not None else last.text)
            elif collective is not None and collective.text:
                authors.append(collective.text)
            for aff_node in a.findall("AffiliationInfo/Affiliation"):
                aff = _text(aff_node)
                if aff and aff not in affiliations:
                    affiliations.append(aff)
            for ident in a.findall("Identifier"):
                if ident.get("Source") == "ORCID":
                    v = _text(ident)
                    if v and v not in author_orcids:
                        author_orcids.append(v)

    journal = _text(art.find("Journal/Title"))

    # 年份：PubDate/Year，回退 MedlineDate 前 4 位
    pub_year = None
    pubdate = art.find("Journal/JournalIssue/PubDate")
    if pubdate is not None:
        y = pubdate.find("Year")
        if y is not None and y.text and y.text.isdigit():
            pub_year = int(y.text)
        else:
            md = pubdate.find("MedlineDate")
            if md is not None and md.text and md.text[:4].isdigit():
                pub_year = int(md.text[:4])

    # MeSH 主题词（PubMed 独有价值，留在 JSON 里供后续筛选）
    mesh = [_text(d) for d in medline.findall("MeshHeadingList/MeshHeading/DescriptorName")]

    # 出版物类型：PublicationTypeList（如 Journal Article / Review / Meta-Analysis），
    # 派生 is_review 便于快速筛选
    pub_types = [_text(p) for p in art.findall("PublicationTypeList/PublicationType")]
    pub_types = [t for t in pub_types if t]
    is_review = any("review" in t.lower() for t in pub_types)

    # 作者关键词（与 MeSH 互补）
    keywords = [_text(k) for k in medline.findall("KeywordList/Keyword")]
    keywords = [k for k in keywords if k]

    # 语言
    language = _text(art.find("Language"))

    # 完整出版日期：优先 ArticleDate（电子出版），拼成 YYYY-MM-DD
    pub_date = ""
    adate = art.find("ArticleDate")
    if adate is not None:
        y = _text(adate.find("Year"))
        m = _text(adate.find("Month"))
        dd = _text(adate.find("Day"))
        if y:
            pub_date = "-".join([y] + [p.zfill(2) for p in (m, dd) if p])

    # 基金资助
    grants = []
    for g in art.findall("GrantList/Grant"):
        grants.append({"agency": _text(g.find("Agency")),
                       "grant_id": _text(g.find("GrantID"))})

    # PubMed 不直接给 OA / 全文链接：用 PMCID 推断
    is_oa = bool(pmcid)
    fulltext_urls = []
    if pmcid:
        fulltext_urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/")

    return {
        "doi": doi or None,
        "pmid": pmid or None,
        "pmcid": pmcid or None,
        "title": title,
        "authors": authors,
        "journal": journal,
        "pub_year": pub_year,
        "pub_date": pub_date,
        "language": language,
        "is_open_access": is_oa,          # 注意：基于 PMCID 推断，非权威
        "is_review": is_review,
        "pub_types": pub_types,
        "cited_by_count": None,           # PubMed 不提供引用数（Europe PMC 才有）
        "keywords": keywords,
        "mesh": mesh,
        "affiliations": affiliations,
        "author_orcids": author_orcids,
        "grants": grants,
        "fulltext_urls": fulltext_urls,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "sections": {"abstract": abstract},
        "status": "metadata",
    }


def search_pubmed(
    query: str,
    api_key: str,
    max_results: int = 1000,
    min_year: Optional[str] = None,
    max_year: Optional[str] = None,
) -> List[Dict]:
    """esearch（usehistory）拿 WebEnv，再分批 efetch 解析。"""
    api_param = f"&api_key={api_key}" if api_key else ""
    throttle = 0.11 if api_key else 0.34   # 有 key 10 req/s，无 key 3 req/s

    # 1) esearch + history server
    es_params = {
        "db": "pubmed", "term": query, "usehistory": "y",
        "retmax": "0", "retmode": "json",
    }
    if min_year or max_year:
        es_params["datetype"] = "pdat"
        es_params["mindate"] = min_year or "1800"
        es_params["maxdate"] = max_year or "3000"
    url = f"{ESEARCH}?{urllib.parse.urlencode(es_params)}{api_param}"
    res = json.loads(_request(url).decode("utf-8"))["esearchresult"]

    total = int(res.get("count", "0"))
    webenv = res.get("webenv")
    query_key = res.get("querykey")
    if not total or not webenv:
        return []

    n = min(total, max_results)
    print(f"检索: {query}（命中总数 {total}，抓取 {n}）")

    # 2) 分批 efetch
    docs: List[Dict] = []
    seen = set()
    for start in range(0, n, BATCH):
        ef_params = {
            "db": "pubmed", "query_key": query_key, "WebEnv": webenv,
            "retstart": str(start), "retmax": str(min(BATCH, n - start)),
            "retmode": "xml",
        }
        url = f"{EFETCH}?{urllib.parse.urlencode(ef_params)}{api_param}"
        xml = _request(url)
        root = ET.fromstring(xml)
        for article in root.findall(".//PubmedArticle"):
            doc = normalize(article)
            if not doc:
                continue
            key = doc["doi"] or doc["pmid"] or doc["pmcid"]
            if not key or key in seen:
                continue
            seen.add(key)
            docs.append(doc)
        print(f"  已抓取 {len(docs)} / 目标 {n}")
        time.sleep(throttle)

    return docs


