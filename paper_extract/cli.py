from __future__ import annotations

import argparse

from .collection import CollectionStore
from .collection.importer import import_articles
from .export import export_collection
from .fetch import run_fetch
from .search import build_plan, run_search
from .status import status_report
from .time import utc_now


def cmd_search_plan(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    started = utc_now()
    plan = build_plan(
        keywords=args.keyword,
        anchors=args.anchor,
        prompt=args.prompt,
        min_year=args.min_year,
        max_year=args.max_year,
        match=args.match,
        no_llm=args.no_llm,
        no_confirm=args.no_confirm,
        provider=args.provider,
        model=args.model,
    )
    path = store.write_plan(plan, started)
    print(f"Wrote search plan: {path}")


def cmd_search(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    path = run_search(
        store,
        query=args.query,
        plan_path=args.plan,
        min_year=args.min_year,
        max_year=args.max_year,
        max_results=args.max_results,
    )
    print(f"Wrote search log: {path}")


def cmd_fetch(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    path = run_fetch(
        store,
        output_format=args.output_format,
        access=args.access,
        limit=args.limit,
        force=args.force,
        interactive=(False if args.non_interactive else None),
        speed=args.speed,
    )
    print(f"Wrote fetch log: {path}")


def cmd_library_doctor(args: argparse.Namespace) -> None:
    import json as _json

    from .library.browser import doctor

    d = doctor()
    if args.json:
        print(_json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(f"Library access: {'READY' if d['ready'] else 'NOT READY'}  ({d['reason']})")
    for k, v in d["checks"].items():
        print(f"  {k}: {v}")
    print(f"Next: {d['next_action']}")


def cmd_status(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    path = status_report(store)
    print(f"Wrote status log: {path}")


def cmd_collection_import(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    path = import_articles(
        store,
        input_path=args.input,
        input_json=args.input_json,
        input_doi=args.input_doi,
        input_pmid=args.input_pmid,
        input_pdf=args.input_pdf,
    )
    print(f"Wrote import log: {path}")


def cmd_collection_export(args: argparse.Namespace) -> None:
    store = CollectionStore.open(args.collection)
    path = export_collection(store, args.to, args.output)
    print(f"Wrote export: {path}")


def cmd_library_login(args: argparse.Namespace) -> None:
    if args.from_chrome:
        from .library.chrome_cookies import import_chrome_cookies
        from .library.config import cookie_file

        try:
            n = import_chrome_cookies(all_domains=args.all_domains)
        except Exception as e:
            print(f"读取 Chrome cookie 失败: {type(e).__name__}: {e}")
            print("提示：先完全退出 Chrome 再试；macOS 首次可能弹 Keychain 授权,请允许。")
            return
        print(f"已从 Chrome 导入 {n} 条 cookie(学术相关域) -> {cookie_file()}")
        print("现在可运行：paper-extract fetch --collection <name> --output-format json --access library")
        return

    from .library.browser import library_login

    ok = library_login(landing_url=args.landing_url, proxy_login_url=args.proxy_login_url,
                        headless=args.headless, use_libkey=args.libkey)
    print("Library login: " + ("captured session/cookies" if ok else "did not complete"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-extract")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search-plan", help="Generate a reproducible literature search plan")
    p.add_argument("--collection", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--keyword", action="append", default=[])
    group.add_argument("--prompt")
    p.add_argument("--anchor", action="append", default=[],
                   help="Mandatory concept (always AND-ed); repeatable. Must also appear as --keyword.")
    p.add_argument("--match", type=int, help="M-of-N: how many key concepts must match (default: all)")
    p.add_argument("--min-year")
    p.add_argument("--max-year")
    p.add_argument("--no-llm", action="store_true", help="Skip the LLM; keyword mode only")
    p.add_argument("--no-confirm", action="store_true", help="Skip interactive alias confirmation")
    p.add_argument("--provider", help="LLM provider: gemini|openai|deepseek|claude (default: auto)")
    p.add_argument("--model", help="Explicit model id override")
    p.set_defaults(func=cmd_search_plan)

    p = sub.add_parser("search", help="Search Europe PMC + PubMed and add metadata to a collection")
    p.add_argument("--collection", required=True)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--query")
    group.add_argument("--plan")
    p.add_argument("--min-year")
    p.add_argument("--max-year")
    p.add_argument("--max", type=int, default=1000, dest="max_results")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("fetch", help="Fetch structured fulltext JSON and/or PDFs")
    p.add_argument("--collection", required=True)
    p.add_argument("--output-format", choices=["json", "pdf", "both"], required=True)
    p.add_argument("--access", choices=["open", "library", "both"], default="open")
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--non-interactive", dest="non_interactive", action="store_true",
                   help="Never prompt/open a login browser; require a pre-established library session and fail fast otherwise")
    p.add_argument("--speed", choices=["fast", "normal", "slow"], default="fast",
                   help="Library throttle between articles: fast=8s, normal=5-60s random, slow=50-300s random (slower avoids reCAPTCHA)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("status", help="Print and log collection status")
    p.add_argument("--collection", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("collection", help="Collection import/export")
    csub = p.add_subparsers(dest="collection_command", required=True)

    ci = csub.add_parser("import", help="Import existing article identifiers or metadata")
    ci.add_argument("--collection", required=True)
    ci.add_argument("--input")
    ci.add_argument("--input-json")
    ci.add_argument("--input-doi", action="append", default=[])
    ci.add_argument("--input-pmid", action="append", default=[])
    ci.add_argument("--input-pdf", action="append", default=[],
                    help="Local PDF file or directory of PDFs; metadata is read from the PDF and enriched via Europe PMC")
    ci.set_defaults(func=cmd_collection_import)

    ce = csub.add_parser("export", help="Export a collection to external formats")
    ce.add_argument("--collection", required=True)
    ce.add_argument("--to", choices=["bib", "ris", "csv", "jsonl"], required=True)
    ce.add_argument("--output")
    ce.set_defaults(func=cmd_collection_export)

    lp = sub.add_parser("library", help="Institutional-library access setup")
    lsub = lp.add_subparsers(dest="library_command", required=True)
    ll = lsub.add_parser("login", help="Open a browser to log in; session is saved for --access library")
    ll.add_argument("--libkey", dest="libkey", action="store_true", default=None,
                    help="Force-load your LibKey Nomad extension (default: auto-load if installed in Chrome)")
    ll.add_argument("--no-libkey", dest="libkey", action="store_false",
                    help="Don't load LibKey; plain SSO / EZProxy login")
    ll.add_argument("--from-chrome", dest="from_chrome", action="store_true",
                    help="Borrow institutional cookies from your real Chrome (Lean Library / SSO) instead of opening a browser")
    ll.add_argument("--all-domains", dest="all_domains", action="store_true",
                    help="With --from-chrome: import cookies for all domains, not just academic hosts")
    ll.add_argument("--landing-url", dest="landing_url",
                    help="Page to open for login (default: a paywalled article, to make access visible)")
    ll.add_argument("--proxy-login-url", dest="proxy_login_url",
                    help="Optional EZProxy template, e.g. https://libproxy.<school>.edu/login?url={target}")
    ll.add_argument("--headless", action="store_true")
    ll.set_defaults(func=cmd_library_login)

    ld = lsub.add_parser("doctor", help="Read-only check of library-access readiness (non-interactive)")
    ld.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    ld.set_defaults(func=cmd_library_doctor)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
