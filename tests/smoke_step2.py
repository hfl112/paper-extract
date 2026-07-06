#!/usr/bin/env python3
"""Step 2 smoke test: fetch orchestration, offline.

Seeds articles WITHOUT identifiers (so open sources fail with no network calls),
then verifies:
  * fetch CLI runs and writes a fetch log
  * --force on an article that already has good full text does NOT destroy it
    when the re-fetch fails (the data-loss bug fix)
  * an article with nothing fetchable is marked failed cleanly (no crash)
  * --access both (non-interactive) prints the notice, then either records
    open->library attempts (configured machine) or fails fast with guidance
    (fresh checkout)
  * sensitive links are flagged in article.json

Usage:
    python tests/smoke_step2.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from paper_extract.collection import CollectionStore  # noqa: E402
from paper_extract.fetch.links import mark_sensitive_links  # noqa: E402
from paper_extract.schema import new_article  # noqa: E402

COLLECTION = "smoke_step2"
COLL_DIR = PROJECT_ROOT / "data" / "collections" / COLLECTION


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "paper_extract", *args],
        cwd="/tmp", env=env, capture_output=True, text=True,
    )


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(1)


def seed() -> tuple[str, str]:
    if COLL_DIR.exists():
        shutil.rmtree(COLL_DIR)
    store = CollectionStore.open(COLLECTION)

    # Article A: already has good full text + a sensitive publisher link.
    a = new_article({"title": "Kept Body Article"})
    a["sections"] = {"abstract": "the abstract", "results": "IMPORTANT PRESERVED BODY"}
    a["status"]["fulltext"] = "available"
    a["quality"] = {"status": "ok", "body_chars": 999, "section_count": 2, "issues": [], "warnings": []}
    a["links"]["publisher"]["page"] = "https://link-springer-com.libproxy.myuni.edu/article/x"
    mark_sensitive_links(a, proxy_suffix="libproxy.myuni.edu")
    store.write_article(a)

    # Article B: title only, nothing fetchable.
    b = new_article({"title": "No Ids Here Article"})
    store.write_article(b)

    store.write_articles_csv()
    store.update_stats()
    return a["article_id"], b["article_id"]


def main() -> None:
    aid, bid = seed()

    # Sensitive link persisted on disk
    a_disk = json.loads((COLL_DIR / "articles" / aid / "article.json").read_text())
    check("sensitive link flagged in article.json", a_disk["links"]["publisher"].get("sensitive") is True)

    # fetch --force json: open fetch fails offline; A must keep its body.
    r = run_cli("fetch", "--collection", COLLECTION, "--output-format", "json", "--force")
    check("fetch --force runs", r.returncode == 0, r.stderr)

    a_after = json.loads((COLL_DIR / "articles" / aid / "article.json").read_text())
    check("force-fail preserved body", a_after["sections"].get("results") == "IMPORTANT PRESERVED BODY",
          str(a_after["sections"]))
    check("force-fail kept status available", a_after["status"]["fulltext"] == "available",
          a_after["status"]["fulltext"])

    b_after = json.loads((COLL_DIR / "articles" / bid / "article.json").read_text())
    check("no-id article marked failed", b_after["status"]["fulltext"] == "failed", b_after["status"]["fulltext"])

    # fetch log exists and records attempts
    logs = sorted((COLL_DIR / "logs").glob("fetch_*.json"))
    check("fetch log written", len(logs) >= 1)
    last = json.loads(logs[-1].read_text())
    check("fetch log has summary", set(last["summary"]) >= {"total", "succeeded", "failed", "skipped"})

    # --access both, non-interactive. Two valid outcomes depending on the machine:
    #   configured library (dev box)  -> runs; log records open then library attempts
    #   fresh checkout (no session)   -> fails fast with actionable guidance
    r = run_cli("fetch", "--collection", COLLECTION, "--output-format", "json",
                "--access", "both", "--force", "--non-interactive")
    check("library notice printed", "Library access may open a browser" in r.stdout, r.stdout[:200])
    if r.returncode == 0:
        logs = sorted((COLL_DIR / "logs").glob("fetch_*.json"))
        both_log = json.loads(logs[-1].read_text())
        routes = {a2["access"] for item in both_log["items"] for a2 in item.get("attempts", [])}
        check("both tried open and library", {"open", "library"} <= routes, str(routes))
    else:
        check("unconfigured library fails fast with guidance",
              "library access not ready" in r.stderr and "library login" in r.stderr, r.stderr)

    print("\nAll Step 2 smoke checks passed.")


if __name__ == "__main__":
    main()
