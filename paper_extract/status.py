from __future__ import annotations

import collections
from pathlib import Path

from . import article as article_mod
from .collection import CollectionStore
from .time import utc_now


def status_report(store: CollectionStore) -> Path:
    started = utc_now()
    articles = store.iter_articles()
    total = len(articles)
    status_counts = collections.Counter()
    kind_counts = collections.Counter()
    quality_counts = collections.Counter()
    source_counts = collections.Counter()
    failed = []
    for article in articles:
        st = article.get("status") or {}
        meta = article.get("metadata") or {}
        quality = article.get("quality") or {}
        source = article.get("source") or {}
        for key in ("metadata", "fulltext", "pdf"):
            status_counts[f"{key}:{st.get(key, 'unknown')}"] += 1
        kind_counts[meta.get("article_kind", "unknown")] += 1
        quality_counts[quality.get("status") or "unknown"] += 1
        if source.get("fulltext"):
            source_counts[f"fulltext:{source['fulltext']}"] += 1
        if source.get("pdf"):
            source_counts[f"pdf:{source['pdf']}"] += 1
        if st.get("fulltext") == article_mod.FAILED or st.get("pdf") == article_mod.FAILED:
            failed.append(article.get("article_id", ""))

    summary = {
        "total": total,
        "succeeded": total,
        "failed": 0,
        "skipped": 0,
        "status_counts": dict(status_counts),
        "article_kind": dict(kind_counts),
        "quality": dict(quality_counts),
        "sources": dict(source_counts),
        "failed_articles": failed[:100],
    }
    print(f"Collection: {store.name}")
    print(f"Articles: {total}")
    print(f"Metadata available: {status_counts.get(f'metadata:{article_mod.FOUND}', 0)}")
    print(f"Fulltext available: {status_counts.get(f'fulltext:{article_mod.AVAILABLE}', 0)}")
    print(f"PDF available: {status_counts.get(f'pdf:{article_mod.AVAILABLE}', 0)}")
    print(f"Article kinds: {dict(kind_counts)}")
    print(f"Quality: {dict(quality_counts)}")
    print(f"Sources: {dict(source_counts)}")
    if failed:
        print(f"Failed/incomplete articles: {len(failed)}")
    log = store.write_log("status", {}, summary, [], started)
    store.update_stats(articles)
    return log
