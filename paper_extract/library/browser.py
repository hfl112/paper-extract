"""Institutional-library full-text access — persistent browser primary, cookie fallback.

Nothing is hardcoded to a specific school: the login URL template and proxy
suffix come from library.json (see config.py). The proxy suffix is auto-captured
on first login and updated whenever it changes.

Primary route  : a persistent cloakbrowser profile carries your login session and
                 renders the real publisher page (gets past most anti-bot walls).
Fallback route : cookie + requests via the EZProxy adapters in fulltext_sources.

All cloakbrowser use is lazy-imported so importing this module never requires it.
"""
from __future__ import annotations

import atexit
import base64
import json
import re
import time
from typing import Any

from ..paths import data_root
from . import config

# ── rendering constants (ported from the archived institutional_fetch) ─────────
_BODY_SELECTORS = ("#body", ".Body", "article", "main", "div[class*='article']", "body")
_CF_WAIT = 15
_MIN_BODY = 500
_LOGIN_TEXT_MARKERS = ("just a moment", "checking your browser", "attention required",
                       "shibboleth", "institutional login", "sign in to continue",
                       "log in to the library", "library card", "ezproxy", "patron",
                       "username", "password")
_LOGIN_TITLE_MARKERS = ("log in", "login", "sign in", "library", "just a moment",
                        "authentication", "ezproxy")
_PAYWALL_MARKERS = ("get access", "purchase pdf", "purchase access", "buy article",
                    "check access", "get full text access", "rent this article", "/getaccess/")

_INPAGE_FETCH_JS = r"""
async (paths) => {
  for (const p of paths) {
    try {
      const r = await fetch(p, {credentials: 'include'});
      if (!r.ok) continue;
      const buf = new Uint8Array(await r.arrayBuffer());
      if (buf.length > 5000 && buf[0]===0x25 && buf[1]===0x50 && buf[2]===0x44 && buf[3]===0x46) {
        let s = ''; for (let i=0;i<buf.length;i++) s += String.fromCharCode(buf[i]);
        return {ok:true, b64: btoa(s), url: p};
      }
    } catch (e) {}
  }
  return {ok:false};
}
"""


def _profile_dir() -> str:
    return str(data_root() / "library_browser_profile")


def _login_url_for(target: str) -> str:
    """Build the EZProxy entry URL for a target (the proven `login?url=` form).

    Uses an explicit login_url_template if set; otherwise derives it from the
    auto-detected proxy suffix: https://<suffix>/login?url=<target>.
    """
    tmpl = config.get_login_url_template()
    if tmpl:
        return tmpl.replace("{target}", target) if "{target}" in tmpl else tmpl
    suffix = config.get_proxy_suffix()
    if suffix:
        return f"https://{suffix}/login?url={target}"
    return ""


# ── persistent context singleton (shared across a fetch run) ───────────────────
_CTX = None
_CTX_TRIED = False

# When False (non-interactive / no TTY), the library path never waits for a human
# to log in — it fails fast on a login page instead of blocking.
_INTERACTIVE = True


def set_interactive(flag: bool) -> None:
    global _INTERACTIVE
    _INTERACTIVE = bool(flag)


def doctor() -> dict:
    """Read-only readiness check for library access (no browser, no waiting).

    Returns {ready, reason, next_action, checks}. `ready` means statically
    configured; session liveness is only confirmed at fetch time.
    """
    import importlib.util
    from pathlib import Path

    from .libkey import staged_extension

    cb = importlib.util.find_spec("cloakbrowser") is not None
    suffix = config.get_proxy_suffix()
    tmpl = config.get_login_url_template()
    profile_exists = Path(_profile_dir()).exists()
    checks = {
        "cloakbrowser_installed": cb,
        "proxy_suffix": suffix or "",
        "login_url_template": bool(tmpl),
        "browser_profile": profile_exists,
        "libkey_extension": staged_extension() is not None,
    }
    if not cb:
        return {"ready": False, "reason": "browser_unavailable",
                "next_action": 'install browser support: pip install ".[browser]"', "checks": checks}
    if not (suffix or tmpl):
        return {"ready": False, "reason": "missing_proxy_suffix",
                "next_action": "run: paper-extract library login", "checks": checks}
    if not profile_exists:
        return {"ready": False, "reason": "needs_login",
                "next_action": "run: paper-extract library login", "checks": checks}
    return {"ready": True, "reason": "ready",
            "next_action": "run: paper-extract fetch --access library",
            "checks": checks,
            "note": "static readiness only; session liveness is confirmed at fetch time"}


