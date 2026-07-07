from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..paths import collections_root
from ..schema import SCHEMA_VERSION, merge_article
from ..time import stamp_from_iso, utc_now

CSV_COLUMNS = [
    "article_id",
    "title",
    "authors",
    "journal",
    "pub_year",
    "doi",
    "pmid",
    "pmcid",
    "article_kind",
    "metadata_status",
    "fulltext_status",
    "pdf_status",
    "quality_status",
    "source_metadata",
    "source_fulltext",
    "source_pdf",
    "is_open_access",
    "pub_types",
]


class CollectionStore:
    def __init__(self, name: str):
        self.name = name
        self.root = collections_root() / name
        self.articles_dir = self.root / "articles"
        self.logs_dir = self.root / "logs"
        self.collection_path = self.root / "collection.json"
        self.csv_path = self.root / "articles.csv"

    @classmethod
    def open(cls, name: str) -> "CollectionStore":
        store = cls(name)
        store.ensure()
        return store

    def ensure(self) -> None:
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if not self.collection_path.exists():
            now = utc_now()
            self.write_collection(
                {
                    "schema_version": SCHEMA_VERSION,
                    "name": self.name,
                    "description": "",
                    "created_at": now,
                    "updated_at": now,
                    "current_plan": "",
                    "paths": {"articles": "articles", "logs": "logs", "articles_csv": "articles.csv"},
                    "stats": {"article_count": 0, "fulltext_count": 0, "pdf_count": 0},
                }
            )

    def read_collection(self) -> dict[str, Any]:
        return json.loads(self.collection_path.read_text(encoding="utf-8"))

    def write_collection(self, data: dict[str, Any]) -> None:
        data["updated_at"] = utc_now()
        self.collection_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_current_plan(self, relative_path: str) -> None:
        data = self.read_collection()
        data["current_plan"] = relative_path
        self.write_collection(data)

    def article_dir(self, article_id: str) -> Path:
        return self.articles_dir / article_id

    def article_path(self, article_id: str) -> Path:
        return self.article_dir(article_id) / "article.json"

    def pdf_path(self, article_id: str) -> Path:
        return self.article_dir(article_id) / "article.pdf"

    def read_article(self, article_id: str) -> dict[str, Any] | None:
        path = self.article_path(article_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def iter_articles(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.articles_dir.glob("*/article.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def upsert_article(self, article: dict[str, Any]) -> dict[str, Any]:
        article_id = article["article_id"]
        self.article_dir(article_id).mkdir(parents=True, exist_ok=True)
        existing = self.read_article(article_id)
        if existing:
            article = merge_article(existing, article)
        article["updated_at"] = utc_now()
        self.article_path(article_id).write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
        return article

    def write_article(self, article: dict[str, Any]) -> None:
        article_id = article["article_id"]
        self.article_dir(article_id).mkdir(parents=True, exist_ok=True)
        article["updated_at"] = utc_now()
        self.article_path(article_id).write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_articles_csv(self, articles: list[dict[str, Any]] | None = None) -> None:
        if articles is None:
            articles = self.iter_articles()
        rows = [self._csv_row(article) for article in articles]
        with self.csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def _csv_row(self, article: dict[str, Any]) -> dict[str, str]:
        meta = article.get("metadata") or {}
        ids = article.get("identifiers") or {}
        status = article.get("status") or {}
        source = article.get("source") or {}
        quality = article.get("quality") or {}
        return {
            "article_id": article.get("article_id", ""),
            "title": meta.get("title", ""),
            "authors": "; ".join(meta.get("authors") or []),
            "journal": meta.get("journal", ""),
            "pub_year": meta.get("pub_year", ""),
            "doi": ids.get("doi", ""),
            "pmid": ids.get("pmid", ""),
            "pmcid": ids.get("pmcid", ""),
            "article_kind": meta.get("article_kind", "unknown"),
            "metadata_status": status.get("metadata", ""),
            "fulltext_status": status.get("fulltext", ""),
            "pdf_status": status.get("pdf", ""),
            "quality_status": quality.get("status", ""),
            "source_metadata": "; ".join(source.get("metadata") or []),
            "source_fulltext": source.get("fulltext", ""),
            "source_pdf": source.get("pdf", ""),
            "is_open_access": meta.get("is_open_access", ""),
            "pub_types": "; ".join(meta.get("pub_types") or []),
        }

    def update_stats(self, articles: list[dict[str, Any]] | None = None) -> None:
        if articles is None:
            articles = self.iter_articles()
        from .. import article as article_mod

        data = self.read_collection()
        data["stats"] = {
            "article_count": len(articles),
            "fulltext_count": sum(1 for a in articles if article_mod.has_fulltext(a)),
            "pdf_count": sum(1 for a in articles if article_mod.has_pdf(a)),
        }
        self.write_collection(data)

    def write_log(self, command: str, args: dict[str, Any], summary: dict[str, Any], items: list[dict[str, Any]], started_at: str) -> Path:
        finished = utc_now()
        stamp = stamp_from_iso(finished)
        path = self.logs_dir / f"{command}_{stamp}.json"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": f"{command}_{stamp}",
            "command": command,
            "collection": self.name,
            "started_at": started_at,
            "finished_at": finished,
            "args": args,
            "summary": summary,
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_plan(self, plan: dict[str, Any], started_at: str) -> Path:
        finished = utc_now()
        stamp = stamp_from_iso(finished)
        path = self.logs_dir / f"plan_{stamp}.json"
        plan = dict(plan)
        plan.setdefault("schema_version", SCHEMA_VERSION)
        plan.setdefault("created_at", finished)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self.set_current_plan(str(path.relative_to(self.root)))
        return path
