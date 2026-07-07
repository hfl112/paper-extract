"""
Step 2：抓开放获取文章的全文，解析成结构化 JSON。

数据源：NCBI E-utilities efetch（db=pmc），对 PMC 开放获取子集返回 JATS 全文 XML。
（注：Europe PMC 的 fullTextXML 端点已不可用/常 404，且只给 HTML/PDF 链接；改走 NCBI。）

目标：把文章里**每一个大标题块**都抓进来——title / abstract / Introduction / Methods /
Results / Discussion / Data availability / Code availability / Funding / Acknowledgements /
Conflicts of interest / … 凡是有标题的段落，标题作 key、正文作 value，存进「成长型文档」的
sections 字典。不同杂志的标题不一样没关系，只要内容在就行。References 单列成 list。

仅依赖 Python 标准库，独立可移植。主流程通过 `paper_extract.fetch.runner`
调用这里的函数：

    from paper_extract.sources.fulltext.fulltext_fetcher import extract_fulltext
    doc = extract_fulltext("PMC13176527", base=article_metadata)

    # 拆开用
    xml, reason = fetch_fulltext_xml(pmcid)   # 网络；失败时 xml=None、reason 说明原因
    parsed = parse_jats(xml)                  # dict: title/sections/references/supplementary/license/...
    doc = build_doc(pmcid, parsed, base, provenance)

    # 校验某篇质量（分层）：fetch/parse/quality_status + issues/warnings
    from paper_extract.sources.fulltext.fulltext_fetcher import check_extraction, quality_block
    quality_block(doc)
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _attr_href(el: ET.Element) -> str:
    """取元素上的 xlink:href（_strip_ns 只去标签 ns，不去属性 ns，故按后缀匹配）。"""
    for k, v in el.attrib.items():
        if k.endswith("href"):
            return v
    return ""

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PMC_HTML_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{num}/"
USER_AGENT = "paper-extract/step2 (literature ETL)"
BROWSER_UA = "Mozilla/5.0 (compatible; paper-extract/step2)"


def load_env(filename: str = ".env") -> None:
    """从本文件所在目录逐级向上找 .env 并加载（读 NCBI_API_KEY，标准库实现）。"""
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
        if parent == d:
            return
        d = parent


# ── 抓取 ──────────────────────────────────────────────────────────────────────

def _request(url: str, max_retries: int = 5) -> bytes:
    delay = 1.0
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                print(f"  HTTP {e.code}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay); delay *= 2; continue
            raise
        except (urllib.error.URLError, http.client.IncompleteRead,
                ConnectionError, TimeoutError) as e:
            if attempt < max_retries - 1:
                print(f"  网络/读取错误 {type(e).__name__}，{delay:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay); delay *= 2; continue
            raise
    raise RuntimeError("超过最大重试次数")


def fetch_fulltext_xml(pmcid: str, api_key: str = "") -> Tuple[Optional[bytes], str]:
    """抓 PMC 全文 JATS XML（efetch db=pmc）。

    返回 (xml_bytes, "") 成功；失败返回 (None, reason)，reason 取值：
      no_pmcid / http_<code> / network_error / not_oa_subset / not_jats
    """
    if not pmcid:
        return None, "no_pmcid"
    num = pmcid.upper().replace("PMC", "").strip()
    url = f"{EFETCH}?db=pmc&id={num}&rettype=xml&retmode=xml"
    if api_key:
        url += f"&api_key={api_key}"
    try:
        data = _request(url)
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except Exception:
        return None, "network_error"
    # 不在 OA 子集时 efetch 返回 <error>...does not allow downloading...</error>
    if b"<body" not in data and b"<error" in data:
        return None, "not_oa_subset"
    if b"<article" not in data:
        return None, "not_jats"
    return data, ""


# ── PMC 网页 HTML 兜底 ─────────────────────────────────────────────────────────
# 有些文章有 PMCID、PMC 网页能读全文，但不在「机器可下载的 OA-XML 子集」里
# （efetch 返回无 <body>）。这时退回解析 PMC 网页 HTML 把正文救回来。
# 内容同为 NCBI 托管的开放获取正文；请低频率、仅对已知 ID 抓取。

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def resolve_pmcid(doi: Optional[str] = None, pmid: Optional[str] = None) -> Optional[str]:
    """用 Europe PMC 按 DOI / PMID 反查 PMCID（step1 没回填或后来才进 PMC 的文章）。
    查不到返回 None（说明没有 PMC 版本，结构化全文取不到）。"""
    queries = []
    if doi:
        queries.append(f'DOI:"{doi}"')
    if pmid:
        queries.append(f'EXT_ID:{pmid} AND SRC:MED')
    for q in queries:
        url = f"{EPMC_SEARCH}?{urllib.parse.urlencode({'query': q, 'format': 'json', 'pageSize': 1})}"
        try:
            data = json.loads(_request(url))
        except Exception:
            continue
        res = data.get("resultList", {}).get("result", [])
        if res and res[0].get("pmcid"):
            return res[0]["pmcid"].upper()
    return None


def fetch_pmc_html(pmcid: str) -> Tuple[Optional[str], str]:
    """抓 PMC 文章网页 HTML。返回 (html_text, "") 或 (None, reason)。"""
    if not pmcid:
        return None, "no_pmcid"
    num = pmcid.upper().replace("PMC", "").strip()
    try:
        req = urllib.request.Request(PMC_HTML_URL.format(num=num),
                                     headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "ignore"), ""
    except urllib.error.HTTPError as e:
        return None, f"html_http_{e.code}"
    except Exception:
        return None, "html_network_error"


class _PMCHtmlParser(HTMLParser):
    """从 PMC 网页正文区收集 标题(h1-h4) → 段落(p/li) 文本。"""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.sections: Dict[str, List[str]] = {}
        self._cur = None
        self._h = self._p = False
        self._hb: List[str] = []
        self._pb: List[str] = []
        self._skip = 0
        self._h1_seen = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav"):
            self._skip += 1
        elif tag == "h1" and not self._h1_seen:
            self._h, self._hb = True, []
        elif tag in ("h2", "h3", "h4"):
            self._h, self._hb = True, []
        elif tag in ("p", "li"):
            self._p, self._pb = True, []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav") and self._skip:
            self._skip -= 1
        elif tag == "h1" and self._h and not self._h1_seen:
            self.title = unescape("".join(self._hb)).strip()
            self._h, self._h1_seen = False, True
        elif tag in ("h2", "h3", "h4") and self._h:
            t = " ".join(unescape("".join(self._hb)).split())
            self._h = False
            if t:
                self._cur = t
                self.sections.setdefault(t, [])
        elif tag in ("p", "li") and self._p:
            txt = " ".join(unescape("".join(self._pb)).split())
            self._p = False
            if txt and self._cur:
                self.sections[self._cur].append(txt)

    def handle_data(self, data):
        if self._skip:
            return
        if self._h:
            self._hb.append(data)
        elif self._p:
            self._pb.append(data)


def _html_region_to_parsed(html_text: str, region: str) -> Dict:
    """把一段正文 HTML region 用 _PMCHtmlParser（标题 h1-h4 → 段落 p/li）解析成
    parse_jats 同结构的 dict。references/supplementary 暂不抽（HTML 结构不稳）；
    license 从整页 CC 链接尽力识别；pub_flags 从标题判断。供 PMC 网页与出版商落地页共用。"""
    p = _PMCHtmlParser()
    p.feed(region)

    sections: Dict[str, str] = {}
    for title, paras in p.sections.items():
        text = "\n\n".join(paras).strip()
        if not text:
            continue
        key = "abstract" if title.strip().lower() == "abstract" else title
        sections[key] = (sections[key] + "\n\n" + text) if key in sections else text

    # 许可：扫页面里的 creativecommons 链接
    lic, lic_url = "", ""
    m = re.search(r'https?://creativecommons\.org/(?:licenses|publicdomain)/[a-z0-9./-]+', html_text, re.I)
    if m:
        lic_url = m.group(0)
        lic = "Creative Commons (from page link)"
    flags, notes = _pub_flags_from_jats(ET.Element("x"), p.title)   # 仅按标题判断
    return {"title": p.title, "sections": sections, "references": [],
            "supplementary": [], "license": lic, "license_url": lic_url,
            "pub_flags": flags, "pub_flag_notes": notes}


def parse_pmc_html(html_text: str) -> Dict:
    """解析 PMC 网页 HTML，返回与 parse_jats 同结构的 dict（能力弱于 JATS）。

    只取正文容器 <section aria-label="Article content"> 内的标题块。
    """
    i = html_text.find('aria-label="Article content"')
    region = html_text[i:] if i >= 0 else html_text
    return _html_region_to_parsed(html_text, region)


# 出版商落地页正文容器标记（已实测的才写死；其余靠 <article>/<main> 兜底）。
#   - Wiley：<section class="article-section__content">（10.1002/ajh.25716 实测命中）
# 追加新出版商时，务必先对真实页面探针确认类名，再写进这里（勿凭猜测，见 Karger 教训）。
_ARTICLE_BODY_MARKERS = ('article-section__content',)


def parse_article_html(html_text: str) -> Dict:
    """通用出版商落地页 HTML → 结构化 sections（复用 PMC 的 h1-h4→p/li 分组逻辑）。

    定位正文区优先级：① 已知出版商容器标记；② <article>；③ <main>；④ 退化到整页。
    正文抽不到（只有摘要/无 body）时由下游 quality_block 判 reject，本函数不自行判空。
    """
    low = html_text.lower()
    region = None
    for marker in _ARTICLE_BODY_MARKERS:
        i = low.find(marker.lower())
        if i >= 0:
            region = html_text[i:]
            break
    if region is None:
        for tag in ('<article', '<main'):
            i = low.find(tag)
            if i >= 0:
                region = html_text[i:]
                break
    if region is None:
        region = html_text
    return _html_region_to_parsed(html_text, region)


# ── JATS 解析 ─────────────────────────────────────────────────────────────────

def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _direct_paragraphs(sec: ET.Element) -> str:
    """本节直属 <p> 段落（用于摘要等简单场景）。"""
    return "\n\n".join(_text(p) for p in sec.findall("p") if _text(p))


def _table_to_md(el: ET.Element) -> str:
    """把 JATS <table-wrap> 或 <table> 转成 Markdown 表格（带 label/caption）。"""
    table = el if el.tag == "table" else el.find(".//table")
    head = ""
    if el.tag == "table-wrap":
        lab, cap = _text(el.find("label")), _text(el.find("caption"))
        head = (f"**{lab}** {cap}").strip()
    if table is None:
        return head
    grid = []
    for tr in table.findall(".//tr"):
        cells = [_text(c) for c in list(tr) if c.tag in ("th", "td")]
        cells = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        if cells:
            grid.append(cells)
    if not grid:
        return head
    ncol = max(len(r) for r in grid)
    grid = [r + [""] * (ncol - len(r)) for r in grid]
    md = ["| " + " | ".join(grid[0]) + " |",
          "| " + " | ".join(["---"] * ncol) + " |"]
    md += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return (head + "\n" if head else "") + "\n".join(md)


def _list_to_text(lst: ET.Element) -> str:
    """JATS <list> → 项目符号文本。"""
    items = []
    for it in lst.findall("list-item"):
        t = _text(it)
        if t:
            items.append(f"- {t}")
    return "\n".join(items)


def _sec_to_text(sec: ET.Element, depth: int = 0) -> str:
    """把一个 sec 按文档顺序拼成文本：段落 / 表格(Markdown) / 列表 / boxed-text /
    图注 / 子 sec(带子标题)都纳入，避免丢块。"""
    parts = []
    for child in list(sec):
        tag = child.tag
        if tag == "title":
            continue
        elif tag == "p":
            t = _text(child)
            if t:
                parts.append(t)
        elif tag in ("table-wrap", "table"):
            md = _table_to_md(child)
            if md:
                parts.append(md)
        elif tag == "list":
            lt = _list_to_text(child)
            if lt:
                parts.append(lt)
        elif tag == "boxed-text":
            bt = "\n\n".join(_text(p) for p in child.findall(".//p") if _text(p)) or _text(child)
            if bt:
                parts.append(bt)
        elif tag == "fig":
            lab, cap = _text(child.find("label")), _text(child.find("caption"))
            if cap:
                parts.append(f"[{lab or 'Figure'}] {cap}".strip())
        elif tag == "sec":
            subtitle = _text(child.find("title"))
            subtext = _sec_to_text(child, depth + 1)
            if subtitle and subtext:
                parts.append(f"{'#' * (depth + 2)} {subtitle}\n{subtext}")
            elif subtext:
                parts.append(subtext)
            elif subtitle:
                parts.append(f"{'#' * (depth + 2)} {subtitle}")
    return "\n\n".join(parts)


def _add_section(sections: Dict[str, str], title: str, text: str, fallback_idx: int) -> None:
    """把一个标题块塞进 sections；无标题给个占位名；同名则合并。"""
    if not text and not title:
        return
    key = title.strip() if title.strip() else f"Section {fallback_idx}"
    if key in sections:                       # 同名标题：合并内容
        sections[key] = sections[key] + "\n\n" + text
    else:
        sections[key] = text


def _abstract_text(root: ET.Element) -> str:
    parts = []
    for ab in root.findall(".//front//abstract"):
        subs = ab.findall("sec")
        if subs:
            for sec in subs:
                t = _text(sec.find("title"))
                body = _direct_paragraphs(sec)
                parts.append(f"{t}: {body}" if t else body)
        else:
            parts.append(_direct_paragraphs(ab) or _text(ab))
    return "\n\n".join(p for p in parts if p)


def _references(root: ET.Element) -> List[str]:
    refs = []
    for ref in root.findall(".//back//ref-list//ref"):
        cite = (ref.find("mixed-citation") or ref.find("element-citation")
                or ref.find("citation") or ref)
        t = _text(cite)
        if t:
            refs.append(t)
    return refs


def _supplementary(article: ET.Element, pmcid: str) -> List[Dict]:
    """抽 <supplementary-material>：label / caption / url（PMC 附件走 .../bin/<文件名>）。"""
    num = pmcid.upper().replace("PMC", "").strip()
    out = []
    for sm in article.findall(".//supplementary-material"):
        href = _attr_href(sm) or _attr_href(sm.find(".//media")) if sm.find(".//media") is not None else _attr_href(sm)
        url = href
        if href and not href.lower().startswith("http"):
            url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{num}/bin/{href}"
        out.append({"label": _text(sm.find("label")),
                    "caption": _text(sm.find("caption")),
                    "url": url})
    return out


def _license(article: ET.Element) -> Tuple[str, str]:
    """从 <permissions><license> 抽许可文本与 URL。"""
    perm = article.find(".//front//permissions")
    lic = perm.find("license") if perm is not None else None
    if lic is None:
        return "", ""
    url = _attr_href(lic)
    ref = lic.find(".//license_ref") or lic.find(".//ext-link") or lic.find(".//uri")
    if ref is not None:
        url = url or _attr_href(ref) or _text(ref)
    return _text(lic), url


def _reuse_class(license_text: str, license_url: str) -> str:
    """按许可判定复用级别：commercial-safe / non-commercial-only / no-derivatives / unknown。"""
    s = f"{license_text} {license_url}".lower()
    if not s.strip():
        return "unknown"
    if "publicdomain" in s or "cc0" in s or "public domain" in s:
        return "commercial-safe"
    cc = "creativecommons" in s or "creative commons" in s
    nd = "-nd" in s or "noderiv" in s or "no deriv" in s
    nc = "-nc" in s or "noncommercial" in s or "non-commercial" in s
    if cc and nd:
        return "no-derivatives"
    if cc and nc:
        return "non-commercial-only"
    if cc:
        return "commercial-safe"
    return "unknown"


def _pub_flags_from_jats(article: ET.Element, title: str) -> Tuple[List[str], List[str]]:
    """从 JATS 的 related-article 与标题判断 撤稿/勘误/关注声明。"""
    flags, notes = set(), []
    for ra in article.findall(".//related-article"):
        rt = (ra.get("related-article-type") or "").lower()
        if "retract" in rt:
            flags.add("retracted"); notes.append(_text(ra) or rt)
        elif "correct" in rt or "erratum" in rt:
            flags.add("erratum")
        elif "concern" in rt:
            flags.add("expression_of_concern")
    tl = (title or "").lower()
    if tl.startswith("retraction") or "retracted:" in tl or "withdrawn:" in tl:
        flags.add("retracted")
    if tl.startswith("erratum") or tl.startswith("correction") or tl.startswith("corrigendum"):
        flags.add("erratum")
    if "expression of concern" in tl:
        flags.add("expression_of_concern")
    return sorted(flags), notes


def parse_jats(xml_bytes: bytes) -> Dict:
    """解析 JATS 全文，返回 dict：
    title / sections / references / supplementary / license / license_url / pub_flags / pub_flag_notes
    """
    root = ET.fromstring(xml_bytes)
    _strip_ns(root)
    article = root.find(".//article") or root          # efetch 外层是 <pmc-articleset>

    title = _text(article.find(".//front//article-title"))
    # PMCID（用于拼附件 URL）
    pmcid = ""
    for aid in article.findall(".//article-id"):
        if (aid.get("pub-id-type") or "") == "pmc":
            pmcid = aid.text or ""
    sections: Dict[str, str] = {}

    ab = _abstract_text(article)
    if ab:
        sections["abstract"] = ab

    idx = 1
    body = article.find("body")
    if body is not None:
        lead = "\n\n".join(_text(p) for p in body.findall("p") if _text(p))
        if lead:
            _add_section(sections, "", lead, idx); idx += 1
        for sec in body.findall("sec"):
            _add_section(sections, _text(sec.find("title")), _sec_to_text(sec), idx); idx += 1

    back = article.find("back")
    if back is not None:
        for sec in back.findall(".//sec"):
            _add_section(sections, _text(sec.find("title")), _sec_to_text(sec), idx); idx += 1
        for note in back.findall(".//notes"):
            ntext = "\n\n".join(_text(p) for p in note.findall(".//p") if _text(p))
            if ntext:
                _add_section(sections, _text(note.find("title")) or "Notes", ntext, idx); idx += 1
        ack = "\n\n".join(_text(a) for a in back.findall(".//ack"))
        if ack:
            _add_section(sections, "Acknowledgements", ack, idx); idx += 1

    lic, lic_url = _license(article)
    flags, notes = _pub_flags_from_jats(article, title)
    return {"title": title, "sections": sections, "references": _references(article),
            "supplementary": _supplementary(article, pmcid),
            "license": lic, "license_url": lic_url,
            "pub_flags": flags, "pub_flag_notes": notes}


# ── 组装成长型文档 ─────────────────────────────────────────────────────────────

def build_doc(pmcid: str, parsed: Dict, base: Optional[Dict] = None,
              provenance: Optional[Dict] = None) -> Dict:
    """把解析结果(parsed dict) + provenance 组装成成长型文档。

    新增字段：provenance / supplementary_materials / publication_flags / is_retracted /
    retraction_notes / quality。status 仍只表流水线阶段(fulltext)，撤稿不写进 status。
    """
    doc = dict(base) if base else {"pmcid": pmcid}
    merged = dict(doc.get("sections") or {})
    for k, v in parsed.get("sections", {}).items():       # step1 abstract 更干净，优先保留
        if k == "abstract" and merged.get("abstract"):
            continue
        merged[k] = v
    doc["sections"] = merged
    doc["references"] = parsed.get("references", [])
    doc["supplementary_materials"] = parsed.get("supplementary", [])
    if parsed.get("title") and not doc.get("title"):
        doc["title"] = parsed["title"]

    # 撤稿/勘误：JATS 信号 ∪ step1 pub_types（属性 flags，不污染 status）
    flags = set(parsed.get("pub_flags") or [])
    for t in (base or {}).get("pub_types", []) or []:
        tl = t.lower()
        if "retract" in tl:
            flags.add("retracted")
        elif "erratum" in tl or "corrigendum" in tl or "correction" in tl:
            flags.add("erratum")
        elif "expression of concern" in tl:
            flags.add("expression_of_concern")
    doc["publication_flags"] = sorted(flags)
    doc["is_retracted"] = "retracted" in flags
    doc["retraction_notes"] = parsed.get("pub_flag_notes", [])

    if provenance:
        doc["provenance"] = provenance
        doc["fulltext_source"] = provenance.get("access_source")   # 兼容旧字段
    doc["status"] = "fulltext"
    doc["quality"] = quality_block(doc)
    return doc


def extract_fulltext_verbose(pmcid: str, base: Optional[Dict] = None,
                             api_key: str = "", allow_html: bool = True
                             ) -> Tuple[Optional[Dict], str]:
    """抓+解析+组装，返回 (doc, "") 成功 / (None, reason) 失败。

    两级：① PMC OA 的 JATS XML（efetch）；② 若 XML 无正文，退回解析 PMC 网页 HTML
    （allow_html=False 可关掉兜底）。成功的文档带 fulltext_source = pmc_xml / pmc_html。
    reason 见 fetch_fulltext_xml / fetch_pmc_html，外加 parse_error:<异常名>。
    """
    num = pmcid.upper().replace("PMC", "").strip()
    pmc_url = PMC_HTML_URL.format(num=num)

    # ① PMC OA 结构化 XML
    xml, reason = fetch_fulltext_xml(pmcid, api_key)
    if xml is not None:
        try:
            parsed = parse_jats(xml)
            prov = {"access_source": "pmc_xml", "source_endpoint": "ncbi_efetch_pmc",
                    "fulltext_url": f"{EFETCH}?db=pmc&id={num}&rettype=xml",
                    "pmc_article_url": pmc_url, "accessed_at": _now(),
                    "license": parsed["license"], "license_url": parsed["license_url"],
                    "reuse_class": _reuse_class(parsed["license"], parsed["license_url"])}
            doc = build_doc(pmcid.upper(), parsed, base, prov)
            if doc["quality"]["quality_status"] != "reject":
                return doc, ""
        except Exception as e:
            reason = f"parse_error:{type(e).__name__}"

    # ② 退回 PMC 网页 HTML（救回有 PMCID、网页有全文、但不在 OA-XML 子集的文章）
    if allow_html:
        html, hreason = fetch_pmc_html(pmcid)
        if html is not None:
            try:
                parsed = parse_pmc_html(html)
                prov = {"access_source": "pmc_html", "source_endpoint": "ncbi_pmc_html",
                        "fulltext_url": pmc_url, "pmc_article_url": pmc_url, "accessed_at": _now(),
                        "license": parsed["license"], "license_url": parsed["license_url"],
                        "reuse_class": _reuse_class(parsed["license"], parsed["license_url"])}
                doc = build_doc(pmcid.upper(), parsed, base, prov)
                if doc["quality"]["quality_status"] != "reject":
                    return doc, ""
                return None, "quality_reject:" + ",".join(doc["quality"]["issues"])
            except Exception as e:
                return None, f"html_parse_error:{type(e).__name__}"
        else:
            reason = reason if xml is not None else hreason
    return None, reason


def extract_fulltext(pmcid: str, base: Optional[Dict] = None,
                     api_key: str = "") -> Optional[Dict]:
    """高层入口：抓 PMC 全文 → 解析 → 组装成长型文档，返回 dict（无开放全文则返回 None）。

    纯逻辑、不写文件，供其它步骤/脚本直接 import 复用。
    base：可选的 step1 元数据 dict，作为成长型文档底座（不传则只用全文构建最小文档）。
    """
    doc, _ = extract_fulltext_verbose(pmcid, base, api_key)
    return doc


# 「正常文章结构」识别：正文章节里至少出现一个 IMRaD 类大块才算像研究论文（含中文标题）
_IMRAD_RE = re.compile(
    r"(introduction|background|method|material|patient|result|finding|"
    r"discussion|conclusion|case report|"
    r"引言|前言|背景|方法|材料|病例|患者|对象|资料|结果|讨论|结论|小结)", re.I)

# back-matter（边角声明）类标题：这些不算"正文叙事"。全是这类 → 没真正抓到文章主体。
_BACKMATTER_RE = re.compile(
    r"(author.{0,3}contribution|data availability|code availability|conflict|"
    r"competing interest|funding|acknowledg|ethic|informed consent|"
    r"institutional review|publisher.{0,3}s note|supplementary|supporting information|"
    r"peer review|declaration|abbreviation|^notes$|disclosure|consent)", re.I)


def quality_block(doc, fetch_status: str = "fetched") -> Dict:
    """把 check_extraction 包装成分层质量块（写进每篇 JSON）：
      fetch_status   fetched / failed（由调用方给；能 build_doc 说明已抓到）
      parse_status   parsed（有真正正文）/ partial（仅摘要/边角）/ failed（格式坏）
      quality_status pass（无硬伤无软提醒）/ weak（无硬伤有软提醒）/ reject（有硬伤）
    """
    c = check_extraction(doc)
    if not c["format_valid"]:
        parse_status = "failed"
    elif c["issues"] and any(i in c["issues"] for i in
                             ("no_body_sections", "empty_body", "only_backmatter")):
        parse_status = "partial"
    else:
        parse_status = "parsed"
    quality_status = "reject" if c["issues"] else ("weak" if c["warnings"] else "pass")
    return {"fetch_status": fetch_status, "parse_status": parse_status,
            "quality_status": quality_status, "body_chars": c["body_chars"],
            "n_sections": c["n_sections"], "n_body_sections": c["n_body_sections"],
            "n_references": c["n_references"], "has_imrad": c["has_imrad"],
            "issues": c["issues"], "warnings": c["warnings"]}


def check_extraction(doc, min_body_chars: int = 500) -> Dict:
    """检测一篇 paper 文档是否真正提取到全文，并校验它像不像一篇正常文章。

    doc 可传 dict 或 paper JSON 文件路径。返回字典含：
      ok               无硬伤（issues 为空）即 True
      format_valid     是 dict 且有非空 sections 容器
      n_sections / n_body_sections / body_chars / n_references / has_abstract / has_imrad
      issues[]         硬伤：bad_format / status_not_fulltext / no_body_sections /
                       empty_body（章节在但内容全空）/ body_too_short
      warnings[]       软提醒：no_abstract / no_references / single_section / no_imrad_structure

    硬伤 = 格式在但没内容、或结构不成文章；软提醒 = 可疑但未必错。
    """
    if isinstance(doc, str):
        try:
            with open(doc, encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {"ok": False, "format_valid": False, "issues": [f"bad_format:{type(e).__name__}"],
                    "warnings": [], "n_sections": 0, "n_body_sections": 0,
                    "body_chars": 0, "n_references": 0, "has_abstract": False, "has_imrad": False}

    issues: List[str] = []
    warnings: List[str] = []

    sections = doc.get("sections") if isinstance(doc, dict) else None
    format_valid = isinstance(doc, dict) and isinstance(sections, dict) and len(sections) > 0
    if not format_valid:
        return {"ok": False, "format_valid": False, "issues": ["bad_format"], "warnings": [],
                "n_sections": 0, "n_body_sections": 0, "body_chars": 0,
                "n_references": 0, "has_abstract": False, "has_imrad": False}

    body = {k: v for k, v in sections.items() if k != "abstract" and isinstance(v, str)}
    body_chars = sum(len(v.strip()) for v in body.values())
    has_abstract = bool((sections.get("abstract") or "").strip())
    has_imrad = any(_IMRAD_RE.search(k) for k in body)
    n_refs = len(doc.get("references") or [])
    # 真正的"正文叙事"章节：非 back-matter 且内容非空
    real_body = {k: v for k, v in body.items()
                 if v.strip() and not _BACKMATTER_RE.search(k)}

    # 硬伤：格式在但没内容 / 不成文章结构
    if doc.get("status") != "fulltext":
        issues.append("status_not_fulltext")
    if not body:
        issues.append("no_body_sections")
    elif body_chars == 0:
        issues.append("empty_body")                       # 章节 key 在，但正文全是空字符串
    elif not real_body:
        issues.append("only_backmatter")                  # 只有作者贡献/声明等边角，没抓到正文主体
    elif body_chars < min_body_chars:
        issues.append(f"body_too_short(<{min_body_chars})")

    # 软提醒：可疑但不一定是错
    if not has_abstract:
        warnings.append("no_abstract")
    # References may come as a parsed list OR as a rendered "References" text section
    # (HTML extraction path). Only warn when neither is present.
    has_ref_section = any(re.search(r"reference|bibliograph", k, re.I) for k in sections)
    if n_refs == 0 and not has_ref_section:
        warnings.append("no_references")
    if len(body) == 1:
        warnings.append("single_section")
    if body and not has_imrad:
        warnings.append("no_imrad_structure")             # 没有 引言/方法/结果/讨论 类大块

    return {
        "ok": not issues,
        "format_valid": True,
        "n_sections": len(sections),
        "n_body_sections": len(body),
        "body_chars": body_chars,
        "n_references": n_refs,
        "has_abstract": has_abstract,
        "has_imrad": has_imrad,
        "issues": issues,
        "warnings": warnings,
    }


def _load_base(base_json: str, pmcid: str) -> Optional[Dict]:
    with open(base_json, encoding="utf-8") as f:
        docs = json.load(f)
    pmcid = pmcid.upper()
    for d in docs:
        if (d.get("pmcid") or "").upper() == pmcid:
            return d
    print(f"  注意：{base_json} 里没找到 {pmcid}，将只用全文构建文档。")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="抓 PMC 开放获取全文 → 结构化 JSON（每个大标题一个 key）")
    ap.add_argument("pmcid", help="PMC 号，如 PMC13072727")
    ap.add_argument("--base", default=None,
                    help="包含文章元数据的 JSON，按 pmcid 取元数据作底座")
    ap.add_argument("--out", default=None, help="输出 JSON（默认 paper/<pmcid>.fulltext.json）")
    args = ap.parse_args()

    load_env()
    api_key = os.environ.get("NCBI_API_KEY", "")
    print(f"抓取全文：{args.pmcid}")
    base = _load_base(args.base, args.pmcid) if args.base else None
    doc, reason = extract_fulltext_verbose(args.pmcid, base, api_key)
    if doc is None:
        print(f"  失败：{reason}")
        sys.exit(1)

    out = args.out or os.path.join("paper", f"{args.pmcid.upper()}.fulltext.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    q = doc["quality"]; prov = doc.get("provenance", {})
    print(f"\n完成 → {out}")
    print(f"  标题: {doc.get('title', '(无)')[:75]}")
    print(f"  来源: {prov.get('access_source')} | 许可: {prov.get('license') or '(未知)'} → {prov.get('reuse_class')}")
    print(f"  质量: {q['quality_status']} (parse={q['parse_status']}) | 正文 {q['body_chars']:,} 字符 / {q['n_body_sections']} 节")
    print(f"  参考文献 {q['n_references']} | 补充材料 {len(doc.get('supplementary_materials', []))} | 撤稿 {doc.get('is_retracted')}")
    if q["issues"] or q["warnings"]:
        print(f"  issues={q['issues']} warnings={q['warnings']}")


if __name__ == "__main__":
    main()