def _get_context(headless: bool = False):
    global _CTX, _CTX_TRIED
    if _CTX_TRIED:
        return _CTX
    _CTX_TRIED = True
    try:
        import cloakbrowser as cb

        from .libkey import staged_extension

        kwargs = {
            "headless": headless,
            "humanize": True,
            "args": [f"--fingerprint={config.get_fingerprint_seed()}"],
        }
        ext = staged_extension()
        if ext is not None:
            # Extensions require a headed context in Chromium.
            kwargs["headless"] = False
            kwargs["extension_paths"] = [str(ext)]
        _CTX = cb.launch_persistent_context(_profile_dir(), **kwargs)
        atexit.register(_close_context)
        _inject_chrome_cookies(_CTX)
    except Exception:
        _CTX = None
    return _CTX


def _inject_chrome_cookies(ctx) -> int:
    """Best-effort: load borrowed Chrome cookies into the context (SSO reuse)."""
    from .chrome_cookies import playwright_cookies

    cookies = playwright_cookies()
    if not cookies:
        return 0
    try:
        ctx.add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


def _close_context() -> None:
    global _CTX
    try:
        if _CTX is not None:
            _CTX.close()
    except Exception:
        pass
    _CTX = None


# ── page helpers ───────────────────────────────────────────────────────────────
def _wait_cloudflare(page) -> None:
    for _ in range(_CF_WAIT):
        try:
            t = (page.title() or "").lower()
        except Exception:
            t = ""
        if "just a moment" not in t and "attention required" not in t:
            return
        time.sleep(1)


def _looks_logged_out(page) -> bool:
    try:
        txt = (page.inner_text("body") or "").lower()
    except Exception:
        txt = ""
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if any(m in title for m in _LOGIN_TITLE_MARKERS):
        return True
    if any(m in txt for m in _PAYWALL_MARKERS):
        return True
    return any(m in txt[:3000] for m in _LOGIN_TEXT_MARKERS)


def _wait_for_login(page, seconds: int = 240) -> bool:
    if not _INTERACTIVE:
        return False  # non-interactive: never block waiting for a human login
    print(f"\n  >>> 请在弹出的浏览器里完成你的学校/机构登录（最多等 {seconds}s）…", flush=True)
    left = seconds
    while left > 0:
        time.sleep(5)
        left -= 5
        if not _looks_logged_out(page):
            print("  >>> 已检测到登录成功，继续。", flush=True)
            return True
    return False


# Auto-detect pacing for the "solve captcha in the browser" wait: poll every
# _POLL_EVERY seconds, giving up after _CAPTCHA_MAX_WAIT so a genuinely
# inaccessible article never stalls the batch.
_POLL_EVERY = 3
_CAPTCHA_MAX_WAIT = 120
_PAYWALL_MAX_WAIT = 45

# If an article body of at least this many chars is rendered, the page is not a
# wall even if a login widget / reCAPTCHA iframe also sits on the page.
_ARTICLE_BODY_MIN = 1000

# reCAPTCHA / hCaptcha / bot-challenge markers (login markers are covered by _looks_logged_out).
_CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "grecaptcha", "not a robot", "verify you are human",
                    "verify you are a human", "unusual traffic", "are you a robot", "press and hold",
                    "select all images", "select each image", "请完成验证", "人机验证", "我不是机器人")

# Challenge widgets render inside third-party iframes with little/no body text,
# so a text-only scan misses them. Match on the iframe host too.
_CHALLENGE_IFRAME_HINTS = ("challenges.cloudflare.com", "hcaptcha.com",
                           "recaptcha", "google.com/recaptcha", "arkoselabs",
                           "funcaptcha", "px-cdn", "perimeterx", "geo.captcha")


