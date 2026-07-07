"""Offline tests for the fetch transport seam (Phase 2).

get_fulltext / download_pdf accept an injected HttpClient, so the full success
path — fetch, parse, build, quality gate — runs with zero network via a fake
adapter. No test here touches urllib.
"""
from __future__ import annotations

from paper_extract.sources.fulltext import fulltext_sources as fs

_BODY_PARA = (
    "This introduction describes, at length and entirely for the purposes of an "
    "offline test fixture, the background and motivation of a fabricated study. "
    "It contains more than five hundred characters so that the extraction quality "
    "gate treats the body as substantial rather than too short, which keeps the "
    "quality status out of the reject bucket and lets get_fulltext return the "
    "assembled document to its caller exactly as it would for a real article."
)

_JATS = (
    "<pmc-articleset><article>"
    "<front><article-meta>"
    '<article-id pub-id-type="pmc">123</article-id>'
    "<title-group><article-title>Fake Offline Study</article-title></title-group>"
    "<abstract><p>An abstract for the fixture.</p></abstract>"
    "</article-meta></front>"
    "<body>"
    f"<sec><title>Introduction</title><p>{_BODY_PARA}</p></sec>"
    "<sec><title>Methods</title><p>We fabricated methods for the parser test.</p></sec>"
    "<sec><title>Results</title><p>The results were as fabricated for the fixture.</p></sec>"
    "</body>"
    "<back><ref-list><ref><element-citation>"
    "<article-title>A cited reference</article-title></element-citation></ref></ref-list></back>"
    "</article></pmc-articleset>"
).encode("utf-8")


class FakeClient:
    """A test HttpClient. Records requested URLs and returns canned responses;
    anything unmapped is a 404 / None so tests can't silently reach the network."""

    def __init__(self, gets=None, blobs=None):
        self.gets = gets or {}      # url-substring -> (code, bytes)
        self.blobs = blobs or {}    # url-substring -> bytes (browser)
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=60, retries=3):
        self.calls.append(url)
        for frag, (code, body) in self.gets.items():
            if frag in url:
                return code, body, ""
        return 404, b"", "HTTP 404"

    def browser_get(self, url, referer=None, timeout=40):
        self.calls.append(url)
        for frag, body in self.blobs.items():
            if frag in url:
                return body
        return None

    def browser_get_url(self, url, referer=None, timeout=40):
        b = self.browser_get(url, referer, timeout)
        return (url, b) if b is not None else (None, None)


def test_get_fulltext_parses_via_fake_client_offline():
    client = FakeClient(gets={"db=pmc": (200, _JATS)})
    doc, reason = fs.get_fulltext(
        {"pmcid": "PMC123", "title": "seed"}, sources=["pmc_xml"], client=client,
    )
    assert reason == ""
    assert doc is not None
    assert "Introduction" in doc["sections"]
    assert doc["quality"]["quality_status"] != "reject"
    assert doc["provenance"]["access_source"] == "pmc_xml"
    # proves it went through the injected client, not the network
    assert client.calls and all("efetch" in c or "db=pmc" in c for c in client.calls)


def test_get_fulltext_reports_reason_when_source_misses():
    client = FakeClient(gets={})  # everything 404s
    doc, reason = fs.get_fulltext(
        {"pmcid": "PMC999"}, sources=["pmc_xml"], client=client,
    )
    assert doc is None
    assert "pmc_xml" in reason


def test_download_pdf_via_fake_client_offline():
    pdf_bytes = b"%PDF-1.4 fake pdf body"
    client = FakeClient(blobs={"pdf=render": pdf_bytes})
    pdf, url = fs.download_pdf({"pmcid": "PMC123"}, client=client)
    assert pdf == pdf_bytes
    assert url == "epmc_render:PMC123"


def test_download_pdf_returns_none_when_no_mirror():
    client = FakeClient(blobs={})
    pdf, url = fs.download_pdf({"pmcid": "PMC123"}, client=client)
    assert pdf is None


def test_client_restored_after_call():
    before = fs._client
    fs.get_fulltext({"pmcid": "PMC123"}, sources=["pmc_xml"], client=FakeClient())
    assert fs._client is before  # seam swap is scoped to the call
