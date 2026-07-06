from __future__ import annotations

import json
from pathlib import Path

from ..collection import CollectionStore
from ..fetch.links import redact_sensitive_links


def export_jsonl(store: CollectionStore, output: str | None = None) -> Path:
    """Export a collection as JSONL — one full article.json per line.

    Sensitive (proxy/token) links are redacted. This is the LLM/RAG-friendly form.
    """
    path = Path(output) if output else Path.cwd() / f"{store.name}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for article in store.iter_articles():
            safe = redact_sensitive_links(article)
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    return path
