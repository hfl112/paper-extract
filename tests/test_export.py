"""Golden export tests (Phase 4).

Pins the exact bib / ris / csv output for a fixture collection so the
citation_view refactor is proven byte-identical (read_text applies universal
newline translation, matching how the strings were captured).
"""
from __future__ import annotations

import pytest

from paper_extract import article as A
from paper_extract.collection.store import CollectionStore
from paper_extract.export import bib, csv_export, ris


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paper_extract.collection.store.collections_root", lambda: tmp_path / "collections"
    )
    s = CollectionStore.open("golden")
    s.upsert_article(A.new_article({
        "doi": "10.1/abc", "pmid": "111", "title": "First Paper",
        "authors": ["Doe J", "Roe R"], "journal": "J Test", "pub_year": 2021,
        "pub_types": ["Journal Article"], "keywords": ["alpha", "beta"],
        "abstract": "Abstract one.",
    }))
    s.upsert_article(A.new_article({
        "doi": "10.2/xyz", "title": "Second Paper", "authors": ["Sng A"],
        "journal": "Nat Test", "pub_year": 2022, "pub_types": ["Review"],
        "abstract": "Abstract two.",
    }))
    return s


_BIB = (
    "@article{doi_10_1_abc,\n  title = {First Paper},\n  author = {Doe J and Roe R},\n"
    "  journal = {J Test},\n  year = {2021},\n  doi = {10.1/abc},\n  pmid = {111}\n}\n\n"
    "@article{doi_10_2_xyz,\n  title = {Second Paper},\n  author = {Sng A},\n"
    "  journal = {Nat Test},\n  year = {2022},\n  doi = {10.2/xyz}\n}\n"
)

_RIS = (
    "TY  - JOUR\nTI  - First Paper\nAU  - Doe J\nAU  - Roe R\nJO  - J Test\nT2  - J Test\n"
    "PY  - 2021\nDO  - 10.1/abc\nAB  - Abstract one.\nKW  - alpha\nKW  - beta\n"
    "UR  - https://doi.org/10.1/abc\nER  - \n\n"
    "TY  - JOUR\nTI  - Second Paper\nAU  - Sng A\nJO  - Nat Test\nT2  - Nat Test\n"
    "PY  - 2022\nDO  - 10.2/xyz\nAB  - Abstract two.\nUR  - https://doi.org/10.2/xyz\nER  - \n"
)

_CSV = (
    "title,authors,journal,pub_year,doi,pmid,pmcid,article_kind,keywords,abstract,url\n"
    "First Paper,Doe J; Roe R,J Test,2021,10.1/abc,111,,research,alpha; beta,Abstract one.,https://doi.org/10.1/abc\n"
    "Second Paper,Sng A,Nat Test,2022,10.2/xyz,,,review,,Abstract two.,https://doi.org/10.2/xyz\n"
)


def test_bib_golden(store, tmp_path):
    p = bib.export_bib(store, str(tmp_path / "g.bib"))
    assert p.read_text(encoding="utf-8") == _BIB


def test_ris_golden(store, tmp_path):
    p = ris.export_ris(store, str(tmp_path / "g.ris"))
    assert p.read_text(encoding="utf-8") == _RIS


def test_csv_golden(store, tmp_path):
    p = csv_export.export_csv(store, str(tmp_path / "g.csv"))
    assert p.read_text(encoding="utf-8") == _CSV
