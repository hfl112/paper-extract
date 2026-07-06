"""
给文献打「癌症类型」多标签：扫 title + abstract + MeSH + keywords，把提到的癌症全列出来。

纯关键词匹配（确定性、无 LLM、可复现），思路同抓关键词：
  - CANCER_LIBRARY 是规范瘤种 → 别名/正则片段 的词库；
  - 命中即收录，一篇可有多个瘤种（cancers 是 list）；
  - 「(unspecified)」泛类只在没有更具体的同族瘤种命中时才保留（避免 ALL 同时被标成
     "Acute Lymphoblastic Leukemia" + "Leukemia (unspecified)"）；
  - 有泛癌信号(cancer/tumor/…)但无任何具体瘤种 → 标 "Cancer (unspecified)"；
  - 完全无癌症信号 → cancers 为空（非肿瘤论文）。

新增字段：cancers（list[str]）、is_cancer（bool）

纯库模块；命令行入口在 code/main.py 的 cancer 子命令：
    python3 code/main.py cancer pptp_result/pptp.json
"""

import re
import csv
from typing import Dict, List

# ── 癌症词库：规范名 → 匹配模式（正则，大小写不敏感）─────────────────────────────
# 避免裸 2 字母缩写歧义（如不收裸 "ALL"）；良性 -oma（adenoma/lipoma 等）不收。
CANCER_LIBRARY: Dict[str, List[str]] = {
    # —— 血液系统 ——
    "Acute Lymphoblastic Leukemia": [r"acute lymphoblastic leuk", r"lymphoblastic leuk[ae]mia",
                                     r"\bB-ALL\b", r"\bT-ALL\b", r"precursor.{0,15}lymphoblastic"],
    "Acute Myeloid Leukemia": [r"acute myeloid leuk", r"acute myelogenous leuk", r"\bAML\b"],
    "Chronic Myeloid Leukemia": [r"chronic myeloid leuk", r"chronic myelogenous leuk", r"\bCML\b"],
    "Chronic Lymphocytic Leukemia": [r"chronic lymphocytic leuk", r"\bCLL\b"],
    "Leukemia (unspecified)": [r"leuk[ae]mia", r"leukemic"],
    "Hodgkin Lymphoma": [r"hodgkin lymphoma", r"hodgkin'?s lymphoma", r"hodgkin disease"],
    "Non-Hodgkin Lymphoma": [r"non.?hodgkin", r"diffuse large b.?cell", r"\bDLBCL\b",
                             r"burkitt", r"follicular lymphoma", r"mantle cell lymphoma"],
    "Lymphoma (unspecified)": [r"lymphoma"],
    "Multiple Myeloma": [r"multiple myeloma", r"plasma cell myeloma"],
    "Myelodysplastic Syndrome": [r"myelodysplastic", r"\bMDS\b"],
    "Histiocytosis": [r"histiocytosis", r"langerhans cell"],

    # —— 中枢神经系统 ——
    "Glioblastoma": [r"glioblastoma", r"\bGBM\b"],
    "DIPG/Diffuse Midline Glioma": [r"diffuse intrinsic pontine", r"\bDIPG\b", r"diffuse midline glioma"],
    "Astrocytoma": [r"astrocytoma"],
    "Oligodendroglioma": [r"oligodendroglioma"],
    "Glioma (unspecified)": [r"glioma"],
    "Medulloblastoma": [r"medulloblastoma"],
    "Ependymoma": [r"ependymoma"],
    "ATRT/Rhabdoid Tumor": [r"atypical teratoid", r"rhabdoid tumou?r", r"\bATRT\b"],
    "Craniopharyngioma": [r"craniopharyngioma"],
    "Meningioma": [r"meningioma"],
    "Brain/CNS Tumor (unspecified)": [r"brain tumou?r", r"brain neoplasm", r"\bCNS tumou?r",
                                      r"intracranial tumou?r", r"central nervous system tumou?r"],

    # —— 儿童常见实体瘤 ——
    "Neuroblastoma": [r"neuroblastoma"],
    "Wilms Tumor": [r"wilms", r"nephroblastoma"],
    "Retinoblastoma": [r"retinoblastoma"],
    "Hepatoblastoma": [r"hepatoblastoma"],
    "Germ Cell Tumor": [r"germ cell tumou?r", r"teratoma", r"yolk sac tumou?r"],

    # —— 肉瘤 ——
    "Osteosarcoma": [r"osteosarcoma", r"osteogenic sarcoma"],
    "Ewing Sarcoma": [r"ewing"],
    "Rhabdomyosarcoma": [r"rhabdomyosarcoma"],
    "Leiomyosarcoma": [r"leiomyosarcoma"],
    "Liposarcoma": [r"liposarcoma"],
    "Synovial Sarcoma": [r"synovial sarcoma"],
    "Chondrosarcoma": [r"chondrosarcoma"],
    "Fibrosarcoma": [r"fibrosarcoma"],
    "Angiosarcoma": [r"angiosarcoma"],
    "Gastrointestinal Stromal Tumor": [r"gastrointestinal stromal", r"\bGIST\b"],
    "Sarcoma (unspecified)": [r"sarcoma"],

    # —— 成人常见癌 ——
    "Breast Cancer": [r"breast cancer", r"breast carcinoma", r"breast tumou?r", r"mammary carcinoma"],
    "Lung Cancer": [r"lung cancer", r"lung carcinoma", r"\bNSCLC\b", r"\bSCLC\b",
                    r"non.?small cell lung", r"small cell lung"],
    "Colorectal Cancer": [r"colorectal", r"colon cancer", r"rectal cancer", r"\bCRC\b"],
    "Gastric Cancer": [r"gastric cancer", r"gastric carcinoma", r"stomach cancer"],
    "Esophageal Cancer": [r"esophageal cancer", r"oesophageal cancer", r"esophageal carcinoma"],
    "Hepatocellular Carcinoma": [r"hepatocellular", r"\bHCC\b"],
    "Cholangiocarcinoma": [r"cholangiocarcinoma", r"bile duct cancer"],
    "Pancreatic Cancer": [r"pancreatic cancer", r"pancreatic carcinoma",
                          r"pancreatic ductal adenocarcinoma", r"\bPDAC\b"],
    "Prostate Cancer": [r"prostate cancer", r"prostate carcinoma", r"prostatic carcinoma"],
    "Bladder Cancer": [r"bladder cancer", r"urothelial carcinoma", r"bladder carcinoma"],
    "Renal Cell Carcinoma": [r"renal cell carcinoma", r"\bRCC\b", r"kidney cancer"],
    "Ovarian Cancer": [r"ovarian cancer", r"ovarian carcinoma"],
    "Cervical Cancer": [r"cervical cancer", r"cervical carcinoma"],
    "Endometrial Cancer": [r"endometrial cancer", r"endometrial carcinoma", r"uterine cancer"],
    "Thyroid Cancer": [r"thyroid cancer", r"thyroid carcinoma"],
    "Head and Neck Cancer": [r"head and neck", r"\bHNSCC\b", r"oral squamous"],
    "Nasopharyngeal Carcinoma": [r"nasopharyngeal"],
    "Melanoma": [r"melanoma"],
    "Adrenocortical Carcinoma": [r"adrenocortical"],
    "Mesothelioma": [r"mesothelioma"],
    "Neuroendocrine Tumor": [r"neuroendocrine"],
}

