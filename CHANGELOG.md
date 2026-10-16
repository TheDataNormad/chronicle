# Changelog

All notable changes to Chronicle are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0-alpha] - 2026-10-16

This release hardens Chronicle against findings F-01 through F-15 from a
runtime engineering audit. It is a **pre-release**: not published to PyPI,
not recommended for production, and not yet fully reviewed line by line.
See PR #1 for the full review and diff.

Test count: 23 → 147. Coverage: 64% → 84%.

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

- **JSON/API connector** (`jsonapi.py`, CORE principle 8, paths 1 and 2).
  Handles Shopify-style, World Bank-style, and plain REST list shapes
  (World Bank `[{...meta...}, [ {...}, ... ]]`, Shopify `{"products": [...]}`,
  plain lists, `{"data": {"items": [...]}}`). The response kind is detected
  automatically; selectors and `records_path` cannot be mixed.
  Values keep their JSON types: a JSON string `"00123"` stays `"00123"`.
  If several record lists exist and no `records_path` was given, the error
  names the candidates; Chronicle never picks one silently.

- **API key redaction** (`storage.py`). Secret-looking query parameters
  (`api_key`, `token`, `password`, `secret`, `sig`, `signature`, and others)
  are replaced with `REDACTED` in every URL Chronicle writes to a manifest,
  the run index, or `project_id_for`. Rotating a key no longer starts a new
  project.

- **`robots.txt` respected by default** (`fetch.py`, CORE principle 7).
  RFC 9309 semantics: a missing robots.txt (4xx) allows everything; a
  robots.txt that cannot be reached (5xx or network error) refuses
  everything. Parsed per host, cached for the fetcher's lifetime.
  `Crawl-delay` is honoured. Pass `respect_robots=False` only when you are
  allowed to.

- **`Retry-After` honoured** on 429 and 503 (`fetch.py`). Accepts both
  seconds and HTTP-date formats. A server asking for longer than
  `MAX_RETRY_AFTER_SECONDS` (120s) raises a `FetchError` rather than
  freezing the caller.

- **Per-host rate limiting** (`HostRateLimiter` in `fetch.py`). One
  independent `RateLimiter` per host. Previously per-instance, so two
  `Scrape` objects against the same site could double the effective rate.

- **`_version.py`** — single source of truth for the package version.
  Imported by `__init__.py`, the HTTP user agent, and the run manifest so
  the three cannot disagree.

- **`ConfigurationError`** — raised when `Scrape` is set up in a way that
  cannot work (e.g. `selectors` with a JSON response, `records_path` with
  HTML, `headers` with Playwright, an unknown `key` column). Fails fast,
  before any network call, and never gets swallowed as a "failed page."

- **`FetchResponse`** dataclass — the body plus `content_type`, `status`,
  and `url`. Adds `Fetcher.fetch()` in addition to the existing
  `Fetcher.get()`.

### Changed

- **`Fetcher.use_cache` defaults to `False`** (`fetch.py`,
  `playwright_fetcher.py`). Drift detection needs fresh HTML; caching is
  now opt-in. `use_cache=True` also caches the response's content type and
  status in a sidecar `.meta.json` file.

- **`Fetcher` and `PlaywrightFetcher` construct and re-use their HTTP
  client lazily.** Closing a fetcher no longer prevents a `Scrape` object
  from running again.

- **`Scrape` closes its fetcher when `run()` returns** (via `try/finally`).
  Fixes a browser-process leak when the run exits with an exception.

- **`Scrape._page_url`** now replaces any existing page parameter on the
  URL rather than blindly appending one. Uses `urlsplit`/`parse_qsl` so
  fragments and repeated parameters behave.

- **`.github/workflows/tests.yml`** — `pytest --cov=chronicle` runs
  directly in CI (the `--cov` flags were removed from `addopts` so local
  test runs are faster). `ruff` is pinned to `0.17.0` so lint results are
  reproducible.

- **`pyproject.toml`** — `[tool.ruff.lint.per-file-ignores]` added for
  `clean.py` (DTZ001, DTZ007), `test_clean.py` (DTZ001), and
  `test_fetch.py` (SIM117). The naive datetimes and nested `with` blocks
  in those files are intentional.

### Fixed

- **Numeric columns containing `0` or `1` were read as booleans, and the
  run failed to save.** A price column with values `[0, 1, 5, 12]` came
  back as `[False, True, 5, 12]`, and the resulting mixed-type column
  could not be written to Parquet — so no manifest, no index entry, and
  the failed run was invisible in history. Without a schema, only
  `true/false/yes/no` are now inferred as booleans. Declared `bool` schema
  fields behave unchanged. (`normalize.py`)

- **One `N/A` in a numeric column lost the whole run.** Column-level type
  inference (`_resolve_columns`) now gives each column a single type:
  numbers + missing markers stay numeric with the markers counted as
  missing and reported; numbers mixed with real text fall back to the
  original text with a note. Storage adds an Arrow-safe fallback
  (`_arrow_safe`) that stores any remaining mixed column as text and
  records the degradation in `manifest.errors`. (`normalize.py`,
  `storage.py`)

