# Handoff: implement the `extract` command for paper-extract

Audience: an AI/engineer picking this up fresh. This is a design + step-by-step
implementation brief. It was validated end-to-end against a real article
(dasatinib PPTP paper) with a throwaway script; this doc turns that into a
production feature.

**Non-goal / important:** the extraction **field set is user-supplied and
open-ended**. Do NOT hardcode any fixed schema or field list. The example fields
below are ONLY an illustration of what a user might pass; the module must accept
an arbitrary, growing spec.

---

## 1. What `extract` does (the job-to-be-done)

Given a collection that already has full text, plus a **user-defined extraction
spec** (a set of fields), run an LLM over each article's full text and produce a
**structured record per article** — stored, logged, and exportable. The result
must be **auditable and reproducible** (which spec + model + when produced it).

It is the analysis rung after `search` → `fetch`: `search/import → fetch →
extract → export`.

---

## 2. Codebase context — patterns to MIRROR (read these first)

This repo already has the seams you should copy. Read them before writing code:

- **Source port** — `paper_extract/search/sources.py`: a `Protocol` + adapters +
  a registry + `select_sources()`. Mirror this shape for pluggability.
- **Injectable transport port** — `paper_extract/sources/fulltext/fulltext_sources.py`:
  `HttpClient` protocol, `UrllibClient` prod adapter, a `client=` param on
  `get_fulltext()`/`download_pdf()` swapped via a context manager, and a fake in
  tests (`tests/test_fulltext_client.py`). **This is the exact pattern for the
  LLM port.**
- **Article module** — `paper_extract/article.py`: the single owner of the
  `article.json` shape + state transitions + status enum strings. It ALREADY has
  a reserved `status.llm_extract` slot (values live only here). Add extract
  transitions/queries HERE, not scattered.
- **On-disk owner** — `paper_extract/collection/store.py` (`CollectionStore`):
  owns the folder layout (`articles/<id>/article.json`, `logs/<cmd>_<ts>.json`)
  and `write_log(command, args, summary, items, started)`. All layout knowledge
  lives here.
- **Runner shape** — `paper_extract/fetch/runner.py` (`run_fetch`) and
  `paper_extract/search/runner.py` (`run_search`): iterate items, skip
  already-done unless `--force`, per-item try/except, persist, then
  `store.write_log(...)` with `summary{total,succeeded,failed,skipped}` +
  `items[]`. Copy this structure for `run_extract`.
