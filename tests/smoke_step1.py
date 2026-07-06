#!/usr/bin/env python3
"""Step 1 smoke test: offline CLI end-to-end + CWD-independence.

Runs `python -m paper_extract` from a DIFFERENT working directory (/tmp) to prove
that collections still resolve to the project's data/ root (paths.py fix), then
checks that every command runs, logs are written, and expected files exist.

Usage:
    python tests/smoke_step1.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTION = "smoke_step1"
COLL_DIR = PROJECT_ROOT / "data" / "collections" / COLLECTION


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the CLI from /tmp with the project on PYTHONPATH."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    import os

    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "paper_extract", *args],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
    )


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    # Clean slate
    import shutil

    if COLL_DIR.exists():
        shutil.rmtree(COLL_DIR)

    # 1. --help for every command
    for cmd in (["--help"], ["search-plan", "--help"], ["search", "--help"],
                ["fetch", "--help"], ["status", "--help"],
                ["collection", "import", "--help"], ["collection", "export", "--help"]):
        r = run_cli(*cmd)
        check(f"--help runs: {' '.join(cmd)}", r.returncode == 0, r.stderr)

    # 2. import a local CSV (title+doi present -> no network needed)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, dir="/tmp") as f:
        f.write("title,doi,journal,pub_year,authors\n")
        f.write('"Whole genome doubling in cancer",10.1002/pbc.21508,"Pediatr Blood Cancer",2020,"Smith J; Doe A"\n')
        csv_path = f.name
    r = run_cli("collection", "import", "--collection", COLLECTION, "--input", csv_path)
    check("collection import runs", r.returncode == 0, r.stderr)

    # 3. status
    r = run_cli("status", "--collection", COLLECTION)
    check("status runs", r.returncode == 0, r.stderr)

    # 4. export bib (to /tmp so we don't clutter the repo)
    out_bib = "/tmp/smoke_step1.bib"
    r = run_cli("collection", "export", "--collection", COLLECTION, "--to", "bib", "--output", out_bib)
    check("export bib runs", r.returncode == 0, r.stderr)

    # 5. File/structure assertions (CWD-independence: written under PROJECT_ROOT, not /tmp)
    check("collection.json exists under project root", (COLL_DIR / "collection.json").exists())
    check("articles.csv exists", (COLL_DIR / "articles.csv").exists())
    articles = list((COLL_DIR / "articles").glob("*/article.json"))
    check("article.json created", len(articles) >= 1, f"found {len(articles)}")
    check("NOT written under /tmp cwd", not (Path("/tmp") / "data" / "collections" / COLLECTION).exists())

    logs = list((COLL_DIR / "logs").glob("*.json"))
    log_cmds = {json.loads(p.read_text()).get("command") for p in logs}
    check("import log written", "import" in log_cmds, str(log_cmds))
    check("status log written", "status" in log_cmds, str(log_cmds))

    check("bib export file exists", Path(out_bib).exists())
    check("bib has an entry", "@article{" in Path(out_bib).read_text())

    art = json.loads(articles[0].read_text())
    check("article has schema_version", art.get("schema_version") == "1.0")
    check("article_id is doi-based", art["article_id"].startswith("doi_"), art["article_id"])

    print("\nAll Step 1 smoke checks passed.")


if __name__ == "__main__":
    main()
