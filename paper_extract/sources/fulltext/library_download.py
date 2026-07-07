#!/usr/bin/env python3
"""
library_download.py – EZProxy 代理 URL / DOI 解析 / PDF 链接提取的纯函数工具。

纯函数（to_proxy_url / resolve_doi / find_pdf_link_in_html / construct_fallback_pdf_urls
/ is_login_page）供 fetch 编排层与 library.browser 复用。
代理域名（proxy suffix）从 library.json 配置读取，不绑定任何具体学校。
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

import requests

from ...library import config as _libcfg


def _proxy_suffix() -> str:
    """当前机构代理后缀（从 library.json 读取，无则空串）。"""
    return _libcfg.get_proxy_suffix()


USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ─── URL 工具 ─────────────────────────────────────────────────────────
def to_proxy_url(url: str, suffix: str | None = None) -> str:
    """将普通 URL 转换为 EZProxy 代理 URL（后缀来自 library.json，可显式传入）。

    例（后缀 libproxy.myuni.edu）: https://link.springer.com/article/xxx
      → https://link-springer-com.libproxy.myuni.edu/article/xxx
    无配置后缀时原样返回。
    """
    suffix = suffix if suffix is not None else _proxy_suffix()
    parsed = urlparse(url)
    hostname: str = parsed.hostname or ""
    if not hostname or not suffix:
        return url
    if suffix in hostname:
        return url  # 已经是代理 URL
    proxy_host: str = hostname.replace(".", "-") + f".{suffix}"
    result: str = f"{parsed.scheme}://{proxy_host}{parsed.path}"
    if parsed.query:
        result += f"?{parsed.query}"
    return result


def resolve_doi(doi: str, sess: requests.Session) -> str:
    """解析 DOI 获取出版商真实 URL。用干净 session 避免代理 Cookie 干扰。"""
    clean = requests.Session()
    clean.headers.update({"User-Agent": USER_AGENT})
    try:
        r = clean.head(f"https://doi.org/{doi}", allow_redirects=True, timeout=15)
        url: str = r.url
    except Exception:
        url = f"https://doi.org/{doi}"

    # Elsevier linkinghub 不做 HTTP 重定向，需要从 HTML meta refresh 中提取真实 URL
    if "linkinghub.elsevier.com" in url:
        url = _resolve_elsevier_linkinghub(url, doi)
    return url


def _resolve_elsevier_linkinghub(linkinghub_url: str, doi: str) -> str:
    """从 Elsevier linkinghub URL 提取 PII，构造 ScienceDirect URL。"""
    # 从 URL 路径提取 PII
    m = re.search(r'/pii/([A-Z0-9]+)', linkinghub_url)
    if m:
        pii: str = m.group(1)
        return f"https://www.sciencedirect.com/science/article/pii/{pii}"
    return linkinghub_url


def construct_fallback_pdf_urls(doi: str, publisher_url: str) -> List[str]:
    """根据出版商 URL 构造常见的 PDF 直链模式。作为 find_pdf_link_in_html 的后备。"""
    parsed = urlparse(publisher_url)
    host: str = (parsed.hostname or "").lower()
    fallbacks: List[str] = []

    # Springer / SpringerLink
    if "springer" in host or "springerlink" in host:
        fallbacks.append(f"https://link.springer.com/content/pdf/{doi}.pdf")

    # Elsevier / ScienceDirect
    if "sciencedirect" in host or "elsevier" in host:
        m = re.search(r'/pii/([A-Z0-9]+)', publisher_url)
        if m:
            pii = m.group(1)
            fallbacks.append(f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true")

    # Wiley
    if "wiley" in host:
        fallbacks.append(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}")

    # Nature
    if "nature.com" in host:
        fallbacks.append(publisher_url.rstrip('/') + ".pdf")

    # Taylor & Francis
    if "tandfonline" in host:
        fallbacks.append(f"https://www.tandfonline.com/doi/pdf/{doi}")

    # SAGE
    if "sagepub" in host:
        fallbacks.append(f"https://journals.sagepub.com/doi/pdf/{doi}")

    # Oxford Academic
    if "oup.com" in host or "academic.oup" in host:
        # OUP uses complex URLs, try appending /pdf
        fallbacks.append(publisher_url.rstrip('/') + "?pdf=True")

    return fallbacks


def is_login_page(url: str) -> bool:
    """判断当前页面是否为登录页。"""
    lower: str = url.lower()
    return any(marker in lower for marker in [
        "/login", "/sso/", "/simplesaml/", "/idp/", "cas/login",
        "shibboleth", "signin", "wayf", "authn",
    ])


# ─── PDF 链接从 HTML 中提取 ──────────────────────────────────────────
def find_pdf_link_in_html(html: str, base_url: str) -> Optional[str]:
    """从 HTML 页面中提取 PDF 下载链接。"""
    # 策略 1: citation_pdf_url meta 标签（最可靠）
    m = re.search(
        r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\']([^"\']+)["\']',
        html, re.I,
    )
    if m:
        return m.group(1)
    # 反向属性顺序
    m = re.search(
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']citation_pdf_url["\']',
        html, re.I,
    )
    if m:
        return m.group(1)

    # 策略 2: 带评分的链接搜索
    candidates: List[tuple[int, str]] = []
    for match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href: str = match.group(1)
        text: str = re.sub(r'<[^>]+>', '', match.group(2)).strip().lower()
        hl: str = href.lower()
        score: int = 0
        if "download pdf" in text:
            score += 10
        if "pdf" in text and "download" in text:
            score += 8
        if text == "pdf":
            score += 6
        if "pdfdirect" in hl:
            score += 7
        if hl.endswith(".pdf"):
            score += 5
        if "/pdf/" in hl or "/pdf?" in hl:
            score += 4
        if "pdf" in text:
            score += 3
        if "purchase" in text or "buy" in text or "rent" in text:
            score -= 20
        if "supplement" in text:
            score -= 5
        if score > 0:
            candidates.append((score, href))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    return None