def _has_challenge_iframe(page) -> bool:
    try:
        srcs = page.evaluate(
            "() => Array.from(document.querySelectorAll('iframe[src]')).map(f => f.src.toLowerCase())"
        )
    except Exception:
        return False
    return any(any(h in s for h in _CHALLENGE_IFRAME_HINTS) for s in srcs or [])


def _has_captcha_text(page) -> bool:
    try:
        txt = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(m in txt for m in _CAPTCHA_MARKERS)


def _has_article_body(page) -> bool:
    """True if a substantial rendered article body is present."""
    for sel in ("article", "main", "div[class*='article']", "section[class*='article']"):
        try:
            t = page.inner_text(sel) or ""
        except Exception:
            t = ""
        if len(t.strip()) >= _ARTICLE_BODY_MIN:
            return True
    return False


def _looks_blocked(page) -> bool:
    """Login page OR a bot/captcha challenge — i.e. the article isn't visible yet."""
    if _has_article_body(page):
        return False
    if _looks_logged_out(page):
        return True
    if _has_captcha_text(page):
        return True
    return _has_challenge_iframe(page)


def _pause_for_user_if_blocked(page, max_wait: int = _CAPTCHA_MAX_WAIT) -> bool:
    """Interactive only: if the page is a captcha/login wall, pause on THIS page and
    let the user solve it, then continue without terminal input."""
    if not _INTERACTIVE:
        return False
    if _has_article_body(page):
        return False
    blocked = _looks_blocked(page)
    for _ in range(3):
        if blocked:
            break
        time.sleep(1.5)
        blocked = _looks_blocked(page)
    if not blocked:
        return False
    is_captcha = _has_challenge_iframe(page) or _has_captcha_text(page)
    max_wait = max_wait if is_captcha else _PAYWALL_MAX_WAIT
    kind = "验证码/登录页" if is_captcha else "登录/付费墙"
    print(f"\n  ⚠ 检测到{kind}。请在【当前这个浏览器窗口】里点掉验证码 / 登录即可。", flush=True)
    print(f"     无需回终端按回车——工具会自动检测你已通过,然后继续(最多等 {max_wait}s)。", flush=True)
    waited = 0
    clear_streak = 0
    while waited < max_wait:
        time.sleep(_POLL_EVERY)
        waited += _POLL_EVERY
        if _looks_blocked(page):
            clear_streak = 0
            continue
        clear_streak += 1
        if clear_streak >= 2:
            print("  ✓ 已检测到通过验证,自动继续。", flush=True)
            return True
    print(f"  ⏱ 等待 {max_wait}s 仍未通过,跳过这一篇继续。", flush=True)
    return True


def _extract_rendered_sections(page) -> dict[str, Any] | None:
    from ..sources.fulltext.fulltext_sources import split_flat_sections

    best = ""
    for sel in _BODY_SELECTORS:
        try:
            t = page.inner_text(sel) or ""
        except Exception:
            t = ""
        if len(t) > 2000:
            best = t
            break
        if len(t) > len(best):
            best = t
    best = best.strip()
    if len(best) < _MIN_BODY:
        return None
    secs = split_flat_sections(best) or {"fulltext": best}
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    return {"title": title, "sections": secs, "references": [], "supplementary": [],
            "license": "", "license_url": "", "pub_flags": [], "pub_flag_notes": []}


def _pdf_candidate_paths(doi: str, cur_url: str) -> list[str]:
    paths: list[str] = []
    d = (doi or "").lower()
    if d.startswith("10.1016"):
        m = re.search(r"/pii/([A-Z0-9]+)", cur_url, re.I)
        if m:
            pii = m.group(1)
            paths += [f"/science/article/pii/{pii}/pdfft?isDTMRedir=true", f"/science/article/pii/{pii}/pdf"]
    if d.startswith(("10.1002", "10.1111")):
        paths += [f"/doi/pdfdirect/{doi}", f"/doi/pdf/{doi}"]
    if d.startswith("10.1007"):
        paths += [f"/content/pdf/{doi}.pdf"]
    return paths


