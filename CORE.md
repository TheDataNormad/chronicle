# Chronicle — Core Principles

Chronicle is a data-science-native scraping library. It gets data from the
web quickly, cleans it into typed DataFrames, remembers every run, and tells
you what changed since the last time.

These principles are non-negotiable. Any change that touches one requires
explicit intent, never a side effect. If a PR weakens a principle, reject it
or discuss the principle first.

The principles describe the target. The README describes what exists today
(see principle 10). If a principle is not yet implemented, the README states
it as a limitation and the CHANGELOG records the version that closes it.

## The promise

> Scrape it. Clean it. Remember it. Tell me what changed.

---

## 1. One-line entry point

`Scrape(url).to_dataframe()` must work for common cases. Chronicle picks the
right fetcher (API, JSON, HTML, JS-rendered) automatically. Selectors,
schemas, drift config and fetcher choice are opt-in, never required.

## 2. Every run is recorded

A `Scrape(...).run()` writes a versioned artifact to `.chronicle/`: raw
responses, parsed data, cleaning report, statistical profile, drift report and
manifest. They always exist together. No silent scraping.

## 3. Changes since last time are reported automatically

Every run is compared to the PREVIOUS run ("where you left off") and to a
long-term baseline. This is not a feature flag. It runs automatically whenever
a prior run exists, including when the new run returns nothing. If this ever
becomes optional-by-default, the library has lost its reason to exist.

## 4. Accurate over convenient

Never guess. Never drop. Never silently convert.
- Rows that fail validation are quarantined (`invalid.parquet`) and surfaced
  via `result.invalid_rows`.
- A value that cannot be parsed unambiguously is flagged, not coerced into a
  plausible-looking wrong value.
- A value that becomes missing during cleaning is recorded in the cleaning
  report, not lost.
- Partial failure (failed pages, empty pages) is always reported.

## 5. Clean, typed DataFrames first

The primary output is a pandas DataFrame with proper dtypes: integers, floats,
strings, booleans, dates, datetimes, categoricals, and explicit missing values.
Every run includes a cleaning report saying what was converted, what was
missing, and what was quarantined. JSON, dicts and CLI output are built on top
of the DataFrame.

## 6. Reproducible, offline replay

Every artifact holds enough to reconstruct what was scraped, when, and with
what configuration. Replay must work without network access.

## 7. Fast and polite

Fast by default: concurrent fetching, no wasted requests, work done once.
Polite by default: per-host rate limits, honouring `Retry-After`, `robots.txt`
respected. Speed never overrides politeness. Caching is opt-in, because drift
detection needs fresh data.

## 8. Any source, best legitimate path

Chronicle reaches data through the most legitimate route available, in order:
1. Official APIs (e.g. World Bank, YouTube Data API, Spotify Web API)
2. Public JSON endpoints and data downloads (e.g. `/products.json`, CSV/XLSX)
3. Static HTML with respectful scraping
4. JavaScript-rendered pages through a headless browser

Path 1 (official APIs) and path 2 (public JSON endpoints) are the target and
are not yet implemented. Currently supported: paths 3 and 4.

Fetchers are pluggable, so a new source is a new connector, not a rewrite.
A source counts as "supported" only when it has tests.

## 9. Respect the sites we scrape

Chronicle does not ship anti-bot evasion, CAPTCHA solving, fingerprint
spoofing (e.g. `playwright-stealth`), proxy rotation, or login/credential
circumvention. It does not attempt to defeat commercial bot-detection
services (Cloudflare, DataDome, PerimeterX, Akamai, Imperva).

Pre-setting consent cookies to skip a regional notice is permitted and
documented. Using a headless browser to render public content is permitted.
It is the user's responsibility to verify either is allowed under the target
site's terms.

If a site forbids automated access, Chronicle's answer is the official API,
a public bulk download, or no data. Not a workaround.

## 10. Honest documentation

The README advertises only what exists in the current release. The roadmap is
for what's coming. Never mix the two. Limitations are documented in plain
language.

---

When in doubt, ask: "Does this make Chronicle faster, more accurate, or better
at remembering and explaining change, and does it weaken any principle?"
If it weakens one, even slightly, stop and reconsider.