"""Borrow institutional-access cookies from the user's real Chrome.

Rationale: institutional access often lives in the user's normal Chrome (via a
Lean Library / LibKey extension or an existing Shibboleth/OpenAthens SSO session).
Chrome 136+ blocks attaching a debugger to the live default profile, so instead
we read the already-established cookies (decrypted via the macOS Keychain by
browser_cookie3) and reuse them. Only academic-access-relevant hosts are stored.
"""
from __future__ import annotations

import json

from . import config

CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# Persist only cookies for hosts relevant to institutional access (privacy: the
# rest of your Chrome cookies are never read to disk unless --all-domains).
_ACADEMIC_MARKERS = (
    "wiley", "sciencedirect", "elsevier", "springer", "nature", "tandfonline",
    "sagepub", "oup.com", "academic.oup", "ahajournals", "cell.com", "nejm",
    "bmj", "jamanetwork", "acs.org", "rsc.org", "ieee", "pnas", "science.org",
    "cambridge.org", "karger", "thelancet", "annualreviews", "jstor", "aacrjournals",
    "shibboleth", "openathens", "idp.", "ezproxy", "libproxy", "leanlibrary",
    "third-iron", "libkey", ".edu",
)


def _relevant(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(m in d for m in _ACADEMIC_MARKERS)


def import_chrome_cookies(all_domains: bool = False) -> int:
    """Read Chrome cookies and save the academic-access ones. Returns the count.

    Raises whatever browser_cookie3 raises (e.g. locked DB while Chrome runs).
    """
    import browser_cookie3

    jar = browser_cookie3.chrome()
    records: list[dict] = []
    for c in jar:
        if not all_domains and not _relevant(c.domain):
            continue
        records.append({"name": c.name, "value": c.value,
                        "domain": c.domain, "path": c.path or "/"})
    cf = config.cookie_file()
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({"cookies": records, "user_agent": CHROME_UA},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    return len(records)


def load_cookie_records() -> list[dict]:
    cf = config.cookie_file()
    if not cf.exists():
        return []
    try:
        raw = json.loads(cf.read_text(encoding="utf-8"))
    except Exception:
        return []
    return raw["cookies"] if isinstance(raw, dict) else raw


def playwright_cookies() -> list[dict]:
    """Borrowed cookies in Playwright/cloakbrowser add_cookies() shape."""
    out = []
    for c in load_cookie_records():
        if not c.get("name"):
            continue
        out.append({"name": c["name"], "value": c.get("value", ""),
                    "domain": c.get("domain", ""), "path": c.get("path", "/")})
    return out