def _collect_pdf_urls(html: str, page_url: str, doi: str) -> list[str]:
    """Candidate PDF URLs for the current article page.

    General first: the publisher's declared <meta citation_pdf_url> (works for
    AACR / Nature / most publishers), proxied so it stays inside the session.
    Then the publisher-specific guessed paths (Wiley / Springer / Elsevier).
    """
    from ..sources.fulltext import library_download as _lib

    urls: list[str] = []
    if html:
        link = _lib.find_pdf_link_in_html(html, page_url)
        if link:
            # Absolute publisher URL -> route through the proxy so it's same-session.
            urls.append(_lib.to_proxy_url(link) if link.lower().startswith("http") else link)
    for p in _pdf_candidate_paths(doi, page_url):
        if p not in urls:
            urls.append(p)
    return urls


def _pdf_via_inpage_fetch(page, doi: str) -> tuple[bytes | None, str]:
    try:
        html = page.content()
    except Exception:
        html = ""
    urls = _collect_pdf_urls(html, page.url, doi)
    if not urls:
        return None, ""
    return _inpage_fetch_bytes(page, urls)


def _context_request_pdf(page, urls: list[str]) -> tuple[bytes | None, str]:
    """Fetch a PDF via the browser context request.

    This is not subject to CORS and follows cross-host redirects while carrying
    the session cookies, which is needed for signed/watermarked PDF redirects.
    """
    for u in urls:
        if not u:
            continue
        try:
            resp = page.request.get(u, timeout=90000)
            body = resp.body()
        except Exception:
            continue
        if body[:4] == b"%PDF" and len(body) > 5000:
            return body, resp.url or u
    return None, ""


def _inpage_fetch_bytes(page, urls: list[str]) -> tuple[bytes | None, str]:
    """Fetch a PDF for any URL: context request first, then in-page fetch."""
    urls = [u for u in urls if u]
    if not urls:
        return None, ""
    pdf, url = _context_request_pdf(page, urls)
    if pdf:
        return pdf, url
    try:
        res = page.evaluate(_INPAGE_FETCH_JS, urls)
    except Exception:
        return None, ""
    if res and res.get("ok"):
        return base64.b64decode(res["b64"]), res.get("url", "")
    return None, ""


# LibKey Nomad injects an access/PDF link into recognized pages (PubMed, publisher,
# Scholar). Collect likely full-text links, including inside shadow DOM.
_LIBKEY_SCAN_JS = r"""
() => {
  const links = new Set();
  const scan = (root) => {
    try {
      root.querySelectorAll('a[href]').forEach(a => {
        const h = (a.href || '').toLowerCase();
        if (h.includes('libkey.io') || h.includes('thirdiron') || h.includes('pdfdirect') ||
            h.includes('/doi/pdf') || h.includes('pdfft') || h.endsWith('.pdf'))
          links.add(a.href);
      });
      root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) scan(el.shadowRoot); });
    } catch (e) {}
  };
  scan(document);
  return Array.from(links);
}
"""


