"""Offline tests for sensitive-link detection and redaction."""
from __future__ import annotations

from paper_extract.fetch.links import (
    is_sensitive_url,
    mark_sensitive_links,
    redact_sensitive_links,
)


def test_is_sensitive_url():
    assert is_sensitive_url("https://link-springer-com.libproxy.myuni.edu/article/x", proxy_suffix="libproxy.myuni.edu")
    assert is_sensitive_url("https://foo.ezproxy.bar.edu/x")
    assert is_sensitive_url("https://publisher.com/read?token=abc123")
    assert is_sensitive_url("https://idp.uni.edu/login?ticket=zzz")
    assert not is_sensitive_url("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/")
    assert not is_sensitive_url("https://doi.org/10.1/x")
    assert not is_sensitive_url("")


def _article_with_links():
    return {
        "article_id": "hash_x",
        "links": {
            "pmc": {"page": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/"},
            "publisher": {"page": "https://link-springer-com.libproxy.myuni.edu/article/x",
                          "pdf": "https://link.springer.com/x.pdf?token=secret"},
        },
    }


def test_mark_sensitive_links():
    art = _article_with_links()
    mark_sensitive_links(art, proxy_suffix="libproxy.myuni.edu")
    assert art["links"]["publisher"]["sensitive"] is True
    assert "sensitive" not in art["links"]["pmc"]


def test_redact_sensitive_links():
    art = _article_with_links()
    red = redact_sensitive_links(art, proxy_suffix="libproxy.myuni.edu")
    # sensitive urls removed
    assert "page" not in red["links"]["publisher"]
    assert "pdf" not in red["links"]["publisher"]
    # clean pmc link kept
    assert red["links"]["pmc"]["page"].endswith("PMC1/")
    # original untouched
    assert art["links"]["publisher"]["page"].startswith("https://link-springer")
