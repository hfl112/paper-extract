#!/usr/bin/env python3
"""Step 4 smoke test: search-plan + standalone llmclient, offline.

Real alias generation needs an API key, so that is handed off for manual testing.
This checks everything offline:
  * llmclient imports and resolves providers without any SDK installed
  * `search-plan --no-llm` builds a plan, writes plan log, sets current_plan
  * anchor + M-of-N query construction is present in the generated query
  * `search-plan --prompt` without a provider fails with a CLEAR error (not a crash)

Usage:
    python tests/smoke_step4.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTION = "smoke_step4"
COLL_DIR = PROJECT_ROOT / "data" / "collections" / COLLECTION


def run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    # Ensure no provider is configured for the "clear error" check.
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-m", "paper_extract", *args],
                          cwd="/tmp", env=env, capture_output=True, text=True)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    if COLL_DIR.exists():
        shutil.rmtree(COLL_DIR)

    # 1. llmclient importable without SDKs
    r = subprocess.run([sys.executable, "-c", "import llmclient; print(llmclient.available_providers())"],
                       capture_output=True, text=True,
                       env={**os.environ, "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""})
    check("llmclient imports standalone", r.returncode == 0, r.stderr)

    # 2. search-plan --no-llm builds a plan offline
    r = run_cli("search-plan", "--collection", COLLECTION,
                "--keyword", "WGD", "--keyword", "cancer", "--keyword", "aneuploidy",
                "--anchor", "cancer", "--match", "1", "--no-llm", "--no-confirm")
    check("search-plan --no-llm runs", r.returncode == 0, r.stderr)

    plans = sorted((COLL_DIR / "logs").glob("plan_*.json"))
    check("plan log written", len(plans) >= 1)
    plan = json.loads(plans[-1].read_text())
    check("anchors captured", plan["anchors"] == ["cancer"], str(plan.get("anchors")))
    check("keys captured", set(plan["keys"]) == {"WGD", "aneuploidy"}, str(plan.get("keys")))
    check("match_n = 1", plan["match_n"] == 1, str(plan.get("match_n")))
    check("epmc query built", bool(plan["queries"]["epmc"]))
    check("M-of-N present in query", " OR " in plan["queries"]["epmc"] and " AND " in plan["queries"]["epmc"])
    check("llm not used", plan["llm"]["used"] is False)

    coll = json.loads((COLL_DIR / "collection.json").read_text())
    check("current_plan updated", coll.get("current_plan", "").startswith("logs/plan_"), str(coll.get("current_plan")))

    # 3. --prompt without a provider fails clearly
    r = run_cli("search-plan", "--collection", COLLECTION, "--prompt", "whole genome doubling in cancer")
    check("prompt-without-provider exits nonzero", r.returncode != 0)
    check("error mentions provider", "provider" in (r.stderr + r.stdout).lower(), r.stderr[-300:])

    print("\nAll Step 4 offline smoke checks passed.")


if __name__ == "__main__":
    main()