def _libkey_resolve_url(page, doi: str, pmid: str) -> tuple[str, str]:
    """Open a LibKey-recognized page and read the access link it injects.

    Returns (best_link, landing_url). PubMed is tried first (most reliable for
    LibKey), then the DOI resolver. Returns ("", "") if nothing is found.
    """
    targets = []
    if pmid:
        targets.append(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    if doi:
        targets.append(f"https://doi.org/{doi}")
    for turl in targets:
        try:
            page.goto(turl, wait_until="domcontentloaded", timeout=90000)
        except Exception:
            continue
        _wait_cloudflare(page)
        if _pause_for_user_if_blocked(page):
            _wait_cloudflare(page)
            time.sleep(2)
        for _ in range(8):  # give the extension time to inject
            time.sleep(1.5)
            try:
                links = page.evaluate(_LIBKEY_SCAN_JS)
            except Exception:
                links = []
            preferred = [l for l in links if "libkey.io" in l.lower() or "thirdiron" in l.lower()]
            if preferred:
                return preferred[0], turl
            if links:
                return links[0], turl
    return "", ""


def _fetch_pdf_via_proxy(page, doi: str) -> tuple[bytes | None, str, str]:
    """Fetch the PDF through the configured EZProxy suffix.

    EZProxy needs a real browser navigation (it redirects through Shibboleth SSO,
    using the profile's shib cookie, then sets its own session cookie) — a bare
    HTTP request won't establish that. So we NAVIGATE to the proxied article page
    first (handshake happens), landing on the proxy host, then fetch the PDF
    same-origin (cookies present, no CORS).
    """
    from urllib.parse import urlparse

    from ..sources.fulltext import library_download as _lib

    suffix = config.get_proxy_suffix()
    if not (suffix and doi):
        return None, "", "no_proxy_suffix"
    try:
        publisher = _lib.resolve_doi(doi, None)
    except Exception:
        publisher = f"https://doi.org/{doi}"

    # 1) Navigate to the proxied article page to establish the EZProxy session.
    proxied_article = _lib.to_proxy_url(publisher, suffix)
    try:
        page.goto(proxied_article, wait_until="domcontentloaded", timeout=90000)
    except Exception:
        pass
    _wait_cloudflare(page)
    time.sleep(3)  # allow shib/EZProxy redirect chain (incl. any auto-submit form)
    # Blocked by captcha/login? Pause on this page and let the user solve it.
    if _pause_for_user_if_blocked(page):
        _wait_cloudflare(page)
        time.sleep(2)

    # 2) Candidates: the publisher's declared citation_pdf_url (general — AACR/Nature/…),
    #    then publisher-specific guessed paths, all proxied onto the session host.
    try:
        html = page.content()
    except Exception:
        html = ""
    pdf_urls = _collect_pdf_urls(html, page.url, doi)
    pdf_urls += [_lib.to_proxy_url(u, suffix) for u in _lib.construct_fallback_pdf_urls(doi, publisher)]
    landed_host = urlparse(page.url).netloc
    if landed_host and suffix in landed_host:
        pdf_urls.append(f"https://{landed_host}/content/pdf/{doi}.pdf")
    seen: set[str] = set()
    pdf_urls = [u for u in pdf_urls if u and not (u in seen or seen.add(u))]

    # 3) context-request first (follows cross-host redirects, sends session
    #    cookies, not CORS-limited), then same-origin in-page fetch.
    pdf, url = _inpage_fetch_bytes(page, pdf_urls)
    if pdf:
        return pdf, url, ""
    return None, "", "proxy_pdf_failed:" + ";".join(pdf_urls[:2])


def _apply_pdf_to_article(article: dict, pdf: bytes, url: str, source_tag: str):
    """Parse PDF bytes into sections and write them into the article. Returns article|None."""
    from .. import assemble as assemble_mod
    from ..sources.fulltext import fulltext_fetcher, fulltext_sources

    parsed, ptag = fulltext_sources._parse_pdf_3layer(pdf)
    if parsed is None:
        return None
    prov = {"access_source": f"{source_tag}_{ptag}", "source_endpoint": source_tag,
            "fulltext_url": url, "accessed_at": fulltext_fetcher._now()}
    return assemble_mod.assemble_from_parsed(article, parsed, prov)


def _fetch_pdf_via_libkey(page, doi: str, pmid: str) -> tuple[bytes | None, str, str]:
    """Use LibKey's injected link to fetch the PDF. Returns (bytes|None, url, reason)."""
    link, _landing = _libkey_resolve_url(page, doi, pmid)
    if not link:
        return None, "", "libkey_no_link"
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=90000)
    except Exception:
        pass
    _wait_cloudflare(page)
    time.sleep(2)
    if _pause_for_user_if_blocked(page):
        _wait_cloudflare(page)
        time.sleep(2)
    # The LibKey link often redirects to the actual PDF; grab the current URL,
    # then fall back to publisher-specific candidate paths.
    pdf, url = _inpage_fetch_bytes(page, [page.url, link])
    if pdf:
        return pdf, url, ""
    pdf2, url2 = _pdf_via_inpage_fetch(page, doi or "")
    if pdf2:
        return pdf2, url2, ""
    return None, link, "libkey_pdf_fetch_failed"


