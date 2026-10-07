# Changelog

All notable changes to Chronicle are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

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