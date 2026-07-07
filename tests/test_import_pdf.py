"""Offline tests for `collection import --input-pdf` (local-PDF import).

Machine-independent: no network (Europe PMC enrichment is monkeypatched), and
the pymupdf-present path is skipif-guarded so the suite passes on a core-only
install where the fallback (filename stem) path is what runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from paper_extract.collection import importer
from paper_extract.collection.store import CollectionStore


def _has_pymupdf() -> bool:
    try:
        import pymupdf  # noqa: F401
        return True
    except Exception:
        try:
            import fitz  # noqa: F401
            return True
        except Exception:
            return False


# --- _find_doi (pure) -------------------------------------------------------

def test_find_doi_extracts_and_strips_trailing_punctuation():
    assert importer._find_doi("see doi: 10.1234/abc.def-1, cited") == "10.1234/abc.def-1"
    assert importer._find_doi("(10.5555/j.xyz.2020.01.001).") == "10.5555/j.xyz.2020.01.001"


def test_find_doi_empty_when_absent():
    assert importer._find_doi("no identifiers here") == ""
    assert importer._find_doi("") == ""


# --- _expand_pdfs (filesystem, tmp only) ------------------------------------

def test_expand_pdfs_mixes_files_and_directories(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    (d / "b.pdf").write_bytes(b"%PDF-1.4 b")
    (d / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (d / "notes.txt").write_text("not a pdf")
    single = tmp_path / "single.pdf"
    single.write_bytes(b"%PDF-1.4 s")

    out = importer._expand_pdfs([str(d), str(single)])
    # directory expands to its *.pdf sorted; non-pdf ignored; order preserved
    assert out == [d / "a.pdf", d / "b.pdf", single]


def test_expand_pdfs_empty_input():
    assert importer._expand_pdfs(None) == []
    assert importer._expand_pdfs([]) == []


# --- _extract_pdf_seed: no-pymupdf fallback ---------------------------------

def test_extract_pdf_seed_falls_back_to_stem_without_pymupdf(tmp_path, monkeypatch):
    # Force-block both import names so the fallback runs even where pymupdf
    # is installed (None in sys.modules makes `import x` raise ImportError).
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)
    pdf = tmp_path / "My Interesting Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")

    seed = importer._extract_pdf_seed(pdf)
    assert seed == {"title": "My Interesting Paper"}


# --- _extract_pdf_seed: pymupdf path (skipped on core-only installs) --------

@pytest.mark.skipif(not _has_pymupdf(), reason="pymupdf not installed (core-only env)")
def test_extract_pdf_seed_reads_doi_with_pymupdf(tmp_path):
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf
    pdf = tmp_path / "paper.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A study of things. doi: 10.1234/test.42")
    doc.save(pdf)
    doc.close()

    seed = importer._extract_pdf_seed(pdf)
    assert seed.get("doi") == "10.1234/test.42"


# --- import_pdf end-to-end (offline, fallback path) --------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paper_extract.collection.store.collections_root", lambda: tmp_path / "collections"
    )
    return CollectionStore.open("t")


def test_import_pdf_offline_copies_file_and_marks_available(store, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)
    # keep enrichment offline
    monkeypatch.setattr(importer.europepmc_fetcher, "search_europepmc", lambda *a, **k: [])
    src = tmp_path / "Local Paper.pdf"
    src.write_bytes(b"%PDF-1.4 body")

    item = importer.import_pdf(store, src)

    assert item["status"] == "succeeded"
    article = store.read_article(item["article_id"])
    assert article["metadata"]["title"] == "Local Paper"
    assert article["status"]["pdf"] == "available"
    assert article["source"]["pdf"] == "import"
    pdf_on_disk = store.article_dir(item["article_id"]) / article["files"]["pdf"]
    assert pdf_on_disk.read_bytes() == b"%PDF-1.4 body"


def test_import_pdf_missing_file_fails_cleanly(store):
    item = importer.import_pdf(store, Path("/nonexistent/nope.pdf"))
    assert item["status"] == "failed"
    assert item["reason"] == "file_not_found"


def test_import_articles_accepts_input_pdf_and_logs_it(store, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)
    monkeypatch.setattr(importer.europepmc_fetcher, "search_europepmc", lambda *a, **k: [])
    src = tmp_path / "One.pdf"
    src.write_bytes(b"%PDF-1.4 one")

    log_path = importer.import_articles(store, input_pdf=[str(src)])

    import json
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["summary"]["total"] == 1
    assert log["summary"]["succeeded"] == 1
    assert log["args"]["input_pdf"] == [str(src)]


def test_import_articles_requires_some_input(store):
    with pytest.raises(ValueError):
        importer.import_articles(store)