def _fetch_institutional(doi: str, ctx, wait_login: bool = False):
    """Return (parsed|None, tag, reason). tag in {institutional_html, institutional_pdf}."""
    from ..sources.fulltext.fulltext_fetcher import parse_article_html
    from ..sources.fulltext.fulltext_sources import _parse_pdf_3layer

    # EZProxy template if configured; otherwise go straight to the DOI and rely on
    # the logged-in persistent session (SSO / OpenAthens).
    url = _login_url_for(f"https://doi.org/{doi}") or f"https://doi.org/{doi}"
    page = ctx.new_page()
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            return None, "", f"goto_err:{type(e).__name__}"
        _wait_cloudflare(page)
        time.sleep(3)
        if _pause_for_user_if_blocked(page):
            _wait_cloudflare(page)
            time.sleep(2)

        parsed = _extract_rendered_sections(page)
        if parsed is not None and sum(len(v) for v in parsed["sections"].values()) >= _MIN_BODY:
            return parsed, "institutional_html", ""

        if _looks_logged_out(page):
            if wait_login and _wait_for_login(page):
                time.sleep(3)
                parsed = _extract_rendered_sections(page)
                if parsed is not None and sum(len(v) for v in parsed["sections"].values()) >= _MIN_BODY:
                    return parsed, "institutional_html", ""
            else:
                return None, "", "logged_out_or_paywall"

        try:
            html = page.content()
        except Exception:
            html = ""
        if html:
            p2 = parse_article_html(html)
            body2 = sum(len(v) for k, v in p2.get("sections", {}).items() if k != "abstract")
            if body2 >= _MIN_BODY and any(k != "abstract" for k in p2.get("sections", {})):
                return p2, "institutional_html", ""

        pdf, _url = _pdf_via_inpage_fetch(page, doi)
        if pdf:
            parsed, ptag = _parse_pdf_3layer(pdf)
            if parsed is not None:
                return parsed, "institutional_pdf", ""
            return None, "", f"pdf_parse_{ptag}"
        return None, "", "html_thin_no_pdf"
    finally:
        try:
            page.close()
        except Exception:
            pass


# ── public API ──────────────────────────────────────────────────────────────────
_SESSION_READY = False


def begin_live_session(landing_url: str | None = None) -> bool:
    """Open the browser once and let the user establish a LIVE access session.

    EZProxy/Shibboleth sessions are short-lived and cold re-establishment trips
    reCAPTCHA. So we open the browser, let the human make access live (log in /
    click LibKey / solve any captcha / VPN), then keep this same context open for
    the whole fetch run — every article reuses the live session, no cold starts.
    """
    global _SESSION_READY
    if _SESSION_READY:
        return True
    ctx = _get_context()
    if ctx is None:
        print("[library] 浏览器不可用（未安装 cloakbrowser?）。")
        return False
    landing = landing_url or config.get_login_landing_url()
    # Enter through the EZProxy `login?url=` form so the human login establishes the
    # proxy session directly (matches the proven old approach), else the plain page.
    entry = _login_url_for(landing) or landing
    page = ctx.new_page()
    try:
        try:
            page.goto(entry, wait_until="domcontentloaded", timeout=90000)
        except Exception:
            pass
        print("\n[library] 浏览器已打开。请在其中把访问弄成“活的”：")
        print("  • 如需要：点一次 LibKey 的 “Download PDF” / 完成人机验证 / 连好 VPN；")
        print("  • 确认这篇文章能看到全文 = 会话已建立。")
        print("然后回到这里按回车。工具会在同一个浏览器会话里批量抓取，")
        print("不再逐篇冷启动、不再反复触发验证码。")
        try:
            input("> 访问弄好后按回车… ")
        except EOFError:
            pass
    finally:
        try:
            page.close()
        except Exception:
            pass
    _SESSION_READY = True
    return True


