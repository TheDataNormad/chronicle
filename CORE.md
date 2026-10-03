# Chronicle — Core Principles

These are the non-negotiable pillars of Chronicle. Any change to a
file or feature that touches these requires explicit intent, not
a side effect. If a PR weakens any of these, it should be rejected
or the principle should be discussed first.

## 1. One-line entry point

`Scrape(url).to_dataframe()` must always work for common cases.
Advanced features (selectors, schemas, drift config) are opt-in,
never required for the basic path.

## 2. Every run is recorded

A `Scrape(...).run()` call writes a versioned artifact to `.chronicle/`.
Raw HTML, parsed data, statistical profile, and manifest always exist
together. No silent scraping.

## 3. Drift detection is first-class

Comparing current data to a baseline is not a feature flag. It runs
automatically on every `Scrape.run()` when a baseline exists. If it
ever becomes optional-by-default, the library has lost its reason to
exist.

## 4. No silent data loss

Rows that fail schema validation are quarantined (in `invalid.parquet`)
and surfaced via `result.invalid_rows`. They are never dropped silently.
Never.

## 5. DataFrames first

The primary output of a scrape is a pandas DataFrame with proper dtypes.
Everything else (JSON, dicts, CLI output) is built on top of that.

## 6. Reproducibility is guaranteed

Every run artifact contains enough information to reconstruct exactly
what was scraped, when, and with what configuration. Replay must be
possible without network access.

## 7. Honest documentation

The README only advertises APIs that exist in the current release.
The roadmap section is for what's coming. Never mix the two.

---

When in doubt, ask: "Does this change strengthen or weaken the core?"
If it weakens — even slightly — stop and reconsider.