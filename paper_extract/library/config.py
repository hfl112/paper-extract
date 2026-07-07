"""Institution-agnostic library (EZProxy) configuration.

Nothing about any specific school is hardcoded. On first login the proxy suffix
is auto-detected from the browser session's cookies/URL and persisted; it is
re-checked on every login and updated if it changes.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from ..paths import library_config_path
from ..time import utc_now

DEFAULT_LOGIN_MARKERS = ["/login", "shibboleth", "wayf", "sso", "idp.", "openathens"]
_PROXY_HOST_HINTS = ("ezproxy", "libproxy", "proxy", "openathens")

# Default page opened by `library login` so the user can authenticate however
# their institution works (EZProxy portal, "Access through your institution" SSO,
# OpenAthens, or VPN). Overridable via config or --landing-url.
#
# Chosen to be a genuinely PAYWALLED article (Unpaywall is_oa=false) on a publisher
# with gentler anti-bot than Wiley/Elsevier (Springer), so a successful login is
# visible: full text only appears once authenticated. Override with --landing-url.
DEFAULT_LOGIN_LANDING_URL = "https://link.springer.com/article/10.1007/s12288-025-02175-9"


def load_config() -> dict[str, Any]:
    path = library_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    path = library_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg["updated_at"] = utc_now()
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_proxy_suffix() -> str:
    return (load_config().get("proxy_suffix") or "").strip().lower()


def get_login_url_template() -> str:
    return (load_config().get("login_url_template") or "").strip()


def get_fingerprint_seed() -> int:
    """Stable stealth-fingerprint seed for the persistent browser profile.

    cloakbrowser can pick a random fingerprint on each launch. With a persistent
    browser profile, that can invalidate challenge-clearance cookies because the
    saved cookie appears to come from a different device on the next run.
    Persist one seed so login and fetch reuse the same browser fingerprint.
    """
    cfg = load_config()
    seed = cfg.get("fingerprint_seed")
    if isinstance(seed, int) and 10000 <= seed <= 99999:
        return seed
    import random

    seed = random.randint(10000, 99999)
    cfg["fingerprint_seed"] = seed
    save_config(cfg)
    return seed


def get_login_landing_url() -> str:
    return (load_config().get("login_landing_url") or DEFAULT_LOGIN_LANDING_URL).strip()


def get_login_markers() -> list[str]:
    return load_config().get("login_markers") or DEFAULT_LOGIN_MARKERS


def cookie_file():
    """Cookie jar path, resolved via the project data root (gitignored)."""
    return library_config_path().parent / "library_cookies.json"


def set_login_url_template(template: str) -> None:
    cfg = load_config()
    cfg["login_url_template"] = template.strip()
    cfg.setdefault("login_markers", DEFAULT_LOGIN_MARKERS)
    save_config(cfg)


def detect_proxy_suffix(cookies: list[dict[str, Any]] | None, urls: Any = None) -> str | None:
    """Infer the EZProxy suffix automatically from a logged-in session.

    Looks at every cookie domain AND the host of every open tab's URL. EZProxy
    rewrites hosts to <publisher>.<proxy-suffix> and plants a session cookie on
    the proxy domain, so once the user has actually opened a full text through
    the proxy, the suffix (e.g. libproxy.myuni.edu) appears here. Returns None if
    nothing proxy-shaped is present — never guesses an unrelated domain.

    `urls` may be a single string or a list of strings.
    """
    candidates: list[str] = []
    for c in cookies or []:
        dom = (c.get("domain") or "").lstrip(".").strip().lower()
        if dom:
            candidates.append(dom)
    if isinstance(urls, str):
        urls = [urls]
    for u in urls or []:
        host = (urlparse(u).hostname or "").lower()
        if host:
            candidates.append(host)

    # ONLY accept domains that actually look like an institutional proxy host.
    # Never fall back to an arbitrary (e.g. ad-tracker) domain.
    hinted = [d for d in candidates if any(h in d for h in _PROXY_HOST_HINTS)]
    if not hinted:
        return None
    # The bare proxy domain is the shortest hinted host; for a rewritten host like
    # link-springer-com.libproxy.myuni.edu, drop the leading publisher label.
    best = min(hinted, key=len)
    parts = best.split(".")
    if len(parts) > 3 and not any(h in parts[0] for h in _PROXY_HOST_HINTS):
        best = ".".join(parts[1:])
    return best or None


def update_proxy_suffix_from_session(cookies, urls: Any = None) -> tuple[str | None, bool]:
    """Detect and persist the proxy suffix. Returns (suffix, changed)."""
    detected = detect_proxy_suffix(cookies, urls)
    if not detected:
        return get_proxy_suffix() or None, False
    current = get_proxy_suffix()
    if detected == current:
        return detected, False
    cfg = load_config()
    cfg["proxy_suffix"] = detected
    save_config(cfg)
    return detected, True
