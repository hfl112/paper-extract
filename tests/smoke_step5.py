#!/usr/bin/env python3
"""Step 5 smoke test: multi-format export (bib/ris/csv/jsonl), offline.

Seeds one fully-populated article (title, authors, doi, abstract, keywords, and a
SENSITIVE proxy link), exports every format via the CLI, and verifies:
  * each command runs and writes a file to the CWD
  * RIS is well-formed (TY.../ER), CSV has the header+row, JSONL parses
  * the sensitive proxy link is redacted from JSONL (and never appears in exports)

Usage:
    python tests/smoke_step5.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from paper_extract.collection import CollectionStore  # noqa: E402
from paper_extract.fetch.links import mark_sensitive_links  # noqa: E402
from paper_extract.schema import new_article  # noqa: E402

COLLECTION = "smoke_step5"
COLL_DIR = PROJECT_ROOT / "data" / "collections" / COLLECTION
OUT = Path(tempfile.mkdtemp(prefix="pe_step5_"))
SENSITIVE = "https://link-springer-com.libproxy.myuni.edu/article/x"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    return subprocess.run([sys.executable, "-m", "paper_extract", *args],
                          cwd="/tmp", env=env, capture_output=True, text=True)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(1)


def seed() -> None:
    if COLL_DIR.exists():
        shutil.rmtree(COLL_DIR)
    store = CollectionStore.open(COLLECTION)
    a = new_article({
        "doi": "10.1002/pbc.21508", "title": "Whole Genome Doubling in Cancer",
        "authors": ["Smith J", "Doe A"], "journal": "Pediatr Blood Cancer",
        "pub_year": 2020, "keywords": ["WGD", "aneuploidy"],
        "abstract": "An informative abstract about WGD.",
    })
    a["links"]["publisher"]["page"] = SENSITIVE
    mark_sensitive_links(a, proxy_suffix="libproxy.myuni.edu")
    store.write_article(a)
    store.write_articles_csv()
    store.update_stats()


def main() -> None:
    seed()
    for fmt, ext in (("bib", "bib"), ("ris", "ris"), ("csv", "csv"), ("jsonl", "jsonl")):
        out = OUT / f"{COLLECTION}.{ext}"
        r = run_cli("collection", "export", "--collection", COLLECTION, "--to", fmt, "--output", str(out))
        check(f"export --to {fmt} runs", r.returncode == 0, r.stderr)
        check(f"{fmt} file created", out.exists())
        text = out.read_text()
        check(f"{fmt} not empty", len(text.strip()) > 0)
        check(f"{fmt} has no sensitive link", SENSITIVE not in text, "leaked proxy URL!")

    # RIS well-formed
    ris = (OUT / f"{COLLECTION}.ris").read_text()
    check("RIS has TY and ER", "TY  - JOUR" in ris and "ER  - " in ris)
    check("RIS has DOI + author", "DO  - 10.1002/pbc.21508" in ris and "AU  - Smith J" in ris)

    # CSV header + row
    csv_lines = (OUT / f"{COLLECTION}.csv").read_text().splitlines()
    check("CSV has header", csv_lines[0].startswith("title,authors,journal"))
    check("CSV has a data row", any("Whole Genome Doubling" in ln for ln in csv_lines[1:]))

    # JSONL parses, redacted, keeps clean doi url
    jl = (OUT / f"{COLLECTION}.jsonl").read_text().splitlines()
    obj = json.loads(jl[0])
    check("JSONL parses to article", obj.get("article_id", "").startswith("doi_"))
    check("JSONL redacted sensitive page", "page" not in (obj.get("links") or {}).get("publisher", {}))

    print("\nAll Step 5 smoke checks passed.")
    print(f"(exports in {OUT})")


if __name__ == "__main__":
    main()
