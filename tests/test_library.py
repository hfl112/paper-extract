"""Offline tests for institution-agnostic library config + proxy rewriting."""
from __future__ import annotations

import json

import paper_extract.library.config as config
from paper_extract.sources.fulltext import library_download


def _point_config_at(tmp_path, monkeypatch):
    cfg_path = tmp_path / "library.json"
    monkeypatch.setattr(config, "library_config_path", lambda: cfg_path)
    return cfg_path


def test_load_config_tolerates_utf8_bom(tmp_path, monkeypatch):
    cfg_path = _point_config_at(tmp_path, monkeypatch)
    cfg_path.write_bytes(b"\xef\xbb\xbf" + b'{"proxy_suffix": "libproxy.myuni.edu"}')
    assert config.load_config().get("proxy_suffix") == "libproxy.myuni.edu"
    assert config.get_proxy_suffix() == "libproxy.myuni.edu"
    assert config.config_error() is None


def test_config_error_and_doctor_report_broken_json(tmp_path, monkeypatch):
    cfg_path = _point_config_at(tmp_path, monkeypatch)
    cfg_path.write_text("{ not: valid json,,, }", encoding="utf-8")
    err = config.config_error()
    assert err is not None and str(cfg_path) in err
    import paper_extract.library.browser as browser
    d = browser.doctor()
    assert d["ready"] is False
    assert d["reason"] == "config_error"
    assert str(cfg_path) in d["next_action"]
    assert d["checks"]["config"] == "parse_error"


def test_detect_proxy_suffix_from_cookie_domain():
    cookies = [{"name": "ezproxy", "value": "x", "domain": ".libproxy.myuni.edu"}]
    assert config.detect_proxy_suffix(cookies, "") == "libproxy.myuni.edu"


def test_detect_proxy_suffix_from_rewritten_host():
    cookies = [{"name": "s", "value": "x", "domain": "link-springer-com.libproxy.myuni.edu"}]
    assert config.detect_proxy_suffix(cookies, "") == "libproxy.myuni.edu"


def test_detect_proxy_suffix_ignores_non_proxy_domains():
    # Regression: must NOT pick an arbitrary (ad/tracker) domain when no proxy host exists.
    cookies = [{"name": "a", "value": "1", "domain": ".adnxs.com"},
               {"name": "b", "value": "2", "domain": ".nature.com"}]
    assert config.detect_proxy_suffix(cookies, "") is None
    # But still finds the real proxy when present alongside noise.
    cookies.append({"name": "c", "value": "3", "domain": ".libproxy.myuni.edu"})
    assert config.detect_proxy_suffix(cookies, "") == "libproxy.myuni.edu"


