from __future__ import annotations

import random
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..collection import CollectionStore
from ..time import utc_now
from .. import library
from . import artifacts, quality


# Per-article wait (seconds) between consecutive library-access articles, so rapid
# automated navigation doesn't look bot-like and re-trigger reCAPTCHA. Random jitter
# in normal/slow tiers makes the pacing less robotic.
SPEED_TIERS = {
    "fast": (8.0, 8.0),      # fixed 8s
    "normal": (5.0, 60.0),   # random 5–60s
    "slow": (50.0, 300.0),   # random 50–300s
}


LIBRARY_NOTICE = """Library access may open a browser and use your school/library login session.
Before continuing, switch to campus network or VPN if your library requires it.
You may need to complete username/password and MFA in the browser.
No credentials will be stored by this tool."""


def _access_routes(access: str) -> list[str]:
    if access == "open":
        return ["open"]
    if access == "library":
        return ["library"]
    return ["open", "library"]  # both: open first, library fallback


def _json_fetcher(route: str) -> Callable[[CollectionStore, dict[str, Any]], tuple[dict[str, Any] | None, str]]:
    if route == "open":
        return lambda store, article: artifacts.fetch_json_open(article)
    return library.fetch_json_library


def _pdf_fetcher(route: str) -> Callable[[CollectionStore, dict[str, Any]], tuple[dict[str, Any] | None, str]]:
    if route == "open":
        return artifacts.fetch_pdf_open
    return library.fetch_pdf_library


def run_fetch(
    store: CollectionStore,
    *,
    output_format: str,
    access: str = "open",
    limit: int | None = None,
    force: bool = False,
    interactive: bool | None = None,
    speed: str = "fast",
) -> Path:
    if output_format not in {"json", "pdf", "both"}:
        raise ValueError("--output-format must be json, pdf, or both")
    if access not in {"open", "library", "both"}:
        raise ValueError("--access must be open, library, or both")
    if speed not in SPEED_TIERS:
        raise ValueError(f"--speed must be one of {', '.join(SPEED_TIERS)}")
    if interactive is None:
        interactive = sys.stdin.isatty()
    if access in {"library", "both"}:
        print(LIBRARY_NOTICE)

    started = utc_now()
    routes = _access_routes(access)
    articles = store.iter_articles()
    if limit:
        articles = articles[:limit]

    # For library access, get one live session ready before the batch. Interactive:
    # open the browser once and let the user log in. Non-interactive (agent/no TTY):
    # never wait for a human — require a configured session and fail fast otherwise.
    if access in {"library", "both"} and any(
        (a.get("status") or {}).get("fulltext") != "available" or force for a in articles
    ):
        ok, msg = library.prepare_session(interactive=interactive)
        if not ok:
            raise SystemExit(msg)

    items: list[dict[str, Any]] = []
    succeeded = failed = skipped = 0
    processed_library = 0
    total = len(articles)

    def _needs(a: dict) -> bool:
        st = a.get("status") or {}
        nj = output_format in {"json", "both"} and (force or st.get("fulltext") != "available")
        np = output_format in {"pdf", "both"} and (force or st.get("pdf") != "available")
        return nj or np

    to_attempt = sum(1 for a in articles if _needs(a))
    width = len(str(to_attempt or 1))
    attempt_idx = 0
    if total:
        speed_note = f", speed={speed}" if access in {"library", "both"} else ""
        print(f"Fetching: {to_attempt} to fetch, {total - to_attempt} already done (skipped)"
              f"  [output-format={output_format}, access={access}{speed_note}]", flush=True)

    def _progress(aid: str, detail: str) -> None:
        # Only attempted articles are shown; skips are counted silently.
        print(f"  [{attempt_idx:>{width}}/{to_attempt}] {detail:<12} ok={succeeded} fail={failed}  {aid}",
              flush=True)

    for article in articles:
        status = article.get("status") or {}
        prior_ft_ok = status.get("fulltext") == "available"
        prior_pdf_ok = status.get("pdf") == "available"

        want_json = output_format in {"json", "both"}
        want_pdf = output_format in {"pdf", "both"}
        need_json = want_json and (force or not prior_ft_ok)
        need_pdf = want_pdf and (force or not prior_pdf_ok)
        if not need_json and not need_pdf:
            skipped += 1
            items.append({"article_id": article["article_id"], "status": "skipped", "attempts": []})
            continue

        attempt_idx += 1

        # Throttle between consecutive library articles (skip the wait before the first).
        if access in {"library", "both"}:
            if processed_library > 0:
                lo, hi = SPEED_TIERS[speed]
                wait = lo if lo == hi else random.uniform(lo, hi)
                print(f"      …waiting {wait:.0f}s ({speed})", flush=True)
                time.sleep(wait)
            processed_library += 1

        attempts: list[dict[str, Any]] = []
        json_ok = not need_json
        pdf_ok = not need_pdf

        for route in routes:
            if need_json and not json_ok:
                updated, reason = _json_fetcher(route)(store, article)
                attempts.append({"access": route, "artifact": "json",
                                 "status": "succeeded" if updated else "failed", "reason": reason})
                if updated:
                    article = updated
                    json_ok = True
            if need_pdf and not pdf_ok:
                updated, reason = _pdf_fetcher(route)(store, article)
                attempts.append({"access": route, "artifact": "pdf",
                                 "status": "succeeded" if updated else "failed", "reason": reason})
                if updated:
                    article = updated
                    pdf_ok = True
            if json_ok and pdf_ok:
                break

        # Failure handling that never destroys existing successful artifacts.
        if need_json and not json_ok and not prior_ft_ok:
            quality.reset_fulltext(article)  # nothing good existed -> mark failed, keep abstract only
        if need_pdf and not pdf_ok and not prior_pdf_ok:
            article.setdefault("status", {})["pdf"] = "failed"

        ok = json_ok and pdf_ok
        store.write_article(article)
        if ok:
            succeeded += 1
            item_status = "succeeded"
        else:
            failed += 1
            item_status = "failed"
        items.append({"article_id": article["article_id"], "status": item_status, "attempts": attempts})
        marks = []
        if want_json:
            marks.append(f"json:{'ok' if json_ok else 'fail'}")
        if want_pdf:
            marks.append(f"pdf:{'ok' if pdf_ok else 'fail'}")
        _progress(article["article_id"], " ".join(marks))

    if total:
        print(f"Done. ok={succeeded} fail={failed} / {to_attempt} attempted ({skipped} already done).", flush=True)

    refreshed = store.iter_articles()
    store.write_articles_csv(refreshed)
    store.update_stats(refreshed)
    return store.write_log(
        "fetch",
        {"output_format": output_format, "access": access, "limit": limit, "force": force},
        {"total": len(articles), "succeeded": succeeded, "failed": failed, "skipped": skipped},
        items,
        started,
    )