# 「(unspecified)」泛类 → 一旦命中下列任一更具体瘤种，则去掉该泛类
_SPECIFIC_SUPPRESSORS = {
    "Leukemia (unspecified)": ["Acute Lymphoblastic Leukemia", "Acute Myeloid Leukemia",
                               "Chronic Myeloid Leukemia", "Chronic Lymphocytic Leukemia"],
    "Lymphoma (unspecified)": ["Hodgkin Lymphoma", "Non-Hodgkin Lymphoma"],
    "Glioma (unspecified)": ["Glioblastoma", "DIPG/Diffuse Midline Glioma",
                             "Astrocytoma", "Oligodendroglioma"],
    "Sarcoma (unspecified)": ["Osteosarcoma", "Ewing Sarcoma", "Rhabdomyosarcoma", "Leiomyosarcoma",
                              "Liposarcoma", "Synovial Sarcoma", "Chondrosarcoma", "Fibrosarcoma",
                              "Angiosarcoma", "Gastrointestinal Stromal Tumor"],
    "Brain/CNS Tumor (unspecified)": ["Glioblastoma", "DIPG/Diffuse Midline Glioma", "Astrocytoma",
                                      "Oligodendroglioma", "Glioma (unspecified)", "Medulloblastoma",
                                      "Ependymoma", "ATRT/Rhabdoid Tumor", "Craniopharyngioma",
                                      "Meningioma"],
}

# 泛癌信号：用于「有癌症信号但叫不出具体瘤种」的兜底标签
_GENERIC = re.compile(
    r"(cancer|tumou?r|oncolog|neoplas|malignan|carcinoma|sarcoma|leuk[ae]mia|lymphoma|"
    r"blastoma|melanoma|glioma|chemotherap|antineoplastic|antitumou?r)", re.I)

_COMPILED = {c: [re.compile(p, re.I) for p in pats] for c, pats in CANCER_LIBRARY.items()}


def doc_text(doc: Dict) -> str:
    return " ".join(
        [doc.get("title", "")]
        + doc.get("mesh", [])
        + doc.get("keywords", [])
        + [doc.get("sections", {}).get("abstract", "")]
    )


def find_cancers(text: str) -> List[str]:
    """返回文本中提到的所有规范瘤种（去掉被更具体瘤种压制的泛类），按词库顺序。"""
    hits = [c for c, rs in _COMPILED.items() if any(r.search(text) for r in rs)]
    hitset = set(hits)
    out = []
    for c in hits:
        sup = _SPECIFIC_SUPPRESSORS.get(c)
        if sup and any(s in hitset for s in sup):
            continue  # 有更具体的同族瘤种，丢掉这个泛类
        out.append(c)
    if not out and _GENERIC.search(text):
        out = ["Cancer (unspecified)"]
    return out


def mentions_cancer(text: str) -> bool:
    """判断一段文本（如检索词/关键词）是否提到癌症 —— 用于自动触发标注。"""
    return bool(find_cancers(text))


def tag_docs(docs: List[Dict]) -> List[Dict]:
    """就地给每篇加 cancers（list）与 is_cancer（bool）。"""
    for doc in docs:
        cancers = find_cancers(doc_text(doc))
        doc["cancers"] = cancers
        doc["is_cancer"] = bool(cancers)
    return docs


# ── CSV：复用合并表列 + 追加 is_cancer / cancers ──────────────────────────────

def write_csv(docs: List[Dict], path: str) -> None:
    cols = ["title", "journal", "pub_year", "doi", "pmid", "is_review",
            "is_cancer", "cancers"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for d in docs:
            w.writerow([
                d.get("title", ""),
                d.get("journal", ""),
                d.get("pub_year", "") or "",
                d.get("doi", "") or "",
                d.get("pmid", "") or "",
                "Y" if d.get("is_review") else "N",
                "Y" if d.get("is_cancer") else "N",
                " | ".join(d.get("cancers", [])),
            ])