def test_update_persists_and_detects_change(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    suffix, changed = config.update_proxy_suffix_from_session(
        [{"name": "e", "value": "1", "domain": ".libproxy.myuni.edu"}], "")
    assert suffix == "libproxy.myuni.edu" and changed is True
    # unchanged on repeat
    _s, changed2 = config.update_proxy_suffix_from_session(
        [{"name": "e", "value": "1", "domain": ".libproxy.myuni.edu"}], "")
    assert changed2 is False
    # changed when a different institution appears
    s3, changed3 = config.update_proxy_suffix_from_session(
        [{"name": "e", "value": "1", "domain": ".ezproxy.other.ac.uk"}], "")
    assert s3 == "ezproxy.other.ac.uk" and changed3 is True
    saved = json.loads((tmp_path / "library.json").read_text())
    assert saved["proxy_suffix"] == "ezproxy.other.ac.uk"


def test_fingerprint_seed_is_persisted(tmp_path, monkeypatch):
    cfg_path = _point_config_at(tmp_path, monkeypatch)
    first = config.get_fingerprint_seed()
    second = config.get_fingerprint_seed()
    assert first == second
    assert 10000 <= first <= 99999
    saved = json.loads(cfg_path.read_text())
    assert saved["fingerprint_seed"] == first


def test_to_proxy_url_uses_configured_suffix():
    url = "https://link.springer.com/article/x"
    assert library_download.to_proxy_url(url, suffix="libproxy.myuni.edu") == \
        "https://link-springer-com.libproxy.myuni.edu/article/x"
    # no suffix -> unchanged (no institution hardcoded)
    assert library_download.to_proxy_url(url, suffix="") == url


def test_doctor_reports_needs_login_without_config(tmp_path, monkeypatch):
    # No proxy_suffix / profile -> not ready, with actionable next step (no browser opened).
    _point_config_at(tmp_path, monkeypatch)
    import paper_extract.library.browser as browser
    monkeypatch.setattr(browser, "_profile_dir", lambda: str(tmp_path / "profile"))
    d = browser.doctor()
    assert d["ready"] is False
    assert d["reason"] in ("browser_unavailable", "needs_login", "proxy_route_undetected", "config_error")
    assert "library login" in d["next_action"] or "browser" in d["next_action"]


def _pretend_cloakbrowser_installed(monkeypatch):
    import importlib.util as _ilu
    _orig = _ilu.find_spec
    monkeypatch.setattr(_ilu, "find_spec",
                        lambda name, *a, **k: True if name == "cloakbrowser" else _orig(name, *a, **k))


def test_doctor_proxy_route_undetected_when_logged_in(tmp_path, monkeypatch):
    # Profile exists (logged in) but no proxy_suffix/template detected -> a distinct,
    # actionable state, NOT "needs_login" (the SSO-first trap).
    _point_config_at(tmp_path, monkeypatch)
    _pretend_cloakbrowser_installed(monkeypatch)
    import paper_extract.library.browser as browser
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(browser, "_profile_dir", lambda: str(profile))
    d = browser.doctor()
    assert d["ready"] is False
    assert d["reason"] == "proxy_route_undetected"
    assert "--proxy-login-url" in d["next_action"] and "{target}" in d["next_action"]


def test_cmd_library_login_hint_when_route_undetected(monkeypatch, capsys):
    import argparse

    import paper_extract.cli as cli
    import paper_extract.library.browser as browser
    monkeypatch.setattr(browser, "library_login", lambda **kw: True)
    monkeypatch.setattr(browser, "doctor", lambda: {
        "ready": False, "reason": "proxy_route_undetected",
        "next_action": "logged in, but ... 'paper-extract library login --proxy-login-url \"...{target}\"' ...",
        "checks": {},
    })
    args = argparse.Namespace(from_chrome=False, landing_url=None, proxy_login_url=None,
                              headless=False, libkey=None, all_domains=False)
    cli.cmd_library_login(args)
    out = capsys.readouterr().out
    assert "captured session/cookies, but" in out
    assert "--proxy-login-url" in out


def test_prepare_session_non_interactive_fails_fast_when_not_ready(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    import paper_extract.library.browser as browser
    monkeypatch.setattr(browser, "_profile_dir", lambda: str(tmp_path / "profile"))
    import paper_extract.library as library
    ok, msg = library.prepare_session(interactive=False)
    assert ok is False
    assert "not ready" in msg and "cannot log in for you" in msg


def test_collect_pdf_urls_uses_citation_pdf_url(tmp_path, monkeypatch):
    # The real fix: publishers without a hardcoded path (e.g. AACR) are covered via
    # the page's <meta citation_pdf_url>, proxied so it stays in-session.
    _point_config_at(tmp_path, monkeypatch)
    config.save_config({"proxy_suffix": "libproxy.myuni.edu"})
    import paper_extract.library.browser as browser
    html = ('<html><head><meta name="citation_pdf_url" '
            'content="https://aacrjournals.org/mct/article-pdf/1/2/3/x.pdf"></head></html>')
    urls = browser._collect_pdf_urls(html, "https://aacrjournals-org.libproxy.myuni.edu/mct/article/1",
                                     "10.1158/1535-7163.mct-20-0394")
    assert any("aacrjournals-org.libproxy.myuni.edu" in u and u.endswith(".pdf") for u in urls), urls


def test_collect_pdf_urls_empty_without_meta_or_known_publisher():
    import paper_extract.library.browser as browser
    # No citation_pdf_url meta and a publisher we have no guessed path for -> no candidates.
    assert browser._collect_pdf_urls("<html></html>", "https://x", "10.1158/xxx") == []


def test_browser_module_imports_without_cloakbrowser():
    # Must import even when cloakbrowser is absent (all lazy). Do NOT launch a
    # browser here — _get_context() would spawn a real profile as a side effect.
    import paper_extract.library.browser as browser
    assert callable(browser.fetch_json_library)
    assert callable(browser.begin_live_session)
    assert callable(browser.library_login)
