# Changelog

All notable changes to Chronicle are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Changes since the previous run** (CORE principle 3). Every stored run
  is now compared row by row to the run before it, automatically, with no
  flag. The result is `result.changes` (a `DiffReport`) with
  `rows_added`, `rows_removed`, changed cells, column and dtype changes,
  `.pretty()` and `.to_dataframe()`. Also written to `diff.json` in the run
  artifact, and the manifest records `previous_run_id`. (`diff.py`,
  `core.py`, `storage.py`)
- **`key=` argument** on `Scrape` (`key="name"` or `key=["a", "b"]`). With a
  key, edited values are reported as changed cells. Without one, Chronicle
  reports added/removed rows only and says so; it never guesses a key.
- **Honest warnings** in the diff: zero-row runs, duplicate or missing keys,
  keys absent from a run, and runs with failed pages (removed rows may be
  coverage gaps, not deletions).
- `result.has_changes()` and a `changes` entry in `result.summary()`.

- **Date parsing that never guesses** (CORE principle 4). Date columns are
  detected (or declared) and converted to real datetimes. A value is only
  converted when exactly one reading is possible; otherwise it is flagged
  and the original text is kept in the cleaning report. Resolution order:
  declared `date_formats` / `dayfirst`, then unambiguous forms (ISO, month
  names, `25/04/2026`, `05/05/2026`), then column evidence (any value with
  day > 12 settles the order for that separator style), then flag.
  (`clean.py`)
- **`dayfirst=` and `date_formats=`** arguments on `Scrape` and
  `normalize_rows`.
- **Cleaning report** (`result.cleaning`, saved as `cleaning.json`):
  per-column dates parsed / missing / flagged, plus every value that was
  flagged, with row index, original text, kind, reason and candidate
  readings. Also records values that became missing (`became_missing`),
  numbers read from surrounding text (`partial_parse`), truncated integers
  (`lossy_int`) and comma-ambiguous numbers (`ambiguous_number`).

### Fixed

- **Type inference turned codes into booleans.** Without a schema, `"1"`,
  `"0"`, `"y"`, `"n"`, `"t"`, `"f"` became `True`/`False`, so a grade of
  `"F"` became `False` and a column of `1, 0, 2` became `True, False, 2`.
  Only `true/false/yes/no` are inferred as booleans now. Declared `bool`
  schema fields are unchanged.
- **`"12,5"` was read as 125** when inferring types. It is now kept as text
  and recorded as `ambiguous_number`. `"1,234"` still reads as 1234.

### Known limitations

- Schema-path numeric coercion still takes the first number in text such as
  `"12-15"` (now recorded as `partial_parse`, not yet rejected).
- Only four-digit years are auto-detected in numeric dates; two-digit years
  need `date_formats`.
- English month names only.

### Notes

- Drift detection still compares against the long-term baseline; the new
  diff compares against the previous run. They answer different questions.

## [0.2.1] - 2026-10-07

### Fixed

Seven fixes from an external review of v0.2.0. Every item below is
protected by a test in the suite. See `tests/test_end_to_end_drift.py`
for the pipeline-level regression tests.

- **Cache off by default.** `Scrape.use_cache` now defaults to `False`
  (was `True`). The cache is keyed on URL only, so two consecutive runs
  returned the same HTML and drift compared a page to itself. The
  headline feature could not work with defaults. Caching is now opt-in
  for development loops. (`core.py`)

- **Zero-row results now trigger SEVERE drift.** When the current run
  returns 0 rows and the baseline had N > 0 rows, `_compute_drift` fires
  a SEVERE finding. Previously the drift check was skipped entirely when
  the DataFrame was empty, so a broken selector produced no alert — the
  exact failure mode the library exists to prevent. (`core.py`,
  `drift.py`)

- **Selector validation moved to `__init__`.** Passing a `selectors`
  dict without `"_container"` now raises `ScrapeError` at construction
  time, before any network call. Previously this failed after fetching.
  (`core.py`)

- **Run IDs use microsecond precision.** Before, two runs in the same
  second produced the same `run_id`, so the second run silently
  overwrote the first's artifacts on disk and the SQLite primary key
  collided. Format is now `%Y-%m-%dT%H-%M-%S-%f`. (`storage.py`)

- **`detect_tables` reads `<th>` cells.** Row-header cells
  (`<th scope="row">`, common on Wikipedia) were skipped, so tables
  with row headers had shifted columns. Now reads both `<th>` and
  `<td>`. (`parse.py`)

- **Footnote markers stripped from table cells.** `[1]`, `[a]`,
  `[citation needed]`, and similar markers are removed from cell text
  so downstream type casting works. (`parse.py`)

- **`@text` no longer returns `None`.** The `"@text"` and
  `"selector@text"` syntax was documented but looked up an attribute
  literally named `"text"` and always returned `None`. Now returns the
  node's text, as documented. Attribute extraction (`@href`, `@src`)
  is unchanged. (`parse.py`)

### Added

- `tests/test_end_to_end_drift.py` — a full pipeline test that spins up
  a local HTTP server, serves different HTML between runs, and asserts
  scrape → store → drift behaves correctly. Five test cases, including
  regressions for the cache bug and the zero-row bug.

- `CHANGELOG.md` — this file.

### Changed

- README updated to v0.2.1. Limitations section trimmed to reflect the
  fixed items; the remaining known issues are still documented in plain
  language.

- Test coverage: 46% → 64% on the full suite, driven by the end-to-end
  pipeline test.

### Known limitations

See the "Limitations" section in `README.md`. The core unaddressed
items: string/categorical drift detection, median/variance drift,
baseline management, per-column type inference, path-based pagination,
and CLI `--selectors`.

## [0.2.0] - 2026-10-02

### Added

- `PlaywrightFetcher` — headless Chromium for JS-rendered pages.
- New `Scrape` parameters: `use_playwright`, `wait_for`, `wait_until`,
  `cookies`.
- `examples/js_rendered_site.py` — JS-rendering example.
- CLI: `--playwright` and `--wait-for` flags.

### Changed

- Default `wait_until` for the Playwright fetcher is now
  `"domcontentloaded"` instead of `"networkidle"` (networkidle never
  fires on sites with background network activity).

### Known limitations

See the "Limitations" section in `README.md`.

## [0.1.2] - 2026-10-01

### Fixed

- Support `selectolax` 1.x (`lexbor` backend) while keeping compatibility
  with 0.x (`parser`/modest backend). A try/except at import time.

## [0.1.1] - 2026-10-01

### Changed

- Removed experimental `detect_lists` auto-detection. Selector mode
  and table auto-detection remain.
- Version bump for PyPI re-release.

### Fixed

- Fixed a mangled `_parse` method in `Scrape`.

## [0.1.0] - 2026-09-30

### Added

- Initial release.
- `Scrape(url).to_dataframe()` — one-line entry point.
- Selector mode for arbitrary HTML parsing.
- Auto table detection.
- Pydantic-based schema validation with row quarantine.
- Versioned run storage in `.chronicle/`.
- Per-column statistical profiles.
- Drift detection: schema, null, mean, range, selector health.
- CLI: `version`, `scrape`, `runs` commands.
- 18 unit tests.
- Published to PyPI as `chronicle-ds`.