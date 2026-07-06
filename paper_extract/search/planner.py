"""Search-plan builder.

The LLM only proposes aliases (and, in --prompt mode, decomposes the question into
anchor/key concepts). Query strings are assembled deterministically:
  * synonyms of one concept -> OR
  * anchor concepts -> always AND (mandatory)
  * key concepts -> M-of-N (at least `match` of them must hit)

The LLM is reached through the standalone `llmclient` package, so any of
Gemini / OpenAI / DeepSeek / Claude works. `--no-llm` (keyword mode only) skips it.
"""
from __future__ import annotations

import json
import re
import sys
from itertools import combinations

from ..time import utc_now


# ── LLM prompts ────────────────────────────────────────────────────────────────
ALIAS_PROMPT = """\
You are a biomedical literature search assistant.

The keywords below will be combined with AND into a single literature search query
(synonyms within each keyword joined by OR, different keywords joined by AND).
So interpret EACH keyword IN THE CONTEXT of the others to resolve ambiguity.
Example: "WGD" alone is ambiguous, but "WGD" together with "cancer" clearly means
"whole genome doubling" (cancer genomics), NOT "whole-genome duplication".

For each keyword, list the aliases used in scientific paper titles and abstracts. Cover:
- the ORIGINAL keyword itself,
- the full expansion of an acronym AND the acronym of a full name (both directions),
- common abbreviations,
- hyphenated vs non-hyphenated spelling,
- British vs US spelling (e.g. "tumour" AND "tumor"),
- widely-used field synonyms for the SAME concept.

Hard rules:
- ONLY include terms that REALLY appear in the literature. Do NOT invent variants.
- Do NOT list plural forms separately — singular only.
- Stay STRICTLY on the same concept; no broader paraphrases.
- Aim for the genuinely common forms — typically 3-8 per keyword.
- Put the most standard / canonical term FIRST in each list.

Keywords (to be AND-combined):
{keywords}

Return ONLY this JSON (no markdown, no code fences), preserving the keywords as given:
{{
  "<keyword1>": ["canonical term", "alias", ...],
  "<keyword2>": ["canonical term", "alias", ...]
}}
"""

DECOMPOSE_PROMPT = """\
You are a biomedical literature search assistant. Turn the user's question into
search concepts for a Europe PMC / PubMed query.

Split the question into:
- "anchors": the MUST-HAVE concepts (always AND-ed into the query),
- "keys": supporting concepts (optional / M-of-N).

Keep each concept a short noun phrase as it would appear in titles/abstracts.
Use 1-3 anchors and 0-4 keys. Do not expand synonyms here (that is a later step).

Question:
{prompt}

Return ONLY this JSON (no markdown, no code fences):
{{"anchors": ["..."], "keys": ["..."]}}
"""


# ── deterministic query construction ─────────────────────────────────────────────
def _esc(term: str) -> str:
    return term.replace('"', '\\"')


def _combine_blocks(blocks: list[str], match_n: int) -> str:
    if match_n >= len(blocks):
        return " AND ".join(blocks)
    combos = ["(" + " AND ".join(c) + ")" for c in combinations(blocks, match_n)]
    return "(" + " OR ".join(combos) + ")"


def _epmc_block(aliases: list[str]) -> str:
    terms = []
    for a in aliases:
        terms.append(f'TITLE:"{_esc(a)}"')
        terms.append(f'ABSTRACT:"{_esc(a)}"')
    return "(" + " OR ".join(terms) + ")"


def _pubmed_block(aliases: list[str]) -> str:
    return "(" + " OR ".join(f'"{_esc(a)}"[tiab]' for a in aliases) + ")"


def _assemble(anchor_blocks: list[str], key_blocks: list[str], match_n: int) -> str:
    parts = list(anchor_blocks)
    if key_blocks:
        parts.append(_combine_blocks(key_blocks, match_n))
    return " AND ".join(parts)


def build_epmc_query(anchor_map, key_map, match_n: int) -> str:
    return _assemble([_epmc_block(a) for a in anchor_map.values()],
                     [_epmc_block(a) for a in key_map.values()], match_n)


def build_pubmed_query(anchor_map, key_map, match_n: int) -> str:
    return _assemble([_pubmed_block(a) for a in anchor_map.values()],
                     [_pubmed_block(a) for a in key_map.values()], match_n)


# ── LLM-backed helpers ────────────────────────────────────────────────────────────
def _call_llm(prompt: str, *, provider=None, model=None) -> str:
    from llmclient import call_llm  # imported lazily; separate package

    return call_llm(prompt, provider=provider, model=model, json=True, task_level="standard")


