#!/usr/bin/env python3
"""Step 3 smoke test: library wiring + de-hardcoding, offline.

Real browser login and institutional fetch need credentials and cloakbrowser, so
those are handed off for manual testing. This checks everything that CAN run offline:
  * `library` / `library login` CLI parse & --help
  * no institution is hardcoded: a fresh root starts with empty proxy/login config
  * `fetch --access library --non-interactive` without a configured session fails
    FAST with actionable guidance (never hangs waiting for a human or a browser)
  * proxy_suffix is auto-captured to library.json and updated when it changes

Uses an isolated PAPER_EXTRACT_ROOT so nothing touches real data/library.json.

Usage:
    python tests/smoke_step3.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = Path(tempfile.mkdtemp(prefix="pe_step3_"))
COLLECTION = "smoke_step3"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PAPER_EXTRACT_ROOT": str(TMP_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "paper_extract", *args],
        cwd="/tmp", env=env, capture_output=True, text=True,
    )


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    # 1. CLI parse
    check("library --help runs", run_cli("library", "--help").returncode == 0)
    check("library login --help runs", run_cli("library", "login", "--help").returncode == 0)

    # 2. De-hardcoding: a fresh root has no institution baked in — proxy suffix and
    #    login template start empty. (Docs/comments may use libproxy.myuni.edu as an
    #    example; what matters is that no default value ships in the config.)
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PAPER_EXTRACT_ROOT": str(TMP_ROOT)}
    r = subprocess.run(
        [sys.executable, "-c",
         "import paper_extract.library.config as c;"
         "print('FRESH', repr(c.get_proxy_suffix()), repr(c.get_login_url_template()))"],
        cwd="/tmp", env=env, capture_output=True, text=True)
    check("no institution hardcoded (fresh config is empty)",
          "FRESH '' ''" in r.stdout, r.stdout + r.stderr)

    # 3. Seed a collection (offline: title+doi present so import skips network)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, dir="/tmp") as f:
        f.write("title,doi\n")
        f.write('"Library Path Article",10.1007/s00261-021-03301-7\n')
        csv_path = f.name
    r = run_cli("collection", "import", "--collection", COLLECTION, "--input", csv_path)
    check("import runs (temp root)", r.returncode == 0, r.stderr)

    # 4. fetch --access library, non-interactive, no session configured: must fail
    #    FAST with actionable guidance (never hang waiting for a human / a browser).
    r = run_cli("fetch", "--collection", COLLECTION, "--output-format", "json",
                "--access", "library", "--non-interactive")
    check("library notice printed", "Library access may open a browser" in r.stdout)
    check("unconfigured library fails fast", r.returncode != 0)
    # Stable contract regardless of WHY it's not ready (no browser extra vs no
    # session): the message names the problem and says it won't auto-log-in.
    check("failure carries guidance", "library access not ready" in r.stderr
          and "Non-interactive fetch cannot log in" in r.stderr, r.stderr)

    # 5. proxy_suffix auto-capture + update, written to library.json under temp root
    snippet = (
        "import paper_extract.library.config as c;"
        "c.set_login_url_template('https://libproxy.myuni.edu/login?url={target}');"
        "s,ch=c.update_proxy_suffix_from_session([{'name':'e','value':'1','domain':'.libproxy.myuni.edu'}],'');"
        "print('SUFFIX',s,ch);"
        "s2,ch2=c.update_proxy_suffix_from_session([{'name':'e','value':'1','domain':'.ezproxy.other.ac.uk'}],'');"
        "print('SUFFIX2',s2,ch2)"
    )
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PAPER_EXTRACT_ROOT": str(TMP_ROOT)}
    r = subprocess.run([sys.executable, "-c", snippet], cwd="/tmp", env=env, capture_output=True, text=True)
    check("config snippet runs", r.returncode == 0, r.stderr)
    check("suffix auto-captured", "SUFFIX libproxy.myuni.edu True" in r.stdout, r.stdout)
    check("suffix updated on change", "SUFFIX2 ezproxy.other.ac.uk True" in r.stdout, r.stdout)
    cfg = json.loads((TMP_ROOT / "data" / "library.json").read_text())
    check("library.json has proxy_suffix", cfg.get("proxy_suffix") == "ezproxy.other.ac.uk", str(cfg))
    check("library.json has login template", "{target}" in cfg.get("login_url_template", ""))

    print("\nAll Step 3 offline smoke checks passed.")
    print(f"(temp root: {TMP_ROOT})")


if __name__ == "__main__":
    main()
