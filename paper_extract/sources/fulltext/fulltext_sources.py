"""
全文获取 —— 单一深接口 + 源适配器注册表。

把「给我这篇文章的结构化全文」收成一个 seam：调用方只跨这一个接口，内部自己决定
走哪条源、按什么优先级兜底、抓取/解析/组装/质检。所有源共享一份 http_get（按 host
自动限速）与一份路由逻辑——消灭散落在各脚本里的 http_get / 限速常量 / DOI 路由重复。

    from paper_extract.sources.fulltext.fulltext_sources import get_fulltext
    doc, reason = get_fulltext(paper)          # paper 带 doi/pmid/pmcid(+step1 元数据作底座)
    #   成功 → (成长型文档 dict, "")；拿不到 → (None, 失败原因)

接口（调用方/测试唯一要知道的）：
    get_fulltext(paper: dict, sources: list[str] | None = None) -> (doc | None, reason)
      - paper：含 doi/pmid/pmcid 的字典；其余字段作 build_doc 底座（abstract/元数据沿用）。
      - sources：限定只试哪些源（默认全优先级链）。
      - 内部：缺 PMCID 时先 EPMC 反查；按 PRIORITY 逐源 fetch+parse+build+质检；
              第一篇质检非 reject 即返回。全程容错，单源失败不影响下一源。

新增源只需写一个 adapter(paper)->(parsed|None, tag, url) 并登记进 ADAPTERS / PRIORITY，
parse/质检/落盘都自动复用，调用方与批量驱动零改动。
"""

import contextlib as _contextlib
import os
import ssl as _ssl
import time
import json
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Protocol, Tuple

from .fulltext_fetcher import (
    load_env, build_doc, resolve_pmcid, parse_jats, parse_pmc_html, parse_article_html,
    _reuse_class, _now, _strip_ns, _text, _pub_flags_from_jats,
    EFETCH, PMC_HTML_URL,
)

load_env()
NCBI_KEY = os.environ.get("NCBI_API_KEY", "")
BROWSER_UA = "Mozilla/5.0 (compatible; paper-extract/fulltext)"
# Unpaywall/NCBI politeness email — configurable, neutral default (no personal data shipped).
EMAIL = os.environ.get("PAPER_EXTRACT_EMAIL") or os.environ.get("UNPAYWALL_EMAIL") or "paper-extract@example.com"
SPRINGER_PREFIXES = ("10.1007", "10.1038", "10.1186")
WILEY_PREFIXES = ("10.1002", "10.1111", "10.1046", "10.1113")  # Wiley/Blackwell 主前缀


# ── HTTP port ──────────────────────────────────────────────────────────────
# The transport is injectable at one seam. Production uses UrllibClient (real
# urllib, per-host throttle + backoff, SSL-ignoring browser fetch); tests inject
# a fake adapter, so the whole fetch success path is exercisable offline. All
# rate-limit and SSL state lives inside the adapter — nothing leaks to module
# scope. get_fulltext()/download_pdf() accept a `client=` to swap the seam.


class HttpClient(Protocol):
    """The transport seam. Two adapters satisfy it: UrllibClient (prod) and a
    test fake. Every method returns rather than raising — network failure is a
    value (code=0 / None), never an exception."""

    def get(self, url: str, headers: Optional[Dict] = None, timeout: int = 60,
            retries: int = 3) -> Tuple[int, bytes, str]: ...

    def browser_get(self, url: str, referer: Optional[str] = None,
                    timeout: int = 40) -> Optional[bytes]: ...

    def browser_get_url(self, url: str, referer: Optional[str] = None,
                        timeout: int = 40) -> Tuple[Optional[str], Optional[bytes]]: ...


