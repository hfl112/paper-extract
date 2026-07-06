# Library / institutional full-text access

Getting paywalled full text is interactive (the user logs in once) and depends on
how their institution grants access. Nothing is hardcoded — the EZProxy suffix is
auto-detected from the login session and stored in `data/library.json`.

Requires `pip install "paper-extract[browser]"` (cloakbrowser). Run the CLI in the
Python environment where paper-extract is installed.

## Setup decision tree (do this once)

Ask the user how they normally read paywalled papers, then pick a `library login` mode:

1. **They use a LibKey Nomad browser extension** (macOS + Chrome) →
   `paper-extract library login`  (auto-loads LibKey Nomad if installed; use --no-libkey to skip)
   Loads their LibKey extension into the tool browser. Tell them to: pick their
   library in the LibKey icon (once), connect VPN if they normally do, click LibKey
   "Download PDF" to open one full text, then press Enter. This makes the session
   live AND lets the proxy suffix auto-detect.

2. **They click "Access through your institution" / OpenAthens (SSO)** →
   `paper-extract library login`
   Opens a paywalled article; they log in via SSO, open the full text, press Enter.

3. **Pure EZProxy portal** → same as (2); can also pass
   `--proxy-login-url 'https://libproxy.<school>.edu/login?url={target}'` to seed it.

4. **Cookie borrow (best-effort fallback)** →
   `library login --from-chrome` (macOS Keychain prompt; note plain requests is
   often 403-blocked by Wiley/Elsevier, so this only helps when browser injection works).

After login, confirm `data/library.json` has a `proxy_suffix` — if it's empty, the
user didn't open a full text through the proxy; have them redo login and open one.
The same file also stores a stable `fingerprint_seed`; login and fetch must reuse
the same browser profile so challenge-clearance cookies remain valid.

## Agent-safe: check first, then non-interactive batch

For automated (agent) use, never run `library login` yourself. Instead:

```bash
paper-extract library doctor           # read-only; add --json for machine output
# if NOT READY -> stop and ask the user to run `paper-extract library login`
paper-extract fetch --collection C --output-format both --access library --non-interactive
```

`--non-interactive` (also auto-selected when there's no TTY) never opens a login
prompt: it reuses the user's established session and fails fast on a login page
instead of hanging. `doctor` reports static readiness only — a still-valid session
is confirmed at fetch time.

## Fetching: "log in once → batch many"

```bash
paper-extract fetch --collection C --output-format both --access library --speed normal
```

What happens:
1. It opens ONE browser and pauses: the user makes access live (log in / LibKey /
   captcha / VPN), then presses Enter.
2. It reuses that **live session** for every article, entering through the EZProxy
   `login?url=` form, extracting rendered full text (PDF fallback), with pacing
   controlled by `--speed` (`fast`, `normal`, `slow`) so rapid navigation doesn't
   re-trigger challenges.

If a captcha/login wall appears during an interactive fetch, the user solves it
in the browser window only. The tool polls the page and continues automatically
when the article is visible; do not ask the user to press Enter for each article.

Because the session is short-lived, do library fetching in one run while the browser
stays open. fetch is idempotent (skips already-done articles), so a re-run resumes.

## Troubleshooting (from real runs)

- **"It jumped to the proxy then back / a reCAPTCHA appeared."** Cold re-establishing
  the EZProxy session per-article trips bot detection. Fix: use the live-session flow
  above (log in once at batch start); don't run separate cold fetches per article.
  If challenges continue, retry with `--speed normal` or `--speed slow`.
- **Weak extraction (only References/Funding, missing Intro/Methods).** The article
  was fetched via the plain (non-proxy) route. Ensure `proxy_suffix` is set so fetch
  enters via `login?url=`; the proxied rendered page has the full IMRaD body.
  Re-run with `--force`.
- **`proxy_suffix` came out wrong (e.g. an ad domain).** Only proxy-hint domains are
  accepted now. Re-run `library login` and open a full text so it detects from the
  proxied tab URL.
- **Session expired mid-batch.** Re-run `library login`, then the fetch; done articles
  are skipped automatically.
- **Wrong PMCID / identity mismatch in logs.** Expected safety check; the fetch clears
  a mismatched PMCID rather than attaching the wrong body.

## Safety

- No credentials are stored. Cookies/tokens are never written into `article.json`.
- Proxy/login URLs are flagged `sensitive` and excluded from all exports.
- Never commit `data/library.json`, `data/library_browser_profile/`,
  `data/library_extensions/`, or any cookie file.
