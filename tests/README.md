# Tests

All commands assume the Python environment where paper-extract is installed.

## Offline (run automatically — no network, no API keys, no browser)

```bash
python -m pytest tests/          # unit tests (28)
python tests/smoke_step1.py      # 地基: CLI + CWD-independence
python tests/smoke_step2.py      # fetch: force-no-data-loss + both fallback + sensitive links
python tests/smoke_step3.py      # library wiring + de-hardcoding + proxy auto-capture
python tests/smoke_step4.py      # search-plan --no-llm + llmclient resolution
python tests/smoke_step5.py      # export bib/ris/csv/jsonl + redaction
```

Run everything: `bash tests/run_all.sh`

## Manual (need real credentials / browser / API keys — cannot be automated here)

1. **Library / institutional full text** (needs `cloakbrowser`). Choose the mode that matches how you
   normally get access:

   ```bash
   pip install ".[browser]"        # installs cloakbrowser
   ```

   **LibKey mode** (for LibKey Nomad users — no SSO needed):
   ```bash
   paper-extract library login --libkey     # loads your LibKey Nomad into the tool browser
   #   In the window: click the LibKey icon, pick your library once; connect VPN if you normally do;
   #   confirm the article shows LibKey "Download PDF"; press Enter. Config and fingerprint persist in the profile.
   paper-extract fetch --collection <name> --output-format both --access library --speed normal --limit 3
   #   fetch opens each article's PubMed/DOI page, lets LibKey inject its link, follows it to the PDF,
   #   and parses it. EXPERIMENTAL: the LibKey link selectors may need tuning — if a fetch fails,
   #   check logs/fetch_*.json (reason like libkey_no_link / libkey_pdf_fetch_failed) and report it.
   ```

   **SSO mode** (for "Access through your institution"):
   ```bash
   paper-extract library login          # opens a paywalled article; log in via SSO, press Enter
   paper-extract fetch --collection <name> --output-format both --access library --speed normal --limit 3
   ```

   **Cookie-borrow mode** (best-effort; note plain requests is 403-blocked by Wiley/Elsevier, so this
   only helps when cloakbrowser injection suffices):
   ```bash
   paper-extract library login --from-chrome     # approve the macOS Keychain prompt
   ```

   Verify in all modes: after setup, a paywalled article's full text is reachable; `article.json`
   has NO cookie/token, proxy links flagged `sensitive`, `logs/fetch_*.json` records the attempts.
   During interactive fetches, solve any captcha/login wall in the browser window; the tool detects
   when the wall clears and continues automatically.
   Confirmed offline: cloakbrowser launches, `add_cookies` works, LibKey Nomad is located and staged
   (`_metadata` stripped). What needs you: a display, your LibKey library selection / login, VPN if
   applicable — plus feedback on what LibKey injects so the link selectors can be refined.

2. **LLM search-plan** (needs an API key for one provider):
   ```bash
   pip install "./llmclient[all]"
   export GEMINI_API_KEY=...        # or OPENAI_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY
   python -m paper_extract search-plan \
       --collection wgd --prompt "whole genome doubling in cancer" --provider gemini
   # or keyword mode with alias expansion + interactive confirm:
   python -m paper_extract search-plan \
       --collection wgd --keyword WGD --keyword cancer --anchor cancer
   ```
   Verify: real aliases generated (WGD → whole genome doubling, …), interactive add/delete works,
   `logs/plan_*.json` has filled `aliases`/`anchors`/`queries`.

3. **Live search / open fetch** (network): `search --query ...`, `fetch --output-format json --access open`.
