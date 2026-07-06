"""
通用去重合并工具的库模块：接受任意多个 JSON 文件（每份是文献列表），按 DOI/PMID
去重合并。

每篇文献保留 _found_in 字段，记录被哪些源文件命中。
合并策略：同一篇文章若多个来源都有，保留有 fulltext_urls 的版本；
          若都有或都没有，保留先出现的版本，将所有来源合并到 _found_in。

命令行入口已统一到 code/main.py 的 merge 子命令（支持 glob，如 _tmp_*/*.json）：
    python code/main.py merge file1.json file2.json --out merged.json
"""

import json
import csv
import os
from typing import Dict, List


CSV_COLS = [
    "title", "authors", "journal", "pub_year", "doi", "pmid",
    "is_open_access", "is_review", "pub_types", "cited_by_count", "language",
    "fulltext_urls", "pubmed_url", "doi_url", "_found_in",
]


def doc_key(doc: Dict) -> str:
    doi = (doc.get("doi") or "").lower().strip()
    if doi:
        return f"doi:{doi}"
    pmid = (doc.get("pmid") or "").strip()
    return f"pmid:{pmid}" if pmid else ""


def merge_files(paths: List[str]) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    for path in paths:
        label = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            docs = json.load(f)
        for doc in docs:
            k = doc_key(doc)
            if not k:
                continue
            if k not in merged:
                entry = dict(doc)
                entry["_found_in"] = [label]
                merged[k] = entry
            else:
                existing = merged[k]
                if label not in existing["_found_in"]:
                    existing["_found_in"].append(label)
                # 如果现有版本没有全文链接、新版本有，升级
                if not existing.get("fulltext_urls") and doc.get("fulltext_urls"):
                    found_in = existing["_found_in"]
                    merged[k] = dict(doc)
                    merged[k]["_found_in"] = found_in
    return list(merged.values())


def print_summary(pmc_n: int, pubmed_n: int, overlap_n: int, merged: List[Dict]) -> None:
    """打印英文统计：两源各自篇数、重叠、并集总数、综述 vs 研究性论文。"""
    total = len(merged)
    reviews = sum(1 for d in merged if d.get("is_review"))
    print("\n==== Summary ====")
    print(f"Europe PMC fetched     : {pmc_n}")
    print(f"PubMed fetched         : {pubmed_n}")
    print(f"Overlap (in both)      : {overlap_n}")
    print(f"Merged (union total)   : {total}")
    print(f"  Reviews              : {reviews}")
    print(f"  Articles (non-review): {total - reviews}")


def write_csv(docs: List[Dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for d in docs:
            w.writerow([
                d.get("title", ""),
                "; ".join(d.get("authors", [])),
                d.get("journal", ""),
                d.get("pub_year", "") or "",
                d.get("doi", "") or "",
                d.get("pmid", "") or "",
                "Y" if d.get("is_open_access") else "N",
                "Y" if d.get("is_review") else "N",
                " | ".join(d.get("pub_types", [])),
                d.get("cited_by_count") if d.get("cited_by_count") is not None else "",
                d.get("language", ""),
                " | ".join(d.get("fulltext_urls", [])),
                d.get("pubmed_url", ""),
                d.get("doi_url", ""),
                " | ".join(d.get("_found_in", [])),
            ])


