"""
第 1 步相互检查：对比 Europe PMC 与 PubMed 两份检索结果的库模块。

compare_and_merge() 做两件事：
- 报告：两边各自数量、重叠（同一 DOI/PMID）、各自独有的文献。
- 合并：以 Europe PMC 为主（保留全文链接/OA），把 PubMed 的 MeSH 补进重叠记录，
  再并入 PubMed 独有的文献 —— 两个来源的长处合一。返回合并后的文献列表。

命令行入口已统一到 code/main.py 的 search 子命令（默认 --sources epmc,pubmed
时自动调用本模块合并）。
"""

from typing import Dict, List


def key_of(doc: Dict) -> str:
    """统一去重键：归一化 DOI 优先，否则 PMID。"""
    doi = (doc.get("doi") or "").lower().strip()
    if doi:
        return f"doi:{doi}"
    pmid = (doc.get("pmid") or "").strip()
    return f"pmid:{pmid}" if pmid else ""


def index(docs: List[Dict]) -> Dict[str, Dict]:
    out = {}
    for d in docs:
        k = key_of(d)
        if k and k not in out:
            out[k] = d
    return out


def _union(a: List[str], b: List[str]) -> List[str]:
    """合并两源的字符串列表，去重保序（大小写不敏感，保留首次出现的写法）。"""
    seen, out = set(), []
    for t in list(a or []) + list(b or []):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def compare_and_merge(epmc_docs: List[Dict], pubmed_docs: List[Dict]) -> List[Dict]:
    """对比两源、打印重叠报告，返回合并增强后的文献列表
    （Europe PMC 打底，补 PubMed 的 MeSH，并入 PubMed 独有）。"""
    epmc = index(epmc_docs)
    pubmed = index(pubmed_docs)

    ke, kp = set(epmc), set(pubmed)
    both = ke & kp
    only_e = ke - kp
    only_p = kp - ke

    print(f"Europe PMC : {len(ke)} 篇")
    print(f"PubMed     : {len(kp)} 篇")
    print(f"重叠       : {len(both)} 篇")
    print(f"仅 Europe PMC: {len(only_e)} 篇")
    print(f"仅 PubMed    : {len(only_p)} 篇")
    overlap_rate = len(both) / min(len(ke), len(kp)) * 100 if ke and kp else 0
    print(f"重叠率（占较小集合）: {overlap_rate:.1f}%")

    def sample(keys, src, n=5):
        for k in list(keys)[:n]:
            print(f"    - {src[k]['title'][:80]}")

    if only_e:
        print("\n仅 Europe PMC 命中（PubMed 漏召回）示例：")
        sample(only_e, epmc)
    if only_p:
        print("\n仅 PubMed 命中（Europe PMC 漏召回）示例：")
        sample(only_p, pubmed)

    # 合并：Europe PMC 打底，两源信息互补，再并入 PubMed 独有
    merged: Dict[str, Dict] = {}
    for k, d in epmc.items():
        doc = dict(d)
        doc["_sources"] = ["epmc", "pubmed"] if k in pubmed else ["epmc"]
        if k in pubmed:
            p = pubmed[k]
            # 列表型字段取两源并集（去重保序）
            for fld in ("pub_types", "keywords", "mesh", "affiliations", "author_orcids"):
                doc[fld] = _union(doc.get(fld), p.get(fld))
            # is_review 据并集后的 pub_types 重算（任一源标记综述即算）
            doc["is_review"] = any("review" in t.lower() for t in doc["pub_types"])
            # 标量：EPMC 为空时用 PubMed 补（cited_by_count 仅 EPMC 有，保留 EPMC）
            for fld in ("pub_date", "language", "grants"):
                if not doc.get(fld) and p.get(fld):
                    doc[fld] = p[fld]
        merged[k] = doc
    for k in only_p:
        doc = dict(pubmed[k])                            # 并入 PubMed 独有
        doc["_sources"] = ["pubmed"]
        merged[k] = doc

    return list(merged.values())