def library_login(landing_url: str | None = None, proxy_login_url: str | None = None,
                   headless: bool = False, use_libkey: bool | None = None) -> bool:
    """Open a browser, let the user set up institutional access, save the profile.

    use_libkey:
      * None (default) — auto: load LibKey Nomad if it's installed in Chrome,
        otherwise plain SSO / "Access through your institution" / EZProxy.
      * True  — force-load LibKey (error if not installed).
      * False — never load LibKey (plain login).
    Returns True if the session/profile was captured.
    """
    if proxy_login_url:
        config.set_login_url_template(proxy_login_url)
    landing = landing_url or config.get_login_landing_url()

    try:
        import cloakbrowser as cb
    except Exception:
        print('需要先安装浏览器依赖: pip install ".[browser]"')
        return False

    from .libkey import find_chrome_libkey, stage_extension

    # Default: use LibKey automatically when the extension is present in Chrome.
    if use_libkey is None:
        use_libkey = find_chrome_libkey() is not None
        if use_libkey:
            print("检测到 LibKey Nomad,自动加载(如需关闭用 --no-libkey)。")

    launch_kwargs = {
        "headless": headless,
        "humanize": True,
        "args": [f"--fingerprint={config.get_fingerprint_seed()}"],
    }
    if use_libkey:
        ext = stage_extension(force=True)
        if ext is None:
            print("没找到 LibKey Nomad 扩展（Chrome 里没装?）。请先在 Chrome 装好 LibKey Nomad,或用 --no-libkey 走普通登录。")
            return False
        launch_kwargs["headless"] = False  # extensions need a headed context
        launch_kwargs["extension_paths"] = [str(ext)]
        print(f"已加载 LibKey Nomad 扩展: {ext}")

    ctx = cb.launch_persistent_context(_profile_dir(), **launch_kwargs)
    try:
        page = ctx.new_page()
        try:
            page.goto(landing, wait_until="domcontentloaded", timeout=90000)
        except Exception:
            pass
        if use_libkey:
            print("\n浏览器已打开（已加载 LibKey Nomad）。请：")
            print("  1) 点浏览器右上角 LibKey 图标 → 设置里选择你的学校/机构（只需一次）。")
            print("  2) 如果你平时靠校园网，请先连好 VPN。")
            print("  3) 【重要】在文章页点一次 LibKey 的 “Download PDF”，真正打开一次全文。")
            print("     —— 这样程序才能从代理地址里自动嗅出你学校的 proxy 后缀。")
            print("看到全文后回到这里按回车继续（配置与代理后缀会自动存下，之后 fetch 复用）。")
        else:
            print("\n浏览器已打开。请用你学校的方式登录，并【打开一次任意付费全文】：")
            print('  • 页面上的 "Log in" / "Access through your institution"（SSO / OpenAthens），或')
            print("  • 你们的 EZProxy 门户；如需校园网请先连 VPN。")
            print("看到全文后回到这里按回车继续（会自动嗅探并保存你学校的 proxy 后缀）。")
        try:
            input("> 完成后按回车… ")
        except EOFError:
            pass

        # Gather cookies + every open tab's URL, so a proxied full-text tab reveals
        # the institution's proxy suffix automatically (no hardcoding, any school).
        cookies: list = []
        urls: list[str] = []
        try:
            cookies = ctx.cookies()
        except Exception:
            pass
        for p in getattr(ctx, "pages", []) or []:
            try:
                urls.append(p.url)
            except Exception:
                pass

        # Save cookies for the optional requests fast-path.
        try:
            cf = config.cookie_file()
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        suffix, changed = config.update_proxy_suffix_from_session(cookies, urls)
        print(f"\n已保存登录会话（{len(cookies)} cookies）到浏览器 profile。")
        if suffix:
            print(f"已自动嗅探到 EZProxy 代理后缀: {suffix}" + ("（已更新）" if changed else ""))
        else:
            print("未嗅探到代理后缀（可能你不是 EZProxy，或本次没打开付费全文）。")
            print("若确有全文,请重跑并【务必点开一次付费文章全文】后再按回车。")
        print("现在可以运行：")
        print("  paper-extract fetch --collection <name> --output-format both --access library")
        return True
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def fetch_json_library(store, article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    from .. import assemble as assemble_mod
    from ..sources.fulltext import fulltext_fetcher, fulltext_sources
    from .libkey import staged_extension

    ids = article.get("identifiers") or {}
    doi = (ids.get("doi") or "").strip()
    pmid = (ids.get("pmid") or "").strip()

    ctx = _get_context()
    reasons: list[str] = []

    # Primary (matches the proven old step2 approach): navigate through the EZProxy
    # `login?url=` entry in the live session, extract rendered HTML sections, and
    # fall back to an in-page PDF fetch. No host-rewrite (that tripped reCAPTCHA).
    if ctx is not None and doi:
        parsed, tag, reason = _fetch_institutional(doi, ctx, wait_login=False)
        if parsed is not None:
            prov = {"access_source": tag, "source_endpoint": "institutional_libproxy",
                    "fulltext_url": _login_url_for(f"https://doi.org/{doi}"),
                    "accessed_at": fulltext_fetcher._now()}
            updated = assemble_mod.assemble_from_parsed(article, parsed, prov)
            if updated is not None:
                return updated, ""
            reasons.append("institutional_reject")
        else:
            reasons.append(reason or "institutional_no_fulltext")
    else:
        reasons.append("library_browser_unavailable" if doi else "no_doi_for_library")

    # Secondary: let LibKey resolve the authenticated PDF, then parse it.
    if ctx is not None and staged_extension() is not None and (doi or pmid):
        page = ctx.new_page()
        try:
            pdf, url, reason = _fetch_pdf_via_libkey(page, doi, pmid)
        finally:
            try:
                page.close()
            except Exception:
                pass
        if pdf:
            updated = _apply_pdf_to_article(article, pdf, url, "libkey")
            if updated is not None:
                return updated, ""
            reasons.append("libkey_pdf_parse_reject")
        else:
            reasons.append(f"libkey:{reason}")

    # Fallback: cookie + requests EZProxy adapters.
    flat, warning = assemble_mod.flatten_article(article)
    doc, reason = fulltext_sources.get_fulltext(flat, sources=["ezproxy_html", "ezproxy_pdf"])
    updated = assemble_mod.assemble_from_doc(article, doc)
    if updated is not None:
        return updated, ""
    reasons.append(f"cookie:{reason}")
    return None, "; ".join(x for x in reasons if x)


def fetch_pdf_library(store, article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    from .. import article as article_mod
    from .. import links as links_mod
    from .libkey import staged_extension

    ids = article.get("identifiers") or {}
    doi = (ids.get("doi") or "").strip()
    pmid = (ids.get("pmid") or "").strip()
    if not doi and not pmid:
        return None, "no_doi_for_library"

    ctx = _get_context()
    if ctx is None:
        return None, "library_browser_unavailable"

    pdf = None
    pdf_url = ""
    page = ctx.new_page()
    try:
        # Primary: navigate through the EZProxy `login?url=` entry, then in-page PDF fetch.
        if doi:
            url = _login_url_for(f"https://doi.org/{doi}") or f"https://doi.org/{doi}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
            except Exception:
                pass
            _wait_cloudflare(page)
            time.sleep(2)
            if _pause_for_user_if_blocked(page):
                _wait_cloudflare(page)
                time.sleep(2)
            pdf, pdf_url = _pdf_via_inpage_fetch(page, doi)
        # Secondary: LibKey-resolved PDF.
        if not pdf and staged_extension() is not None:
            pdf, pdf_url, _reason = _fetch_pdf_via_libkey(page, doi, pmid)
    finally:
        try:
            page.close()
        except Exception:
            pass

    if not pdf:
        return None, "library_pdf_not_found"
    path = store.pdf_path(article["article_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    rel = str(path.relative_to(store.article_dir(article["article_id"])))
    article_mod.record_pdf(article, rel, "library")
    if pdf_url:
        article.setdefault("links", {}).setdefault("library", {})["pdf"] = pdf_url
    links_mod.mark_sensitive_links(article)
    return article, ""
