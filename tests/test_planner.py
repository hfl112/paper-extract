"""Offline tests for deterministic query construction and no-llm planning."""
from __future__ import annotations

import pytest

from paper_extract.search.planner import (
    _combine_blocks,
    _epmc_block,
    build_epmc_query,
    build_plan,
)


def test_epmc_block_quotes_and_escapes():
    b = _epmc_block(['whole genome doubling', 'WGD'])
    assert 'TITLE:"whole genome doubling"' in b
    assert 'ABSTRACT:"WGD"' in b
    assert b.startswith("(") and b.endswith(")")


def test_combine_blocks_all_and():
    assert _combine_blocks(["A", "B", "C"], 3) == "A AND B AND C"


def test_combine_blocks_m_of_n():
    out = _combine_blocks(["A", "B", "C"], 2)
    assert out == "((A AND B) OR (A AND C) OR (B AND C))"


def test_build_epmc_query_anchor_and_keys():
    anchor_map = {"cancer": ["cancer", "tumor"]}
    key_map = {"WGD": ["whole genome doubling"], "aneuploidy": ["aneuploidy"]}
    q = build_epmc_query(anchor_map, key_map, match_n=1)
    # anchor is AND-ed, keys are OR-combined at match=1
    assert " AND " in q
    assert q.count("OR") >= 1


def test_build_plan_no_llm_keyword_mode():
    plan = build_plan(keywords=["WGD", "cancer"], anchors=["cancer"], no_llm=True, no_confirm=True)
    assert plan["input"]["mode"] == "keyword"
    assert plan["anchors"] == ["cancer"]
    assert plan["keys"] == ["WGD"]
    assert plan["aliases"] == {"cancer": ["cancer"], "WGD": ["WGD"]}
    assert plan["queries"]["epmc"] and plan["queries"]["pubmed"]
    assert plan["llm"]["used"] is False


def test_build_plan_prompt_requires_llm():
    with pytest.raises(ValueError):
        build_plan(prompt="anything", no_llm=True)