def find_aliases(keywords: list[str], *, provider=None, model=None) -> dict[str, list[str]]:
    prompt = ALIAS_PROMPT.format(keywords="\n".join(f"- {k}" for k in keywords))
    raw = _call_llm(prompt, provider=provider, model=model)
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    data = json.loads(raw)
    out: dict[str, list[str]] = {}
    for k in keywords:
        aliases = data.get(k) or [k]
        if k not in aliases:
            aliases = [k] + aliases
        seen, uniq = set(), []
        for a in aliases:
            a = a.strip()
            if a and a.lower() not in seen:
                seen.add(a.lower())
                uniq.append(a)
        out[k] = uniq
    return out


def decompose_prompt(prompt: str, *, provider=None, model=None) -> tuple[list[str], list[str]]:
    raw = _call_llm(DECOMPOSE_PROMPT.format(prompt=prompt), provider=provider, model=model)
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    data = json.loads(raw)
    anchors = [a.strip() for a in (data.get("anchors") or []) if a.strip()]
    keys = [k.strip() for k in (data.get("keys") or []) if k.strip()]
    return anchors, keys


def confirm_aliases(alias_map: dict[str, list[str]], anchors: set) -> dict[str, list[str]]:
    """Interactive review loop. Enter=confirm, `3,7`=delete, `+kw:alias`=add, q=quit."""
    while True:
        print("=" * 72)
        print("Aliases per concept (OR within a concept, AND across):\n")
        idx = 1
        index_to_pair: dict[int, tuple[str, str]] = {}
        for kw, aliases in alias_map.items():
            tag = "   <- required (anchor)" if kw in anchors else ""
            print(f"{kw}:{tag}")
            for a in aliases:
                print(f"  [{idx}] {a}")
                index_to_pair[idx] = (kw, a)
                idx += 1
            print()
        print("Enter=confirm  |  3,7=delete by index  |  +kw:alias=add  |  q=quit")
        ans = input("> ").strip()
        if ans.lower() == "q":
            raise SystemExit("cancelled")
        if not ans:
            break
        if ans.startswith("+"):
            for spec in ans[1:].split(";"):
                spec = spec.strip()
                if ":" in spec:
                    kw, alias = (s.strip() for s in spec.split(":", 1))
                    if kw in alias_map and alias and alias.lower() not in {a.lower() for a in alias_map[kw]}:
                        alias_map[kw].append(alias)
            continue
        drop = {int(x) for x in ans.split(",") if x.strip().isdigit()}
        if drop:
            pruned = {kw: [] for kw in alias_map}
            for i, (kw, a) in index_to_pair.items():
                if i not in drop:
                    pruned[kw].append(a)
            if any(not lst for lst in pruned.values()):
                print("  That would empty a concept; undone.\n")
                continue
            alias_map = pruned
    return alias_map


# ── public entry ───────────────────────────────────────────────────────────────
def build_plan(
    *,
    keywords: list[str] | None = None,
    anchors: list[str] | None = None,
    prompt: str | None = None,
    min_year: str | None = None,
    max_year: str | None = None,
    match: int | None = None,
    no_llm: bool = False,
    no_confirm: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    keywords = [k.strip() for k in (keywords or []) if k.strip()]
    anchors = [a.strip() for a in (anchors or []) if a.strip()]
    if bool(keywords) == bool(prompt):
        raise ValueError("Provide exactly one of --keyword or --prompt")
    if prompt and no_llm:
        raise ValueError("--prompt cannot be used with --no-llm")

    llm_used = False
    if prompt:
        mode = "prompt"
        anchor_terms, key_terms = decompose_prompt(prompt, provider=provider, model=model)
        llm_used = True
        if not anchor_terms and not key_terms:
            raise ValueError("LLM returned no concepts for the prompt")
    else:
        mode = "keyword"
        anchor_terms = anchors
        key_terms = [k for k in keywords if k not in anchors]
        if not anchor_terms and not key_terms:
            key_terms = keywords

    all_terms = anchor_terms + key_terms
    if not no_llm:
        alias_map = find_aliases(all_terms, provider=provider, model=model)
        llm_used = True
        if not no_confirm and sys.stdin.isatty():
            alias_map = confirm_aliases(alias_map, set(anchor_terms))
    else:
        alias_map = {t: [t] for t in all_terms}

    anchor_map = {t: alias_map.get(t, [t]) for t in anchor_terms}
    key_map = {t: alias_map.get(t, [t]) for t in key_terms}
    match_n = match if match is not None else len(key_map)
    match_n = max(1, min(match_n, len(key_map))) if key_map else 0

    return {
        "schema_version": "1.0",
        "input": {"mode": mode, "keywords": keywords, "anchors": anchor_terms,
                  "prompt": prompt or "", "no_llm": no_llm},
        "year_filter": {"min_year": min_year, "max_year": max_year},
        "aliases": alias_map,
        "anchors": anchor_terms,
        "keys": key_terms,
        "match_n": match_n,
        "queries": {
            "epmc": build_epmc_query(anchor_map, key_map, match_n),
            "pubmed": build_pubmed_query(anchor_map, key_map, match_n),
        },
        "llm": {"provider": provider or "", "used": llm_used},
        "created_at": utc_now(),
    }