class UrllibClient:
    """Production HttpClient. Owns all rate-limit + SSL state (per-host throttle
    table, last-call clock, unverified-SSL context, browser headers)."""

    _RATE = {
        "eutils.ncbi.nlm.nih.gov": 0.15 if NCBI_KEY else 0.34,
        "www.ncbi.nlm.nih.gov":    0.34,
        "www.ebi.ac.uk":           0.5,
        "api.springernature.com":  0.7,
        "api.elsevier.com":        1.0,
        "api.biorxiv.org":         1.0,
        "www.biorxiv.org":         1.0,
        "www.medrxiv.org":         1.0,
        "api.crossref.org":        1.0,
        "api.unpaywall.org":       0.12,
        "api.core.ac.uk":          6.0,
    }
    _DEFAULT_RATE = 0.5
    _NOVERIFY = _ssl._create_unverified_context()
    _BROWSER_HDR = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self) -> None:
        self._last_call: Dict[str, float] = {}

    def _throttle(self, netloc: str) -> None:
        iv = self._RATE.get(netloc, self._DEFAULT_RATE)
        wait = iv - (time.monotonic() - self._last_call.get(netloc, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_call[netloc] = time.monotonic()

    def get(self, url: str, headers: Optional[Dict] = None, timeout: int = 60,
            retries: int = 3) -> Tuple[int, bytes, str]:
        netloc = urllib.parse.urlsplit(url).netloc
        delay = 2.0
        for attempt in range(retries):
            self._throttle(netloc)
            try:
                req = urllib.request.Request(url, headers=headers or {"User-Agent": BROWSER_UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.getcode(), resp.read(), ""
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                return e.code, b"", f"HTTP {e.code}"
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                return 0, b"", type(e).__name__
        return 0, b"", "max_retries"

    def browser_get(self, url: str, referer: Optional[str] = None,
                    timeout: int = 40) -> Optional[bytes]:
        h = dict(self._BROWSER_HDR)
        h["Referer"] = referer or url
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                        timeout=timeout, context=self._NOVERIFY) as r:
                return r.read()
        except Exception:
            return None

    def browser_get_url(self, url: str, referer: Optional[str] = None,
                        timeout: int = 40) -> Tuple[Optional[str], Optional[bytes]]:
        h = dict(self._BROWSER_HDR)
        h["Referer"] = referer or url
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                        timeout=timeout, context=self._NOVERIFY) as r:
                return r.geturl(), r.read()
        except Exception:
            return None, None


# Active client (swapped for the duration of a get_fulltext/download_pdf call
# when a `client=` is passed). Module-level thin wrappers keep the ~15 existing
# adapter call sites unchanged.
_client: HttpClient = UrllibClient()


def http_get(url: str, headers: Optional[Dict] = None, timeout: int = 60,
             retries: int = 3) -> Tuple[int, bytes, str]:
    """GET，永不抛异常。返回 (code, body, err)；code=0 表示网络层失败。
    自动按 host 限速、对 429/5xx 退避重试。委托给当前 HttpClient。"""
    return _client.get(url, headers, timeout, retries)


@_contextlib.contextmanager
def _using_client(client: Optional[HttpClient]):
    """Temporarily install `client` as the active transport for a top-level
    call, restoring the previous one afterwards. No-op when client is None."""
    global _client
    if client is None:
        yield
        return
    prev = _client
    _client = client
    try:
        yield
    finally:
        _client = prev


# ── Elsevier 解析器（非 JATS，ce: 命名空间；返回 parse_jats 同结构 dict）─────────
def _els_sec_text(sec: ET.Element, depth: int = 0) -> str:
    parts: List[str] = []
    for ch in list(sec):
        tag = ch.tag
        if tag in ("section-title", "label"):
            continue
        if tag == "para":
            t = _text(ch)
            if t:
                parts.append(t)
        elif tag == "list":
            items = [f"- {_text(it)}" for it in ch.findall("list-item") if _text(it)]
            if items:
                parts.append("\n".join(items))
        elif tag == "section":
            sub = _text(ch.find("section-title"))
            body = _els_sec_text(ch, depth + 1)
            if sub and body:
                parts.append(f"{'#' * (depth + 2)} {sub}\n{body}")
            elif body:
                parts.append(body)
        elif tag in ("table", "figure", "float-anchor", "display", "textbox"):
            cap = _text(ch.find(".//caption"))
            if cap:
                parts.append(f"[{tag}] {cap}")
            if tag == "table":
                cells = " ".join(_text(e) for e in ch.findall(".//entry") if _text(e))
                if cells:
                    parts.append(cells)
    return "\n\n".join(parts)


def parse_elsevier(xml_bytes: bytes) -> Dict:
    root = ET.fromstring(xml_bytes)
    _strip_ns(root)
    cd = root.find(".//coredata")
    title = _text(cd.find("title")) if cd is not None else ""
    abstract = _text(cd.find("description")) if cd is not None else ""
    if not abstract:
        for a in root.findall(".//abstract"):
            if (a.get("class") or "") == "author":
                abstract = _text(a); break

    sections: Dict[str, str] = {}
    if abstract:
        sections["abstract"] = abstract
    cont = root.find(".//originalText//sections") or root.find(".//sections")
    idx = 1
    if cont is not None:
        for sec in cont.findall("section"):
            st = _text(sec.find("section-title")) or f"Section {idx}"
            body = _els_sec_text(sec)
            if body:
                sections[st] = (sections[st] + "\n\n" + body) if st in sections else body
            idx += 1

    refs = [t for ref in root.findall(".//bib-reference") if (t := _text(ref))]
    lic_url = _text(cd.find("openaccessUserLicense")) if cd is not None else ""
    lic = "Creative Commons" if "creativecommons" in lic_url.lower() else ""
    flags, notes = _pub_flags_from_jats(ET.Element("x"), title)
    return {"title": title, "sections": sections, "references": refs,
            "supplementary": [], "license": lic, "license_url": lic_url,
            "pub_flags": flags, "pub_flag_notes": notes}


# ── 源适配器：adapter(paper) -> (parsed|None, tag_or_reason, fulltext_url) ──────
# parsed 是 parse_jats/parse_elsevier/parse_pmc_html 同结构的 dict。tag = access_source。
def _a_pmc_xml(paper: Dict):
    num = (paper.get("pmcid") or "").upper().replace("PMC", "")
    url = f"{EFETCH}?db=pmc&id={num}&rettype=xml&retmode=xml"
    if NCBI_KEY:
        url += f"&api_key={NCBI_KEY}"
    code, body, err = http_get(url)
    if code != 200 or not body:
        return None, f"http_{code or err}", ""
    if b"<body" not in body and b"<error" in body:     # 不在机器可下载 OA-XML 子集
        return None, "not_oa_subset", ""
    if b"<article" not in body:
        return None, "not_jats", ""
    return parse_jats(body), "pmc_xml", f"{EFETCH}?db=pmc&id={num}&rettype=xml"


def _a_pmc_html(paper: Dict):
    num = (paper.get("pmcid") or "").upper().replace("PMC", "")
    url = PMC_HTML_URL.format(num=num)
    code, body, err = http_get(url, {"User-Agent": BROWSER_UA})
    if code != 200 or not body:
        return None, f"html_{code or err}", ""
    return parse_pmc_html(body.decode("utf-8", "ignore")), "pmc_html", url


def _a_epmc_xml(paper: Dict):
    num = (paper.get("pmcid") or "").upper().replace("PMC", "")
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/PMC{num}/fullTextXML"
    code, body, err = http_get(url)
    if code != 200 or b"<article" not in body:
        return None, f"epmc_{code or err}", ""
    return parse_jats(body), "epmc_xml", url


def _a_springer(paper: Dict):
    key = os.environ.get("SPRINGER_OA_API_KEY", "")
    if not key:
        return None, "no_key", ""
    url = ("https://api.springernature.com/openaccess/jats?"
           + urllib.parse.urlencode({"q": f"doi:{paper['doi']}", "api_key": key}))
    code, body, err = http_get(url)
    if code in (401, 403):
        return None, f"auth_{code}", ""
    if code != 200 or b"<article" not in body:
        return None, f"springer_{code or err}", ""
    return parse_jats(body), "springer_oa", url


def _a_elsevier(paper: Dict):
    key = os.environ.get("ELSEVIER_API_KEY", "")
    if not key:
        return None, "no_key", ""
    url = f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(paper['doi'])}"
    code, body, err = http_get(url, {"X-ELS-APIKey": key, "Accept": "text/xml",
                                     "User-Agent": BROWSER_UA})
    if code in (401, 403):
        return None, f"denied_{code}", ""
    if code != 200:
        return None, f"elsevier_{code or err}", ""
    if body.count(b"<ce:para") < 3:                 # 只有 meta、无正文（非授权/闭源）
        return None, "meta_only_no_body", ""
    return parse_elsevier(body), "elsevier_oa", url


def _a_biorxiv(paper: Dict):
    doi = paper["doi"]
    for server in ("biorxiv", "medrxiv"):
        code, body, err = http_get(f"https://api.biorxiv.org/details/{server}/{doi}/na/json")
        if code != 200:
            continue
        try:
            coll = json.loads(body).get("collection") or []
        except Exception:
            continue
        jats = [c.get("jatsxml") for c in coll if c.get("jatsxml")]
        if jats:
            c2, xbody, e2 = http_get(jats[-1])      # 最新版本
            if c2 == 200 and b"<article" in xbody:
                return parse_jats(xbody), f"{server}_jats", jats[-1]
            return None, f"jats_fetch_{c2 or e2}", ""
    return None, "no_biorxiv_version", ""


def _a_core(paper: Dict):
    """CORE.ac.uk v3：按 DOI 取已抽好的纯文本正文（flat text，非分节）。本项目命中率低
    （语料偏新）+ 限流凶，不进默认优先级；保留供别处/别的语料复用：sources=["core"]。"""
    key = os.environ.get("CORE_API_KEY", "")
    if not key:
        return None, "no_key", ""
    url = "https://api.core.ac.uk/v3/search/works?" + urllib.parse.urlencode(
        {"q": f'doi:"{paper["doi"]}"', "limit": 1})
    code, body, err = http_get(url, {"Authorization": f"Bearer {key}"})
    if code in (401, 403, 429):
        return None, f"ratelimit_or_auth_{code}", ""
    if code != 200:
        return None, f"core_{code or err}", ""
    try:
        res = (json.loads(body).get("results") or [])
    except Exception:
        return None, "core_badjson", ""
    if not res or not res[0].get("fullText"):
        return None, "core_no_fulltext", ""
    # flat text → 单一 body section（无标题结构，CORE 不给分节）
    parsed = {"title": res[0].get("title", ""), "sections": {"fulltext": res[0]["fullText"]},
              "references": [], "supplementary": [], "license": "", "license_url": "",
              "pub_flags": [], "pub_flag_notes": []}
    return parsed, "core_fulltext", res[0].get("downloadUrl") or url


# ── PDF 路线（兜底，最低优先级）：Docling 主力 + PyMuPDF/OCR 备份 ──────────────────
# 重依赖(docling/fitz/pytesseract)惰性导入——不装也不影响 PMC/XML 路径。
# 输入：paper 里的 pdf_url / land_url（Unpaywall 探测回填，见 check_test oa）；有 pmcid
# 则优先用 EuropePMC 渲染链（仓库托管、不反爬）。PDF 流式取字节，不落盘。
_DOCLING_CONV = None


def _docling_converter():
    global _DOCLING_CONV
    if _DOCLING_CONV is None:
        from docling.document_converter import DocumentConverter
        _DOCLING_CONV = DocumentConverter()
    return _DOCLING_CONV


def _md_to_sections(md: str) -> Dict[str, str]:
    """Docling markdown → sections dict：按 # 标题切块，Abstract 归到 abstract。"""
    import re
    sections: Dict[str, str] = {}
    cur, buf = "", []
    def flush():
        t = "\n".join(buf).strip()
        if t and cur:
            key = "abstract" if cur.strip().lower() == "abstract" else cur
            sections[key] = (sections[key] + "\n\n" + t) if key in sections else t
    for line in md.splitlines():
        m = re.match(r"^#{1,6}\s+(.+)", line)
        if m:
            flush(); cur = m.group(1).strip(); buf = []
        else:
            buf.append(line)
    flush()
    return sections


# Docling 从 PDF 版面推断标题时会混入页面furniture/广告，这些不是真章节，过滤掉。
import re as _re
_PDF_NOISE_RE = _re.compile(
    r"(open access|ready to submit|choose bmc|submit your manuscript|springer nature|"
    r"publisher'?s note|^\s*received\b|accepted:|author details|running (title|head)|"
    r"^\s*correspond|author for correspondence|key\s*words?\b|author manuscript|"
    r"^\s*title\s*$|study protocol|research article|original article|review article|"
    r"this article is|terms of use|creative commons|©|copyright)", _re.I)


# 常见正文章节词（含这些的标题不当噪声删，避免误杀如"Data and materials"）
_SECTION_KW = _re.compile(
    r"(introduc|background|method|material|result|discuss|conclu|abstract|"
    r"data|analy|patient|case|review|aim|object|limitation|availab|"
    r"reference|acknowled|funding|ethic|consent|contribution|declarat|competing|"
    r"supplement|appendix|highlight|引言|方法|结果|讨论|结论|材料)", _re.I)


def _looks_like_author_line(k: str) -> bool:
    """'Chiara Piana, Meindert Danhof & Oscar...' / 期刊名：含 & 连接 + 人名式大写、无章节词。
    只认 & / &amp;（真章节标题极少含 &，作者/期刊名几乎必有），不认 'and' 以免误杀。"""
    if _SECTION_KW.search(k):
        return False
    return bool(_re.search(r"&amp;|&", k) and _re.search(r"[A-Z][a-z]+\s+[A-Z]", k))


def clean_pdf_sections(secs: Dict[str, str]) -> Dict[str, str]:
    """丢掉 Docling 误判为标题的噪声块：期刊名/广告/版权/日期/关键词/通讯/作者行/超短残留。
    保护：① 含正文章节词的标题一律保留；② 超长标题但带大量正文的保留(那是被并进来的内容，删了会丢正文)。"""
    out: Dict[str, str] = {}
    for k, v in secs.items():
        if k == "abstract":
            out[k] = v; continue
        if _SECTION_KW.search(k):            # 真章节 → 保留
            out[k] = v; continue
        body = (v or "").strip()
        if not body:                         # 空节
            continue
        if _PDF_NOISE_RE.search(k):          # 期刊标签/广告/版权/关键词/通讯地址
            continue
        if _looks_like_author_line(k):       # 作者行(含 &)
            continue
        if len(body) < 60:                   # 极短残留(期刊名/孤立标签)
            continue
        if len(k) > 120 and len(body) < 200:  # 超长标题且无实质内容=误判的标题/句子
            continue
        out[k] = v
    return out


def parse_pdf_docling(pdf_bytes: bytes) -> Dict:
    import io
    from docling.datamodel.base_models import DocumentStream
    res = _docling_converter().convert(DocumentStream(name="a.pdf", stream=io.BytesIO(pdf_bytes)))
    secs = clean_pdf_sections(_md_to_sections(res.document.export_to_markdown()))
    return {"title": "", "sections": secs,
            "references": [], "supplementary": [], "license": "", "license_url": "",
            "pub_flags": [], "pub_flag_notes": []}


# 扁平正文(pymupdf/ocr 抽出的整块文字)里的"独立标题行"识别：行本身就是章节名
# (可带序号/尾冒号)才算，避免把句子里出现的 "methods" 误判成标题。中英+葡文常见词。
_FLAT_HEAD_RE = _re.compile(
    r"^(?:\d{1,2}\s*[.)]\s*)?"
    r"(abstract|summary|introduction|background|"
    r"materials?\s+and\s+methods|(?:patients?|subjects?)\s+and\s+methods?|"
    r"methodology|methods?|materials?|"
    r"results?\s+and\s+discussion|results?|discussions?|"
    r"conclusions?|limitations?|references?|acknowledge?ments?|"
    r"funding|author\s+contributions?|conflicts?\s+of\s+interest|"
    r"resumo|introdu\w+|m[ée]todos?|resultados?|discuss[ãa]o|conclus[ãa]o|refer[êe]ncias)"
    r"\s*:?$", _re.I)


def split_flat_sections(text: str) -> Dict[str, str]:
    """把一整块扁平正文按"独立标题行"切成 sections（Introduction/Methods/Results/…）。
    切不出 ≥2 个块就返回空 dict（调用方保留单块 fulltext）。通用兜底：无 Docling 时给 PDF 结构。"""
    sections: Dict[str, str] = {}
    cur: Optional[str] = None
    buf: List[str] = []

    def flush():
        if cur and buf:
            body = "\n".join(buf).strip()
            if body:
                key = "abstract" if cur.lower() in ("abstract", "summary", "resumo") else cur
                sections[key] = (sections[key] + "\n\n" + body) if key in sections else body

    for raw in text.splitlines():
        s = raw.strip()
        m = _FLAT_HEAD_RE.match(s) if (s and len(s) <= 40) else None
        if m:
            flush()
            cur = _re.sub(r"\s+", " ", m.group(1)).strip().title()
            buf = []
        elif cur is not None:
            buf.append(raw)
    flush()
    # 至少要有一个"正文叙事"标题(非纯 abstract/references)才算切分成功
    real = [k for k in sections if k.lower() not in ("abstract", "references", "reference")]
    return sections if len(sections) >= 2 and real else {}


def parse_pdf_pymupdf(pdf_bytes: bytes) -> Dict:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc); doc.close()
    text = text.strip()
    secs = split_flat_sections(text) or {"fulltext": text}   # 能按标题分就分，否则单块
    return {"title": "", "sections": secs, "references": [],
            "supplementary": [], "license": "", "license_url": "", "pub_flags": [], "pub_flag_notes": []}


