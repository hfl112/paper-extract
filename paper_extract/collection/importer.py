from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .. import article as article_mod
from ..article import new_article
from ..sources.search import europepmc_fetcher
from ..time import utc_now
from .store import CollectionStore


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)


def _read_input(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("10."):
            rows.append({"doi": value})
        elif value.isdigit():
            rows.append({"pmid": value})
        else:
            rows.append({"title": value})
    return rows


def _read_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("articles"), list):
        return data["articles"]
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"Unsupported JSON shape: {path}")


def _expand_pdfs(paths: list[str] | None) -> list[Path]:
    """Turn --input-pdf values into a flat, ordered list of .pdf files.

    A value may be a single PDF or a directory (expanded to its *.pdf, non-recursive)."""
    out: list[Path] = []
    for value in paths or []:
        path = Path(value).expanduser()
        if path.is_dir():
            out.extend(sorted(path.glob("*.pdf")))
        else:
            out.append(path)
    return out


def _pymupdf_available() -> bool:
    try:  # same fallback order as _extract_pdf_seed
        import pymupdf  # type: ignore # noqa: F401
        return True
    except Exception:
        try:
            import fitz  # type: ignore # noqa: F401
            return True
        except Exception:
            return False


def _find_doi(text: str) -> str:
    match = _DOI_RE.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}>\"'").strip()


def _extract_pdf_seed(path: Path) -> dict[str, Any]:
    """Best-effort metadata from a local PDF: DOI from body text, title from
    embedded metadata. Falls back to the filename stem so the article_id never
    collapses to the shared 'unknown' hash. Degrades cleanly without pymupdf."""
    text = ""
    embedded_title = ""
    pymupdf = None
    try:  # pymupdf >= 1.24 exposes the top-level name; older builds only expose fitz
        import pymupdf  # type: ignore
    except Exception:
        try:
            import fitz as pymupdf  # type: ignore
        except Exception:
            pymupdf = None
    if pymupdf is not None:
        try:
            with pymupdf.open(path) as doc:
                embedded_title = ((doc.metadata or {}).get("title") or "").strip()
                for index, page in enumerate(doc):
                    if index >= 3:  # DOI, if present, is on the first pages
                        break
                    text += page.get_text()
        except Exception:
            pass

    seed: dict[str, Any] = {}
    doi = _find_doi(text)
    if doi:
        seed["doi"] = doi
    clean_title = "" if embedded_title.lower().startswith("microsoft word") else embedded_title
    if doi:
        if clean_title:
            seed["title"] = clean_title
    else:
        seed["title"] = clean_title or path.stem
    return seed


def import_pdf(store: CollectionStore, path: Path) -> dict[str, Any]:
    """Import one local PDF: derive/enrich metadata, upsert the article, then
    copy the file in and mark it available. Returns a log item dict."""
    if not path.is_file():
        return {"article_id": "", "status": "failed", "reason": "file_not_found", "attempts": []}
    try:
        article = new_article(_enrich(_extract_pdf_seed(path)))
        if article_mod.metadata_found(article):
            article_mod.mark_metadata(article, found=True, sources=["import"])
        article = store.upsert_article(article)  # merge onto any existing article first
        article_id = article["article_id"]
        dest = store.pdf_path(article_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        rel = str(dest.relative_to(store.article_dir(article_id)))
        article_mod.record_pdf(article, rel, "import")
        store.write_article(article)  # write_article, not upsert: merge_article drops files/source
        return {"article_id": article_id, "status": "succeeded", "attempts": []}
    except Exception as e:
        return {"article_id": "", "status": "failed", "reason": type(e).__name__, "attempts": []}


def _enrich(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("title") and (row.get("doi") or row.get("pmid")):
        return row
    doi = (row.get("doi") or "").strip()
    pmid = (row.get("pmid") or "").strip()
    query = ""
    if doi:
        query = f'DOI:"{doi}"'
    elif pmid:
        query = f'EXT_ID:{pmid}'
    if not query:
        return row
    try:
        docs = europepmc_fetcher.search_europepmc(query, max_results=1)
    except Exception:
        return row
    if not docs:
        return row
    enriched = dict(row)
    for k, v in docs[0].items():
        if v not in (None, "", [], {}):
            enriched.setdefault(k, v)
            if not enriched.get(k):
                enriched[k] = v
    return enriched


def import_articles(
    store: CollectionStore,
    *,
    input_path: str | None = None,
    input_json: str | None = None,
    input_doi: list[str] | None = None,
    input_pmid: list[str] | None = None,
    input_pdf: list[str] | None = None,
) -> Path:
    started = utc_now()
    rows: list[dict[str, Any]] = []
    if input_path:
        rows.extend(_read_input(Path(input_path)))
    if input_json:
        rows.extend(_read_json(Path(input_json)))
    for doi in input_doi or []:
        rows.append({"doi": doi})
    for pmid in input_pmid or []:
        rows.append({"pmid": pmid})
    pdf_paths = _expand_pdfs(input_pdf)
    if not rows and not pdf_paths:
        raise ValueError("No import input provided")
    if pdf_paths and not _pymupdf_available():
        print('Note: pymupdf not installed — using filenames as titles (no DOI extraction). '
              'For metadata from PDF contents: pip install ".[pdf]"')

    items = []
    succeeded = failed = 0
    for row in rows:
        try:
            enriched = _enrich(row)
            article = new_article(enriched)
            if article_mod.metadata_found(article):
                article_mod.mark_metadata(article, found=True, sources=["import"])
            else:
                article_mod.mark_metadata(article, found=False)
            store.upsert_article(article)
            status = "succeeded" if article_mod.metadata_found(article) else "failed"
            succeeded += int(status == "succeeded")
            failed += int(status == "failed")
            items.append({"article_id": article["article_id"], "status": status, "attempts": []})
        except Exception as e:
            failed += 1
            items.append({"article_id": "", "status": "failed", "reason": type(e).__name__, "attempts": []})
    for pdf_path in pdf_paths:
        item = import_pdf(store, pdf_path)
        succeeded += int(item["status"] == "succeeded")
        failed += int(item["status"] == "failed")
        items.append(item)
    articles = store.iter_articles()
    store.write_articles_csv(articles)
    store.update_stats(articles)
    total = len(rows) + len(pdf_paths)
    return store.write_log(
        "import",
        {
            "input": input_path or "",
            "input_json": input_json or "",
            "input_doi": input_doi or [],
            "input_pmid": input_pmid or [],
            "input_pdf": [str(p) for p in pdf_paths],
        },
        {"total": total, "succeeded": succeeded, "failed": failed, "skipped": 0},
        items,
        started,
    )
