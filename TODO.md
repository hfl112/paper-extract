# TODO / Backlog

Running backlog. Not user-facing (that's CHANGELOG.md); this is the dev roadmap.

## Deferred (do later, not now)

- **Publish to PyPI** — deferred: more features coming first. MUST fix these two
  blockers before uploading, or `pip install` breaks for users:
  - `paths.data_root()` resolves into `site-packages` when pip-installed
    (walks for a `pyproject.toml` ancestor, else `here.parent.parent`). Default
    to CWD `./data` or a `platformdirs` user-data dir; keep `PAPER_EXTRACT_ROOT`
    override.
  - Vendored `llmclient` ships as a **top-level** package → name collision in
    `site-packages`. Re-vendor under `paper_extract/_vendor/llmclient/` and
    import as `paper_extract._vendor.llmclient`.

## Considering (evaluating)

- **CI (GitHub Actions)** — matrix py3.11/3.12/3.13: `pytest` + ruff + mypy;
  run existing offline tests automatically on push/PR. Smoke tests behind a
  `network` marker (manual/nightly). Highest value-to-effort item.
- **OpenAlex as a 3rd search source** — new `OpenAlexSource` adapter (Source
  protocol) + a `normalize()` mapping OpenAlex JSON → the shared doc shape;
  register in `DEFAULT_SOURCES`. Widens coverage beyond biomedical (kills the
  #1 user-bounce). NOTE: also generalize `merge_results` for clean 3+-source
  dedup — today the epmc+pubmed branch appends extra sources without deduping
  against the merged set (`doc_key` already exists to key it).

## Engineering backlog (from architecture review 2026-07-07)

P1:
- Type the Article: `TypedDict` now (cheap), ideally pydantic v2 validated at
  the `CollectionStore` read/write seam + a `schema_version` migration hook.
- Replace `raise SystemExit` in `fetch/runner.py` with a domain exception; let
  `cli.py` own the exit.
- Adopt stdlib `logging` (stderr) + `-v/--verbose`; log before swallowing broad
  `except Exception`; make user-facing strings English-only (drop hardcoded
  Chinese in a public repo).
- Verify SSL by default on OA fetch; fall back to unverified only on cert error
  and record `ssl_unverified: true` in provenance.
- Test the fragile paths: unit-test the pure functions in `browser.py`; extend
  the existing HttpClient fake with recorded per-adapter responses; add a
  `Page`/`Context` protocol so browser wall-detection is testable.

P2:
- Move `flatten_article`'s network PMCID check out of the "pure" translation
  helper into an explicit fetch step.
- Point internal imports at `..article` (not the `schema.py` back-compat shim).
- Add `paper_extract/__main__.py` so `python -m paper_extract` works.
- Config visibility: extend `library doctor` to report which env keys/tokens
  are detected (redacted).
- Opt-in bounded thread pool for `--access open` only (keep library serial);
  make `UrllibClient._throttle` thread-safe first.
- Skill polish: instruct `library doctor --json`; note the extras are heavy +
  independent; drop the "Never touch Zotero" leftover line.

## Product roadmap (from PM review 2026-07-07)

Now: OpenAlex source · a v0 `extract` command (LLM over full text → structured
output; `llm_extract` schema slot + bundled `llmclient` already exist) ·
PyPI + runnable example collection in `examples/` + RAG-ending quickstart notebook.

Next: cross-collection dedup + incremental sync · RAG export = section-aware
chunking + stable per-chunk IDs (do NOT build an embedding store) · shareable
collection manifest / replay command.

Later (gate on demand): screening/PRISMA export · citation-graph/snowballing ·
arXiv/bioRxiv search sources · lightweight TUI.

Do NOT build: hosted service · embedding/vector DB · general reference manager ·
captcha solving.