- **Bundled LLM client** — `llmclient` (vendored). `call_llm(prompt, *, provider,
  model, json=False, task_level, system) -> str`. **CRITICAL LIMITATION:**
  `json=True` only sets `response_mime_type=application/json` (generic "valid
  JSON"), it does **NOT** pass/enforce a response schema. So layer 2 (below)
  cannot be done through `llmclient` as-is — see Phase E0.
- **CLI** — `paper_extract/cli.py`: argparse subcommands, one `cmd_*` per command.
- **Tests + CI** — offline only; fakes injected (see `FakeClient` in
  `tests/test_fulltext_client.py`, `FakeSource` in `tests/test_search.py`). CI
  (`.github/workflows/ci.yml`) gates `ruff check .`, `mypy`, `pytest`, and the
  offline smokes. Keep everything green.

---

## 3. The design (settled) — the three layers

Extraction quality is controlled by three independent layers, each with a
distinct job. Build all three.

1. **Prompt (soft guidance).** Assembled from the spec: an instruction + the
   field list rendered as text (`name (type): description`) + rules
   (only-stated-facts / null-if-absent / anti-generalization) + selected article
   context (NOT the whole paper — pick sections + a budget). Tells the model
   *what each field means*. The model can disobey.

2. **Schema enforcement (hard, structural).** Derive a provider schema from the
   spec's fields and pass it to the provider's structured-output feature
   (Gemini `response_schema`, OpenAI `response_format: json_schema`). Guarantees
   the returned object has exactly the declared keys/types/order — the model
   cannot add/drop keys or return wrong types. Requires the LLM port to accept a
   schema (Phase E0). This is the difference between "asking in text" and
   "constraining the decoder".

3. **Code validation (deterministic, semantic).** After parse: (A) keep only
   declared fields / coerce / fill missing → null (a thin fallback; mostly
   redundant once layer 2 is enforced), and (B) **semantic cleanup the schema
   cannot express** — dedup arrays, normalize whitespace, canonicalize labels
   against a controlled vocabulary, drop redundant values. Layer 2 guarantees
   *shape*; layer 3 guarantees *content the schema can't state* (e.g. "no
   duplicate items", "map synonyms", "drop a parent category when a subtype is
   present").

Rule of thumb: **prompt = meaning; schema = structure; code = semantics.**
Don't try to make the LLM do (via prompt) what code should guarantee.

---

## 4. Other settled decisions

- **LLM as an injectable port** (mirror `HttpClient`). Define an in-repo
  `LLMClient` protocol with a **schema-aware** method. Prod adapter produces
  structured output per provider; test adapter is a fake returning canned JSON.
  Two adapters → the seam is real, and `extract` becomes fully offline-testable.
- **Spec is user-defined, versioned, extensible.** A spec has a `name`, an
  optional `instruction`, and a list of `fields` (each: `name`, `type`,
  `description`; optionally `enum`, `required`, nested items). Compute a stable
  `spec_id` = hash of the normalized spec. **Never assume a fixed field set.**
- **Storage keyed by `spec_id`, additive & non-destructive.** Changing the spec
  → new `spec_id` → a *different* extraction stored alongside the old. Old
  results are never clobbered. (Recommended: nest under
  `article["extractions"][spec_id] = {..fields.., "_provenance": {...}}` to keep
  the "one readable article.json per paper" promise; if extractions get large,
  switch to `articles/<id>/extractions/<spec_id>.json` and put the path in
  `CollectionStore`. Either way: layout knowledge in `CollectionStore`, the
  state transition in `article.py`.)
- **Idempotent.** `run_extract` skips articles already extracted under this exact
  `spec_id`; `--force` redoes. (Mirror `run_fetch`'s prior-success skip.)
- **Provenance & reproducibility.** Each record carries `_provenance`
  {model, spec_id, extracted_at, source_sections, token_usage?}; the run writes
  `logs/extract_<ts>.json` including the full spec (so a corpus is replayable).
- **Core returns results; runner persists.** `extract_article(...)` is pure (no
  disk writes); `run_extract` owns persistence + logging. (Mirror
  `run_search`/`run_fetch`.)
- **Context selection is configurable, not hardcoded.** Default to
  abstract + methods + results + conclusions with a char/token budget; expose it
  so users can widen/narrow. (In the demo, feeding only those sections — not the
  whole 47k-char paper — already produced accurate extractions.)
- **Error modes are part of the interface.** Retry on transient provider errors
  (503/429) with backoff; retry on unparseable/invalid output up to N; on final
  failure mark the article's extract status failed with a `reason` (mirror
  `fetch`'s per-item `attempts[].reason`). Free-text fields will vary run-to-run
  (LLM non-determinism) — that's expected; lock *structure* (layer 2) and
  *semantics* (layer 3), not prose.

---

## 5. Module layout (proposed)

```
paper_extract/
  llm.py                      # LLMClient protocol + prod adapter (schema-aware) + notes
  extract/
    __init__.py               # exports run_extract
    spec.py                   # ExtractSpec: load(file/dict), spec_id, derive provider schema, render prompt fields
    core.py                   # extract_article(article, spec, llm) -> ExtractionResult (PURE) + cleanup fns (dedup/canon)
    runner.py                 # run_extract(store, *, spec, llm=None, limit=None, force=False) -> Path(log)
  article.py                  # + record_extraction(), mark_extract_failed(), has_extraction() ; enum strings stay here
  cli.py                      # + `extract` subcommand
  export/                     # + extractions exporter (CSV/JSONL): one row per (article, spec_id)
```

---

## 6. Implementation steps (phased; one commit each; keep CI green)

Work on a branch. Each phase: `ruff check .` + `mypy` + `pytest` + offline smokes
green (run smokes with `PAPER_EXTRACT_ROOT` UNSET), then commit; push and confirm
CI green. Add a CHANGELOG entry for user-facing phases.

### Phase E0 — LLM port with schema support
- Add `paper_extract/llm.py`: `class LLMClient(Protocol)` with strict type hints conforming to the project's quality rules:
  ```python
  from typing import Protocol, Dict, Any, Optional
  from google.genai import types

  class LLMClient(Protocol):
      def extract(
          self, 
          prompt: str, 
          *, 
          schema: types.Schema, 
          system: Optional[str] = None
      ) -> Dict[str, Any]:
          """Send a prompt to the LLM and return a structured JSON response matching the schema."""
          ...
  ```
- Prod adapter: implement structured output per provider. Gemini path is known
  to work: `google.genai` → `client.models.generate_content(model="gemini-2.5-flash",
  contents=prompt, config=types.GenerateContentConfig(system_instruction=...,
  response_mime_type="application/json", response_schema=<types.Schema>))`.
  For other providers use their json-schema/tool mode. Reuse `llmclient.config`
  for key/model resolution if convenient, but you MUST bypass `llmclient.call_llm`
  for the schema (it can't pass one).
- Fake adapter for tests: returns preset dicts (incl. a malformed-then-valid
  sequence and a refusal) — no network.
- **Acceptance:** unit test drives the fake adapter; a `@network`-gated test can
  hit a real provider but must not run in CI. `ruff`/`mypy`/`pytest` green.

### Phase E1 — ExtractSpec (pure)
- `spec.py`: load a spec from a dict / JSON / YAML file; validate it (field names
  unique, types in an allowed set); compute `spec_id` (stable hash of normalized
  spec); `to_provider_schema(spec)` → the schema object for layer 2;
  `render_fields(spec)` → the field-list text for the prompt.
- **Acceptance:** unit tests: spec_id stable & changes when a field/description
  changes; schema derivation covers string/array/number/boolean; bad spec raises.

### Phase E2 — extract_article core + cleanup (pure, offline)
- `core.py`: `build_prompt(spec, article, context_cfg)` (section selection +
  budget), `extract_article(article, spec, llm)` → builds prompt, calls
  `llm.extract(prompt, schema=..., system=...)`, runs layer-3 cleanup, attaches
  provenance, returns an `ExtractionResult`. Include the cleanup helpers
  (dedup, whitespace-normalize, optional controlled-vocab canonicalize — start
  with a small alias map + a pluggable hook; a real vocab like NCIt/MeSH is a
  later add).
- **Acceptance:** offline tests with the fake LLM: valid output → cleaned record;
  malformed-then-valid → retry then success; refusal/empty → failure with reason;
  arrays are deduped; declared-fields-only enforced. No network.

### Phase E3 — Article transitions + storage
- `article.py`: `record_extraction(article, spec_id, record, provenance)`,
  `mark_extract_failed(article, reason)`, `has_extraction(article, spec_id)`;
  set `status.llm_extract` via the enum constants defined in this module.
- Persist nested under `article["extractions"][spec_id]` (recommended) — or add
  `store.extraction_path(...)` if you choose separate files.
- **Acceptance:** unit tests on the transitions + `has_extraction`; golden test
  that a recorded extraction round-trips through `CollectionStore` read/write.

### Phase E4 — run_extract runner
- `runner.py`: iterate articles that have full text; skip those already extracted
  under `spec_id` unless `force`; per-article try/except → record or mark failed;
  write `logs/extract_<ts>.json` with `summary` + `items[]` (incl. the full spec
  for replay).
- **Acceptance:** offline test with fake LLM + a tmp `PAPER_EXTRACT_ROOT`
  collection: extracts N, skips already-done, `--force` redoes, log summary
  correct, one article's failure isolated. No network.

### Phase E5 — CLI + export
- `cli.py`: `extract` subcommand: `--collection` (req), `--spec <file>` (req; or
  `--field name:type:desc` repeatable as an inline convenience), `--provider`,
  `--model`, `--limit`, `--force`. Resolve the LLM adapter from provider/keys;
  fail cleanly if no key (like the search-plan LLM path does).
- `export/`: an extractions exporter — `collection export --to extractions`
  (CSV/JSONL): one row per (article, spec_id); columns = the spec's fields; array
  fields joined. Reuse the `citation_view` pattern (single extraction → row).
- **Acceptance:** `paper-extract extract --help` shows the flags; a fake-LLM
  end-to-end smoke (offline) builds + exports; CHANGELOG updated; CI green.

---

## 7. Illustrative example ONLY (do not hardcode)

This is what a user's spec might look like (validated in a throwaway run against
"Initial testing of dasatinib by the PPTP", doi:10.1002/pbc.21368). It is an
EXAMPLE of the shape; users define their own fields and will add many more.

- `instruction`: "This is a pediatric preclinical drug-testing article. Extract
  only what the text states; null if not reported; never coin a broader category
  to summarize more specific labels."
- fields (each name / type / description):
  - `drug` (string) — the primary drug/compound tested.
  - `cancer_types_tested` (array) — histologies tested, exact table labels, no
    parent category when a subtype is listed.
  - `cancer_types_active` (array) — only models where the drug was active;
    include response type (e.g. "complete response").
  - `dose_route` (string) — dose + route.
  - `in_vitro_exposure` (string) — concentration range + duration; null if none.
  - `in_vivo_schedule` (string) — schedule incl. any change and its reason.
  - `outcome` (string) — in-vitro & in-vivo efficacy separately, with counts,
    named responsive models, complete-response model + molecular context, and
    toxicity caveats.

What the validated run demonstrated:
- Splitting one vague `cancer_types` field into `tested` vs `active` (a
  description change only, no code) hugely improved usefulness.
- Layer 2 (schema) made the returned keys exactly the declared set, in order.
- Layer 3 (code) deterministically deduped the histology array and dropped a
  redundant parent category ("Rhabdomyosarcoma" when subtypes were present) —
  which prompt instructions alone did NOT reliably achieve.

### Golden Test Case for Layer 3 (Code Validation)
To write precise, offline assertions for the Phase E2 cleanup helpers, the test suite should validate the following input-output mapping:

**Input (Raw LLM output parsed to dictionary):**
```json
{
  "drug": "dasatinib ",
  "cancer_types_tested": [
    "rhabdomyosarcoma",
    "alveolar rhabdomyosarcoma",
    "embryonal rhabdomyosarcoma",
    "rhabdomyosarcoma"
  ],
  "cancer_types_active": [
    "Alveolar rhabdomyosarcoma"
  ],
  "dose_route": "  50 mg/kg p.o. daily  "
}
```

**Output (After Layer 3 clean_extraction_result):**
```json
{
  "drug": "dasatinib",
  "cancer_types_tested": [
    "alveolar rhabdomyosarcoma",
    "embryonal rhabdomyosarcoma"
  ],
  "cancer_types_active": [
    "Alveolar rhabdomyosarcoma"
  ],
  "dose_route": "50 mg/kg p.o. daily"
}
```
*Note: whitespace is stripped, elements are deduped, and parent categories ("rhabdomyosarcoma") are discarded when specific subtypes (e.g. "alveolar rhabdomyosarcoma") are present.*

---

## 8. Scaling to many / fine-grained fields (strategies to try)

A single giant spec + one prompt does NOT reliably capture a large or detailed
field set. Three real failure modes appear as fields grow: (a) **attention
dilution** — per-field quality drops as the field count rises, long-tail fields
get answered lazily; (b) **context budget** — detailed fields want more source
text, which collides with "many fields" and blows the token budget; (c)
**reliability/testability** — one giant call fails as a unit. Prefer
divide-and-conquer. These strategies are composable and, importantly, need **no
architecture change** — each "group / pass" is just another `spec` (its own
`spec_id`), run by the same `run_extract`, idempotent and independently re-runnable.
Adding more to extract later = adding/splitting specs, not editing code.

1. **Field-cluster extraction (most common, most robust).** Split one big spec
   into a few themed sub-specs (e.g. `dosing_*`, `efficacy_*`, `safety_*`), one
   LLM call per cluster (5–8 related fields each), then merge by `article_id`
   into one wide record. Pros: each prompt stays focused → higher quality; a
   failed cluster re-runs alone; naturally parallel. Cost: multiple calls per
   article. Fits the design directly (each cluster is a spec; merge = join the
   per-`spec_id` extractions into one table).

2. **Coarse screen → fine extract (two passes).** Pass 1 (cheap: small model,
   short context) decides *whether* the article has the target content / which
   class it is / where the relevant passages are. Pass 2 (expensive: big model)
   runs the full detailed extraction **only on the hits**, feeding **only the
   relevant passages**. Best when the corpus is large and the hit rate is low
   (screen out most articles cheaply). Pass 1 is just "a very small, cheap spec".

3. **Locate-then-extract (retrieval / section routing).** Fine fields live in
   specific places (dose → Methods, efficacy → Results, toxicity → Safety). Do a
   lightweight locate step (keyword / embedding retrieval / ask the LLM which
   sections are relevant) and feed only those passages to the extraction.
   Orthogonal to #1/#2 — stack it for smaller, sharper context and better recall
   on detailed fields.

4. **Self-check / gap-fill (optional extra pass).** After extraction, find which
   fields are still `null` and re-extract **only those** with a more focused
   prompt (or a bigger model, or more context). The adversarial review we ran
   (a second model critiquing the extraction for omissions) can be automated as
   this pass.

Rule of thumb for choosing:
- many fields but same articles, modest corpus → **#1 clusters** (don't over-engineer).
- large corpus, low hit rate → **#2 coarse→fine** (cost is the driver).
- specific fields need high precision / easily missed → **#3 locate** + **#4 gap-fill**.
- maximal quality → stack all: screen → locate → cluster-extract → gap-fill.

Recommendation: build the single-spec path first (Phases E0–E5), then add
cluster/multi-pass orchestration on top — it's a runner-level loop over specs,
not a change to `extract_article` or the storage model.

---

## 9. Gotchas learned from the real run (save the next dev time)

- `llmclient.call_llm(..., json=True)` = generic JSON only, **no schema**. Layer 2
  needs the provider SDK directly (or an extended llmclient). See Phase E0.
- Gemini: model `gemini-2.5-flash`; `response_schema` via
  `google.genai.types.Schema` (`Type.OBJECT/ARRAY/STRING`, `nullable`,
  `property_ordering`); transient `503 UNAVAILABLE` happens → retry with backoff.
- The venv is **uv-managed and has no `pip`** — use `uv pip install ...`. The LLM
  SDKs are the `[llm]` extra (`pip install -e ".[llm]"` equivalent). `llmclient`
  auto-loads a `.env` from the current working directory for keys.
- Don't feed the whole paper into the prompt — select sections + a budget.
- LLM free-text fields vary run-to-run and won't perfectly obey formatting
  instructions — lock structure (layer 2) and semantics (layer 3) in code.
- Keep all new files free of machine coupling (no absolute `/Users/...` paths, no
  real institution names). CI + the existing decoupling conventions apply.
- `mypy` is scoped in `pyproject.toml` to specific well-typed modules; only add a
  new module to that list if it is fully typed and clean.
