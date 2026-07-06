from __future__ import annotations

import copy
import json
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from ..paths import library_config_path


# Query-string keys that indicate a per-session credential/signature.
_SENSITIVE_QUERY_KEYS = {
    "token", "access_token", "auth", "authtoken", "ticket", "sig", "signature",
    "session", "sessionid", "jsessionid", "sid", "key", "apikey", "api_key",
    "shibboleth", "wayf", "saml", "sso", "code", "state",
}

# Substrings in the host that indicate an institutional proxy / login route.
_SENSITIVE_HOST_MARKERS = ("ezproxy", "libproxy", "idp.", "shibboleth", "openathens", "/login")

# Link-value keys inside each links bucket that hold URLs.
_URL_KEYS = ("page", "pdf", "xml", "html")


def _configured_proxy_suffix() -> str:
    path = library_config_path()
    if not path.exists():
        return ""
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("proxy_suffix") or "").strip().lower()
    except Exception:
        return ""


def is_sensitive_url(url: str, proxy_suffix: str | None = None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    suffix = (proxy_suffix if proxy_suffix is not None else _configured_proxy_suffix())
    if suffix and suffix in host:
        return True
    if any(marker in host for marker in _SENSITIVE_HOST_MARKERS):
        return True
    if "/login" in (parsed.path or "").lower():
        return True
    keys = {k.lower() for k in parse_qs(parsed.query)}
    return bool(keys & _SENSITIVE_QUERY_KEYS)


def iter_link_urls(article: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Yield (bucket, key, url) for every URL stored under article['links']."""
    for bucket, entry in (article.get("links") or {}).items():
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if key in _URL_KEYS and isinstance(value, str) and value:
                yield bucket, key, value


def mark_sensitive_links(article: dict[str, Any], proxy_suffix: str | None = None) -> dict[str, Any]:
    """Flag any links bucket that holds a sensitive URL with sensitive=True (in place)."""
    suffix = proxy_suffix if proxy_suffix is not None else _configured_proxy_suffix()
    buckets_with_sensitive = {
        bucket for bucket, _key, url in iter_link_urls(article) if is_sensitive_url(url, suffix)
    }
    for bucket, entry in (article.get("links") or {}).items():
        if not isinstance(entry, dict):
            continue
        if bucket in buckets_with_sensitive:
            entry["sensitive"] = True
        else:
            entry.pop("sensitive", None)
    return article


def redact_sensitive_links(article: dict[str, Any], proxy_suffix: str | None = None) -> dict[str, Any]:
    """Return a deep copy with sensitive URL values removed (for export)."""
    suffix = proxy_suffix if proxy_suffix is not None else _configured_proxy_suffix()
    out = copy.deepcopy(article)
    for bucket, entry in (out.get("links") or {}).items():
        if not isinstance(entry, dict):
            continue
        for key in list(entry.keys()):
            if key in _URL_KEYS and isinstance(entry[key], str) and is_sensitive_url(entry[key], suffix):
                entry.pop(key, None)
        entry.pop("sensitive", None)
    return out