- **`"12,5"` was read as 125** when inferring types. It is now kept as
  text and recorded as `ambiguous_number`. `"1,234"` still reads as 1234.

- **`"2023-05-01"` was becoming `2023.0`** in a numeric field. Date-shaped
  strings are no longer parsed as their leading integer; they are flagged.

- **`"12-15"` was becoming `12.0`** and `"4.5"` in an int field was
  truncating to `4`. Both now become missing and are recorded as
  `partial_parse` and `lossy_int` respectively. Required fields then
  quarantine the row. (`normalize.py`)

- **Leading zeros were dropped.** `"00123"`, `"0803..."` (SKUs, phone
  numbers, ZIP codes) stay as text, even when the whole column would
  otherwise be numeric.

- **`£`, `€`, `¥`, `₦`, `₹`, and `%` are now recognised** around a number,
  not just `$`. Em dashes (`—`) and en dashes (`–`) count as missing
  markers.

- **A torn `baseline.json` permanently bricked the project.** Now written
  via a temp file plus `os.replace` (atomic on POSIX, best-effort on
  Windows).

- **Empty response bodies aborted the whole run.** A page that fetches
  but cannot be parsed is now reported as a failed page (in
  `result.errors`), the run continues, and the failure is visible in the
  manifest.

- **Mixed-type `DataFrame` cells crashed profile generation.** List- and
  dict-valued cells (from JSON arrays and objects) are hashed via `repr`
  before counting unique values. (`profile.py`)

- **A column that is empty in every row is now warned about.**
  `report.warnings` names the column so the user can check the selector or
  field path. A wrong selector used to return an all-null column with no
  signal on the first run.

- **Table cells with duplicate headers, extra cells, or short rows no
  longer lose data.** Duplicate header names are made unique
  (`Total`, `Total_2`), extra cells get their own `col_N` columns, and
  short rows are padded with `None`. (`parse.py`)

- **Selectors containing `@` in an attribute value are no longer
  mis-parsed.** `a[href^="mailto:x@y"]` is treated as an ordinary CSS
  selector; only `attribute@attr` and `selector@text` use the `@` syntax.
  (`parse.py`)

- **`Fetcher` no longer retries a malformed URL.** `httpx.UnsupportedProtocol`
  and `httpx.InvalidURL` fail on the first attempt.

- **Fetch failures during `run()` close the fetcher even on exception.**
  Fixes a browser-process leak when the last page raises.

- **`project_id_for(url)` redacts the URL first**, so a rotated API key
  keeps the same project and therefore the same run history.

- **SQLite connections use `with closing(...) as conn, conn:`** so both
  the connection and the transaction are closed and committed cleanly.
  Previously a crash between `execute` and `commit` could leave the index
  inconsistent.

### Known limitations

- **Schema-path numeric coercion still takes the first number** in text
  such as `"12-15"` (recorded as `partial_parse`, not yet rejected).
- **Only four-digit years** are auto-detected in numeric dates; two-digit
  years need `date_formats`.
- **English month names only.** Non-English dates are flagged.
- **Timezone handling is strict**: a column that mixes timezone-aware and
  naive values is not converted; nothing is guessed.
- **The cache directory is shared** between the httpx `Fetcher` and the
  `PlaywrightFetcher`; a `pw_` prefix keeps them from colliding, but the
  two caches are not coordinated.

### Still open

The following audit findings remain unaddressed in this release and are
tracked for the next development cycle:

- **F-03 (partial)**: storage is not yet fully transactional. `baseline.json`
  is written atomically; the run directory and the SQLite index are not
  published together with a status column, and there is no orphan-recovery
  routine or content-hash verification.
- **F-04**: no baseline management API. Promotion, reset, rollback, and
  versioned baselines are not implemented; the baseline remains the first
  successful run until deleted by hand.
- **F-05**: the drift detector still has blind spots. Distribution changes
  with the same mean and range, categorical drift, dtype drift, and
  duplicate-rate changes are not detected. Near-zero-mean columns still
  produce false alarms.
- **F-08**: pagination strategies beyond `?page=N` are not implemented.
  Path-based, cursor-based, and next-link pagination are not supported.
- **F-09**: no SSRF protection. Loopback, private, link-local, and cloud
  metadata addresses are not blocked; redirects are not re-validated.
- **F-11**: no response-size cap or parse-time limit.
- **F-13**: no full provenance record (content hashes, dependency versions,
  per-page HTTP metadata) and no offline `replay(run_id)` API.
- **Test coverage**: `cli.py` and `playwright_fetcher.py` remain the least
  covered modules.

### Notes

- Drift detection still compares against the long-term baseline; the new
  diff compares against the previous run. They answer different questions
  and both run automatically on every `Scrape.run()` when a prior run
  exists.

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