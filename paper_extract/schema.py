"""Backward-compatible re-export shim.

The article.json schema and its state logic now live in `paper_extract.article`
(the Article module). This module is kept so existing `from ..schema import …`
imports keep working; no schema knowledge or status strings live here.
"""
from __future__ import annotations

from .article import (  # noqa: F401
    SCHEMA_VERSION,
    article_kind,
    merge_article,
    new_article,
)