def parse_pdf_ocr(pdf_bytes: bytes) -> Dict:
    import io, fitz, pytesseract
    from PIL import Image
    doc = fitz.open(stream=pdf_bytes, filetype="pdf"); out = []
    for p in doc:
        pix = p.get_pixmap(dpi=150)
        out.append(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png")))))
    doc.close()
    text = "\n".join(out).strip()
    secs = split_flat_sections(text) or {"fulltext": text}
    return {"title": "", "sections": secs, "references": [],
            "supplementary": [], "license": "", "license_url": "", "pub_flags": [], "pub_flag_notes": []}


# ── 多镜像下载（绕 WAF）：浏览器伪装 + 忽略 SSL + Referer；遍历 Unpaywall 全部 oa_locations ──
def _referer_for(url: str) -> str:
    for host, ref in (("mdpi.com", "https://www.mdpi.com/"),
                      ("tandfonline.com", "https://www.tandfonline.com/"),
                      ("wiley.com", "https://onlinelibrary.wiley.com/")):
        if host in url:
            return ref
    if "biorxiv.org" in url or "medrxiv.org" in url:
        return url.replace(".full.pdf", "")
    return url


def _browser_get(url: str, referer: Optional[str] = None, timeout: int = 40):
    """浏览器伪装 + 忽略 SSL + Referer 的 GET。永不抛异常，返回 bytes 或 None。
    委托给当前 HttpClient。"""
    return _client.browser_get(url, referer, timeout)


def _pdf_from_landing(html: str, base: str) -> Optional[str]:
    """从机构库落地页 HTML 抽真实 PDF 直链（DSpace bitstream / Pure portal-files / 通用 .pdf）。"""
    for pat in (r'href="([^"]+bitstream[^"]+\.pdf[^"]*)"',
                r'href="([^"]+/portal/files/[^"]+\.pdf[^"]*)"',
                r'href="([^"]+/files/[^"]+\.pdf[^"]*)"',
                r'href="([^"]+\.pdf[^"]*)"',
                r'(?:citation_pdf_url|content="[^"]*?)["\']?\s*content="([^"]+\.pdf[^"]*)"'):
        m = _re.search(pat, html, _re.I)
        if m:
            link = m.group(1).replace("&amp;", "&")
            return urllib.parse.urljoin(base, link)
    return None


def resolve_oa_pdf(doi: str):
    """遍历 Unpaywall 全部 oa_locations 找能下到的 PDF：① 任一位置暴露 PMCID → EPMC 渲染链；
    ② 仓库直链 url_for_pdf（仓库不 WAF，优先）；③ 仓库落地页 → 解析出 PDF 直链。返回 (bytes, url) 或 (None, reason)。"""
    code, body, _ = http_get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={EMAIL}")
    if code != 200:
        return None, f"unpaywall_{code}"
    try:
        locs = json.loads(body).get("oa_locations") or []
    except Exception:
        return None, "unpaywall_badjson"
    # ① PMCID 命中（最干净）
    for l in locs:
        m = _re.search(r"PMC\d+", (l.get("url") or "") + " " + (l.get("url_for_pdf") or ""))
        if m:
            b = _browser_get(f"https://europepmc.org/articles/{m.group(0)}?pdf=render")
            if b and b[:4] == b"%PDF":
                return b, f"epmc_render:{m.group(0)}"
    # ② 仓库直链（repository 优先于 publisher）
    for l in sorted(locs, key=lambda x: x.get("host_type") == "publisher"):
        up = l.get("url_for_pdf")
        if up:
            b = _browser_get(up, _referer_for(up))
            if b and b[:4] == b"%PDF":
                return b, up
    # ③ 落地页解析
    for l in locs:
        land = l.get("url")
        if land and not land.lower().endswith(".pdf"):
            html = _browser_get(land)
            if html:
                link = _pdf_from_landing(html.decode("utf-8", "ignore"), land)
                if link:
                    b = _browser_get(link, land)
                    if b and b[:4] == b"%PDF":
                        return b, link
    return None, "no_mirror"


def _browser_get_url(url: str, referer: Optional[str] = None, timeout: int = 40):
    """同 _browser_get，但一并返回重定向后的最终 URL（作相对链接的 base）。返回 (final_url, bytes) 或 (None, None)。
    委托给当前 HttpClient。"""
    return _client.browser_get_url(url, referer, timeout)


def _pdf_via_doi_landing(doi: str):
    """Unpaywall 之外的免费兜底：DOI → 出版商/仓库落地页 → citation_pdf_url meta 或页面 .pdf 直链。
    覆盖 Unpaywall 没索引/直链失效的 OA（如迁站的 Frontiers、大学机构库）。返回 (bytes, url) 或 (None, reason)。"""
    base, html = _browser_get_url(f"https://doi.org/{doi}")
    if not html:
        return None, "landing_unreachable"
    h = html.decode("utf-8", "ignore")
    m = (_re.search(r'name=["\']citation_pdf_url["\'][^>]*content=["\']([^"\']+)["\']', h, _re.I)
         or _re.search(r'content=["\']([^"\']+)["\'][^>]*name=["\']citation_pdf_url["\']', h, _re.I))
    url = m.group(1) if m else _pdf_from_landing(h, base or f"https://doi.org/{doi}")
    if not url:
        return None, "no_landing_pdf"
    url = urllib.parse.urljoin(base or f"https://doi.org/{doi}", url.replace("&amp;", "&"))
    b = _browser_get(url, base)
    if b and b[:4] == b"%PDF" and len(b) > 5000:
        return b, url
    return None, "landing_pdf_not_pdf"


def download_pdf(paper: Dict, client: Optional[HttpClient] = None):
    """Public entry: fetch PDF bytes for an article dict (no disk write).

    Returns (pdf_bytes|None, url). Wraps the internal multi-mirror strategy so
    callers outside this module do not depend on a private function. Pass
    `client` to inject a transport (tests supply a fake; default is urllib).
    """
    with _using_client(client):
        return _download_pdf(paper)


def _download_pdf(paper: Dict):
    """取 PDF 字节（不落盘）。顺序：pmcid→EPMC 渲染链；已知 pdf_url/land_url（浏览器伪装）；
    Unpaywall 全镜像（resolve_oa_pdf）；最后 DOI 落地页 citation_pdf_url 兜底。"""
    if paper.get("pmcid"):
        num = paper["pmcid"].upper().replace("PMC", "")
        b = _browser_get(f"https://europepmc.org/articles/PMC{num}?pdf=render")
        if b and b[:4] == b"%PDF":
            return b, f"epmc_render:PMC{num}"
    for k in ("pdf_url", "land_url"):
        u = paper.get(k)
        if u:
            b = _browser_get(u, _referer_for(u))
            if b and b[:4] == b"%PDF":
                return b, u
    if paper.get("doi"):
        b, u = resolve_oa_pdf(paper["doi"])           # ① Unpaywall 全镜像绕 WAF
        if b:
            return b, u
        return _pdf_via_doi_landing(paper["doi"])     # ② 落地页 citation_pdf_url 兜底
    return None, ""


def _has_pdf(p):
    # 有 doi 也允许：_download_pdf 会用 Unpaywall(resolve_oa_pdf) 反查 OA 直链，
    # 裸 DOI 的 gold/green OA 文章才不会被挡在 PDF 兜底之外。
    return bool(p.get("pdf_url") or p.get("land_url") or p.get("pmcid") or p.get("doi"))


def _parse_pdf_3layer(pdf: bytes):
    """PDF 字节 → 三层解析：Docling(分章节) → PyMuPDF(扁平) → 文字层过少则 OCR(扫描件)。
    按**全部 section 求和**判长度。返回 (parsed, tag) 或 (None, reason)。多个 PDF 来源共用。"""
    try:                                              # 主力 Docling
        parsed = parse_pdf_docling(pdf)
        if sum(len(v) for v in parsed["sections"].values()) >= 500:
            return parsed, "pdf_docling"
    except Exception:
        pass
    try:                                              # 备份 PyMuPDF（可能已按标题分节）
        parsed = parse_pdf_pymupdf(pdf)
        if sum(len(v) for v in parsed["sections"].values()) >= 1000:
            return parsed, "pdf_pymupdf"
        ocr = parse_pdf_ocr(pdf)                       # 文字层过少 → 疑似扫描件 → OCR
        if sum(len(v) for v in ocr["sections"].values()) >= 500:
            return ocr, "pdf_ocr"
        return None, "pdf_too_short"
    except Exception as e:
        return None, f"pdf_parse_{type(e).__name__}"


def _a_pdf(paper: Dict):
    """三层兜底：下载一次 PDF → _parse_pdf_3layer。"""
    pdf, url = _download_pdf(paper)
    if pdf is None:
        return None, "pdf_download_failed", ""
    parsed, tag = _parse_pdf_3layer(pdf)
    if parsed is None:
        return None, tag, ""
    return parsed, tag, url


def _a_wiley_tdm(paper: Dict):
    """Wiley 官方 TDM API（合规,机构订阅方有权非商业文本挖掘,不额外收费）：
    GET api.wiley.com/onlinelibrary/tdm/v1/articles/{DOI} + 头 Wiley-TDM-Client-Token → PDF。
    覆盖机构订阅的 Wiley 内容(含闭源,不止 OA)；无反爬、无阅读器,直出干净 PDF。
    token 从 https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining 自助领,
    登记进 ../api.md 的环境变量 WILEY_TDM_TOKEN。"""
    key = os.environ.get("WILEY_TDM_TOKEN", "")
    if not key:
        return None, "no_token", ""
    doi = paper["doi"]
    url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{urllib.parse.quote(doi)}"
    # urllib 默认跟随重定向；TDM 会 302 到实际 PDF
    code, body, err = http_get(url, {"Wiley-TDM-Client-Token": key, "User-Agent": BROWSER_UA})
    if code in (401, 403):
        return None, f"auth_{code}(token?)", ""
    if code != 200 or body[:4] != b"%PDF":
        return None, f"wiley_tdm_{code or err}", ""
    parsed, tag = _parse_pdf_3layer(body)
    if parsed is None:
        return None, tag, ""
    return parsed, "wiley_tdm", url


def _mk_pdf_single(parse_fn, tag):
    def adapter(paper):
        pdf, url = _download_pdf(paper)
        if pdf is None:
            return None, "pdf_download_failed", ""
        try:
            return parse_fn(pdf), tag, url
        except Exception as e:
            return None, f"{tag}_{type(e).__name__}", ""
    return adapter


_a_pdf_docling = _mk_pdf_single(parse_pdf_docling, "pdf_docling")
_a_pdf_pymupdf = _mk_pdf_single(parse_pdf_pymupdf, "pdf_pymupdf")
_a_pdf_ocr = _mk_pdf_single(parse_pdf_ocr, "pdf_ocr")


# ── EZProxy 订阅路线：机构账号 Cookie 会话 → 落地页 HTML→JSON（优先）/ PDF（备选）──────
# 会话/Cookie/UA 集中此一处（locality）。账号安全的批量节流留在编排层（fetch runner 的
# speed 节流），适配器本身不 sleep。URL/抠链/登录判定复用 library_download 的纯函数。
from . import library_download as _lib


def _ez_cookie_candidates():
    """Cookie 文件候选路径（library.json 同源的 config 路径）。"""
    from ...library import config as _libcfg
    return (str(_libcfg.cookie_file()),)


_EZ_SESSION = None
_EZ_TRIED = False


def _ezproxy_session():
    """懒加载机构账号 requests.Session（Cookie + 登录时的 UA）。无 Cookie/加载失败 → None。
    兼容两种 Cookie 文件格式：扁平 list 与自定义 dict({"cookies":[...], "user_agent":...})。"""
    global _EZ_SESSION, _EZ_TRIED
    if _EZ_TRIED:
        return _EZ_SESSION
    _EZ_TRIED = True
    try:
        import requests
        path = next((p for p in _ez_cookie_candidates() if os.path.exists(p)), None)
        if not path:
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        cookies = raw["cookies"] if isinstance(raw, dict) else raw
        ua = raw.get("user_agent") if isinstance(raw, dict) else None   # 用登录 UA（防御 UA 指纹）
        s = requests.Session()
        s.headers.update({"User-Agent": ua or _lib.USER_AGENT})
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        _EZ_SESSION = s
    except Exception:
        _EZ_SESSION = None
    return _EZ_SESSION


def _has_ezproxy(p) -> bool:
    return bool(p.get("doi")) and _ezproxy_session() is not None


def _proxy_get(url: str, referer: Optional[str] = None, timeout: int = 45):
    """经 EZProxy 会话 GET（转代理域名）。返回 (final_url, resp|None, err)。不 sleep。"""
    s = _ezproxy_session()
    if s is None:
        return url, None, "no_ezproxy_session"
    purl = _lib.to_proxy_url(url)
    try:
        r = s.get(purl, allow_redirects=True, timeout=timeout,
                  headers={"Referer": referer} if referer else None)
    except Exception as e:
        return purl, None, f"exc_{type(e).__name__}"
    if _lib.is_login_page(r.url):
        return r.url, None, "login_required"
    if r.status_code >= 400:
        return r.url, None, f"http_{r.status_code}"
    return r.url, r, ""


def _pdf_bytes_to_parsed(pdf: bytes, url: str):
    """已下到的 PDF 字节 → 三层解析（Docling → PyMuPDF → OCR），返回适配器三元组。"""
    try:
        parsed = parse_pdf_docling(pdf)
        if sum(len(v) for v in parsed["sections"].values()) >= 500:
            return parsed, "ezproxy_pdf", url
    except Exception:
        pass
    try:
        parsed = parse_pdf_pymupdf(pdf)
        if len(parsed["sections"].get("fulltext", "")) >= 1000:
            return parsed, "ezproxy_pdf", url
        ocr = parse_pdf_ocr(pdf)
        if len(ocr["sections"].get("fulltext", "")) >= 500:
            return ocr, "ezproxy_pdf", url
        return None, "pdf_too_short", url
    except Exception as e:
        return None, f"pdf_parse_{type(e).__name__}", url


def _a_ezproxy_html(paper: Dict):
    """DOI → 出版商落地页（经代理）→ parse_article_html → sections。落地页 HTML 缓存到
    paper['_ez_landing']，供 _a_ezproxy_pdf 复用（回落路径不重复取页）。"""
    if _ezproxy_session() is None:
        return None, "no_ezproxy_session", ""
    pub = _lib.resolve_doi(paper["doi"], _ezproxy_session())
    fin, resp, err = _proxy_get(pub)
    if resp is None:
        return None, err, pub
    # 少数文章页本身即 PDF：不在此处理，缓存空、让 pdf 适配器兜
    if resp.content[:5] == b"%PDF-":
        paper["_ez_landing"] = (fin, "", resp.content)
        return None, "landing_is_pdf", fin
    html = resp.text
    paper["_ez_landing"] = (fin, html, None)
    parsed = parse_article_html(html)
    if not any(k != "abstract" for k in parsed.get("sections", {})):
        return None, "no_fulltext_html", fin
    return parsed, "ezproxy_html", fin


def _a_ezproxy_pdf(paper: Dict):
    """落地页 → 抠 PDF 直链（citation_pdf_url / 评分链接 / 出版商模式）→ 经代理下载 → 解析。
    优先复用 _a_ezproxy_html 缓存的落地页 HTML，避免重复 GET。"""
    s = _ezproxy_session()
    if s is None:
        return None, "no_ezproxy_session", ""
    doi = paper["doi"]
    cached = paper.get("_ez_landing")
    if cached:
        fin, html, pdf_bytes = cached
        if pdf_bytes:                       # 落地页本身就是 PDF
            return _pdf_bytes_to_parsed(pdf_bytes, fin)
    else:
        pub = _lib.resolve_doi(doi, s)
        fin, resp, err = _proxy_get(pub)
        if resp is None:
            return None, err, pub
        if resp.content[:5] == b"%PDF-" and len(resp.content) > 5000:
            return _pdf_bytes_to_parsed(resp.content, fin)
        html = resp.text
    # 收集候选 PDF 直链：落地页里抠 + 出版商常见模式
    candidates: List[str] = []
    link = _lib.find_pdf_link_in_html(html, fin)
    if link:
        candidates.append(link)
    candidates += _lib.construct_fallback_pdf_urls(doi, fin)
    for cand in candidates:
        pcand = _lib.to_proxy_url(cand)
        try:
            r = s.get(pcand, allow_redirects=True, timeout=90)
        except Exception:
            continue
        if r.content[:5] == b"%PDF-" and len(r.content) > 5000:
            return _pdf_bytes_to_parsed(r.content, cand)
    return None, "non_pdf_or_no_link", fin


# name -> (adapter, applies(paper)->bool, source_endpoint)
# 全部测过的源都登记在此（即便本项目用处小，保留供复用）。是否进默认链见 PRIORITY。
ADAPTERS = {
    "pmc_xml":  (_a_pmc_xml,  lambda p: bool(p.get("pmcid")), "ncbi_efetch_pmc"),
    "pmc_html": (_a_pmc_html, lambda p: bool(p.get("pmcid")), "ncbi_pmc_html"),
    "epmc_xml": (_a_epmc_xml, lambda p: bool(p.get("pmcid")), "europepmc_fulltextxml"),
    "springer": (_a_springer, lambda p: (p.get("doi") or "").startswith(SPRINGER_PREFIXES), "springer_oa"),
    "wiley_tdm": (_a_wiley_tdm, lambda p: bool(os.environ.get("WILEY_TDM_TOKEN")) and (p.get("doi") or "").startswith(WILEY_PREFIXES), "wiley_tdm_api"),
    "elsevier": (_a_elsevier, lambda p: (p.get("doi") or "").startswith("10.1016"), "elsevier_article"),
    "biorxiv":  (_a_biorxiv,  lambda p: (p.get("doi") or "").startswith("10.1101"), "biorxiv_api"),
    "core":     (_a_core,     lambda p: bool(p.get("doi")), "core_v3"),
    # EZProxy 订阅路线（requests+代理域）：登记保留但【不进默认链】—— 依赖机构账号 Cookie，
    # 且 requests 打不过 ScienceDirect 反爬、EZProxy 登录态易过期，实测收益有限。
    # 需要时显式 sources=["ezproxy_html","ezproxy_pdf"] 调用；HTML 路的正解应是真浏览器渲染。
    "ezproxy_html": (_a_ezproxy_html, _has_ezproxy, "ezproxy_publisher_html"),
    "ezproxy_pdf":  (_a_ezproxy_pdf,  _has_ezproxy, "ezproxy_publisher_pdf"),
    # PDF 路线（兜底）：pdf=三层自动；其余三个可显式单测
    "pdf":         (_a_pdf,         _has_pdf, "pdf_auto"),
    "pdf_docling": (_a_pdf_docling, _has_pdf, "pdf_docling"),
    "pdf_pymupdf": (_a_pdf_pymupdf, _has_pdf, "pdf_pymupdf"),
    "pdf_ocr":     (_a_pdf_ocr,     _has_pdf, "pdf_ocr"),
}

# 默认优先级：PMC(最高质量、免费) → EPMC 镜像 → 出版商 OA-XML → PDF(兜底, Docling 主力)。
#   PDF 兜底现在对裸 DOI 也生效(_has_pdf 认 doi)：走 Unpaywall 反查 OA 直链，gold/green OA 免登录可得。
# 不在默认链但已登记、可显式调用（sources=[...]）的：
#   - "core"：纯文本兜底，本库命中率低+限流凶，保留供别处复用。
#   - "ezproxy_html"/"ezproxy_pdf"：EZProxy 订阅路，依赖机构 Cookie、requests 过不了强反爬，收益有限。
#   - "pdf_docling"/"pdf_pymupdf"/"pdf_ocr"：PDF 单方法，供单测/强制指定；默认用 "pdf"(三层自动)。
PRIORITY = ["pmc_xml", "pmc_html", "epmc_xml", "springer", "wiley_tdm", "elsevier", "biorxiv", "pdf"]
ALL_SOURCES = list(ADAPTERS)   # 含未进默认链的（core / pdf 单方法等），需要时 sources=ALL_SOURCES


def get_fulltext(paper: Dict, sources: Optional[List[str]] = None,
                 client: Optional[HttpClient] = None) -> Tuple[Optional[Dict], str]:
    """深接口：按优先级逐源取结构化全文，第一篇质检非 reject 即返回。

    paper：含 doi/pmid/pmcid 的字典（其余字段作 build_doc 底座）。
    sources：限定试哪些源（默认 PRIORITY 全链）。
    client：注入 transport（测试传 fake，默认走 urllib）。
    返回 (doc, "") 成功 / (None, reason) 失败；reason 逐源列出每个*实际尝试过*的源的原因
    （"源:原因" 用 "; " 连接，如 "elsevier:elsevier_404; ezproxy_html:no_fulltext_html;
    pdf:pdf_download_failed"），未命中的门（applies=False）不计入以免噪声。
    """
    with _using_client(client):
        return _get_fulltext(paper, sources)


def _get_fulltext(paper: Dict, sources: Optional[List[str]] = None
                  ) -> Tuple[Optional[Dict], str]:
    order = sources or PRIORITY
    paper = dict(paper)

    # 缺 PMCID 但有 doi/pmid → 先反查（一次，供所有 PMC 系源用）
    if not paper.get("pmcid") and (paper.get("doi") or paper.get("pmid")) \
            and any(s in order for s in ("pmc_xml", "pmc_html", "epmc_xml")):
        pm = resolve_pmcid(paper.get("doi"), paper.get("pmid"))
        if pm:
            paper["pmcid"] = pm

    reasons: List[str] = []
    for name in order:
        adapter, applies, endpoint = ADAPTERS[name]
        if not applies(paper):
            continue
        try:
            parsed, tag, url = adapter(paper)
        except Exception as e:
            reasons.append(f"{name}:exc_{type(e).__name__}"); continue
        if parsed is None:
            reasons.append(f"{name}:{tag}"); continue
        prov = {"access_source": tag, "source_endpoint": endpoint, "fulltext_url": url,
                "accessed_at": _now(),
                "license": parsed.get("license", ""), "license_url": parsed.get("license_url", ""),
                "reuse_class": _reuse_class(parsed.get("license", ""), parsed.get("license_url", ""))}
        doc = build_doc(paper.get("pmcid", ""), parsed, paper, prov)
        if doc["quality"]["quality_status"] != "reject":
            return doc, ""
        reasons.append(f"{name}:reject(" + ",".join(doc["quality"]["issues"]) + ")")
    return None, "; ".join(reasons) if reasons else "no_applicable_source"
