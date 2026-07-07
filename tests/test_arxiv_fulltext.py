"""Offline tests for the arXiv full-text adapter (fetch layer).

arXiv is no longer a search source — OpenAlex indexes arXiv, so those papers
arrive with a 10.48550/arXiv.* DOI. This adapter fetches their body, keyed on
that DOI (like the bioRxiv adapter keys on 10.1101). No network here.
"""
from __future__ import annotations

from paper_extract.sources.fulltext import fulltext_sources as fs


def test_arxiv_id_from_doi():
    assert fs._arxiv_id_from_doi("10.48550/arXiv.2201.00978") == "2201.00978"
    assert fs._arxiv_id_from_doi("10.48550/arxiv.2102.05095") == "2102.05095"
    assert fs._arxiv_id_from_doi("10.1038/s41586-021-03534-y") == ""
    assert fs._arxiv_id_from_doi("") == ""


def test_arxiv_registered_in_priority_after_biorxiv():
    assert "arxiv" in fs.ADAPTERS
    adapter, applies, _endpoint = fs.ADAPTERS["arxiv"]
    assert applies({"doi": "10.48550/arxiv.2201.00978"}) is True
    assert applies({"doi": "10.1101/2020.01.01.000000"}) is False
    assert "arxiv" in fs.PRIORITY and fs.PRIORITY.index("arxiv") > fs.PRIORITY.index("biorxiv")


class _FakeClient:
    """browser_get returns preset bytes for any URL; records requested URLs."""
    def __init__(self, blob=b""):
        self.blob = blob
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=60, retries=3):
        return 404, b"", "HTTP 404"

    def browser_get(self, url, referer=None, timeout=40):
        self.calls.append(url)
        return self.blob

    def browser_get_url(self, url, referer=None, timeout=40):
        return url, self.blob


def test_arxiv_adapter_hits_arxiv_pdf_url_offline():
    # non-%PDF body -> clean failure, but proves the adapter targeted arxiv.org/pdf/<id>
    client = _FakeClient(blob=b"<html>not a pdf</html>")
    doc, reason = fs.get_fulltext(
        {"doi": "10.48550/arxiv.2201.00978"}, sources=["arxiv"], client=client,
    )
    assert doc is None
    assert "arxiv" in reason
    assert client.calls == ["https://arxiv.org/pdf/2201.00978"]


def test_arxiv_adapter_skipped_for_non_arxiv_doi():
    client = _FakeClient(blob=b"%PDF-1.4 x")
    doc, reason = fs.get_fulltext(
        {"doi": "10.1038/s41586-021-03534-y"}, sources=["arxiv"], client=client,
    )
    assert doc is None
    assert client.calls == []  # applies() gate skipped it, no fetch attempted
