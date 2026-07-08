# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **OpenAlex** as a third search source (default-on), extending coverage beyond
  biomedical to all disciplines (2.5M+ venues, no API key). Abstracts are
  reconstructed from OpenAlex's inverted index. Because OpenAlex indexes arXiv,
  arXiv papers now surface in ordinary searches.
- **arXiv full text**: an arXiv fetch adapter (keyed on the `10.48550/arXiv.*`
  DOI, like the bioRxiv adapter) downloads and parses the PDF for arXiv papers
  found via OpenAlex. arXiv is intentionally *not* a separate search source.
- `search --source <name>` (repeatable) to limit a search to specific sources
  (`epmc` / `pubmed` / `openalex`); default searches all. Unknown names error.
- `collection import --input-pdf`: import papers from a local PDF file or a
  directory of PDFs. Metadata (DOI, title) is read from the PDF via PyMuPDF
  (optional `[pdf]` extra) with a filename fallback when it is not installed,
  then enriched via Europe PMC.

### Fixed
- **Library-login diagnostics.** `library doctor` now distinguishes "not logged
  in" (`needs_login`) from "logged in but the institution proxy route wasn't
  detected" (`proxy_route_undetected`, common after pure SSO) and gives
  actionable next steps instead of an unhelpful "run library login" loop.
  `library login` reports whether it's actually ready to fetch. Config files are
  read BOM-tolerantly (utf-8-sig) and parse errors are surfaced in `doctor`
  instead of being silently treated as "unconfigured".

### Changed
- Internal architecture refactor — no change to CLI behavior or on-disk output:
  - The article schema and its state transitions now live in one **Article
    module** (`paper_extract/article.py`); status values are defined once.
  - Full-text assembly (the flatten → build → quality → link-marking sequence)
    is shared by the open-access and institutional routes via one **assemble**
    module, removing duplicated code and an internal import cycle.
  - The fetch transport is an **injectable HTTP client**, so the full-text
    fetch path is exercisable offline.
  - Search sources sit behind a **Source interface** with Europe PMC and PubMed
    adapters, sharing one retry loop and dedup key.
  - Citation exports (BibTeX/RIS/CSV) share one `citation_view`.

### Removed
- Dead modules and unused writers left over from a retired CLI
  (`cancer_tagger`, `dedup_merge`, per-fetcher `write_csv`/`write_json`).

## [0.1.0] - 2026-07-05

### Added
- Initial public release: search (Europe PMC + PubMed), import by DOI/PMID/CSV,
  structured full-text JSON + PDF fetch (open access and institutional via
  EZProxy/LibKey), and BibTeX/RIS/CSV/JSONL export, with per-command audit logs.
- Agent Skill for Claude Code / Codex-style agents.

[Unreleased]: https://github.com/hfl112/paper-extract/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hfl112/paper-extract/releases/tag/v0.1.0
