# Chronicle

> **The web scraping library that remembers.**

Chronicle is a data-science-native web scraping framework that stores every run, profiles the result, and compares it to a baseline — so silent changes in a page's structure or content are visible before they poison a pipeline.

**⚠️ Alpha software.** Chronicle is v0.2.1. Drift detection works for a specific set of checks (see [Limitations](#limitations-v021)). It does not yet catch every kind of change. Read the limitations section before relying on it.

[![tests](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml/badge.svg)](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v0.2.1-blue.svg)](https://pypi.org/project/chronicle-ds/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

---

## Why Chronicle?

Existing scraping libraries hand you raw data and walk away. **Chronicle remembers.**

| Tool | Strength | The gap Chronicle fills |
|------|----------|------------------------|
| Requests | HTTP requests | No parsing, no structure |
| BeautifulSoup | HTML parsing | No fetching, no data quality |
| Scrapy | Large-scale crawling | No cross-run comparison, no reproducibility |
| Selenium | JS-rendered pages | No data pipeline, no versioning |
| lxml | Fast parsing | No fetching, no schema |

Chronicle sits **on top of** these tools and adds:

- 🕐 **Versioned runs** — every scrape is stored with raw HTML, parsed data, config, and a statistical profile
- 🔍 **Drift detection** — compares each run to a baseline and flags schema, null, mean, and range changes
- 📊 **DataFrame-native** — one line from URL to a pandas DataFrame
- ✅ **Schema validation** — pydantic models coerce types and quarantine invalid rows instead of dropping them
- 🌐 **Two fetchers** — fast HTTP for static pages, headless Chromium for JS-rendered SPAs

---

## Install

```bash
pip install chronicle-ds
```

Chronicle requires **Python 3.10+**.

For JS-rendered pages:

```bash
pip install "chronicle-ds[browser]"
playwright install chromium
```

---

## Quickstart

```python
from chronicle import Scrape

# Works on pages that contain a clear HTML <table>.
df = Scrape(
    "https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos"
).to_dataframe()

print(df.head())
```

> **Auto-detection** only supports HTML `<table>` elements. For product grids,
> news lists, or SPAs, pass `selectors={...}` — see below.

## More Control

Selector mode works on any HTML page:

```python
from chronicle import Scrape

result = Scrape(
    url="http://books.toscrape.com/",
    selectors={
        "_container": "article.product_pod",
        "title": "h3 > a@title",
        "price": "p.price_color",
        "stock": "p.instock.availability",
        "link":  "h3 > a@href",
    },
    rate_limit=2.0,
).run()

df = result.to_dataframe()
print(df.head())
```

**Pagination** currently appends `?page=N` to the URL. This works for sites
with query-string pagination, not for path-based pagination
(e.g. `books.toscrape.com` uses `/catalogue/page-N.html`). Per-site pagination
templates are on the roadmap.

## JS-rendered pages

For pages that load content via JavaScript:

```python
from chronicle import Scrape

result = Scrape(
    url="https://quotes.toscrape.com/js/",
    selectors={
        "_container": "div.quote",
        "text":   "span.text",
        "author": "small.author",
    },
    use_playwright=True,
    wait_for="div.quote",
).run()
```

**Key parameters:**

| Parameter | What it does |
|-----------|--------------|
| `use_playwright=True` | Use headless Chromium instead of httpx |
| `wait_for="<css>"` | Wait for a selector to appear before parsing |
| `wait_until` | `"domcontentloaded"` (default), `"load"`, or `"networkidle"` |
| `cookies=[...]` | Pre-set cookies (consent walls, sessions) |

> **Colab / Jupyter note:** Playwright's sync API cannot run inside a live
> asyncio loop. On those platforms, run the scrape in a separate thread:
>
> ```python
> import threading
> result = {}
> def run():
>     result["r"] = Scrape(url, use_playwright=True, ...).run()
> t = threading.Thread(target=run)
> t.start(); t.join()
> df = result["r"].to_dataframe()
> ```

**Known limitations:** Some sites (YouTube, Twitter, LinkedIn) detect
headless browsers and serve reduced content. Use their official APIs.
A Playwright async fetcher is on the roadmap.

---

## The Core Idea

Every `Scrape(...).run()` writes an artifact to `.chronicle/`:

```
.chronicle/projects/<project_id>/
├── baseline.json           # reference profile for drift
└── runs/<run_id>/
    ├── manifest.json       # config, timestamps, library version
    ├── profile.json        # statistical fingerprint of each column
    ├── data.parquet        # the validated DataFrame
    ├── invalid.parquet     # quarantined rows (if any)
    └── raw/page_1.html     # exact HTML received
```

### Drift Detection

On the second and subsequent runs, Chronicle compares the new profile to the
baseline. **Currently implemented checks:**

- **Row-count drift** — current run returns 0 rows after a nonzero baseline (SEVERE, catches broken selectors)
- **Schema drift** — columns added or removed
- **Null drift** — per-column null % changes above thresholds
- **Mean drift** — numeric column means shifting above thresholds (skipped if the baseline mean is 0)
- **Range drift** — numeric values falling outside the baseline's min/max by more than 2×
- **Selector health** — a column that used to be populated is now almost entirely null

```python
from chronicle import Scrape

result = Scrape(
    "https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos",
).run()

if result.has_drift():
    print(result.drift_report.pretty())
```

Output:

```
======================================================================
DRIFT REPORT   severity: SEVERE
======================================================================

🔴 [SEVERE] Views (null)
    Null % jumped from 0.00% to 97.00% — selector likely broken
    baseline: 0.0  →  current: 0.97
```

> **On caching:** the HTTP cache is **off by default** since v0.2.1.
> Drift detection needs fresh HTML on each run to be meaningful. Pass
> `use_cache=True` when iterating on selectors locally and you want to
> avoid re-fetching.

### Quality Reports

Every `ScrapeResult` carries a `quality_report()` — row counts, success rate, per-column null %, dtype, and stats for numeric columns.

```python
result.quality_report()
```

### Schema Validation

Pass a pydantic model and Chronicle coerces types, validates each row, and quarantines the ones that fail — instead of silently dropping them.

```python
from pydantic import BaseModel
from chronicle import Scrape

class Video(BaseModel):
    title: str
    views: float | None = None

result = Scrape(
    url="https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos",
    schema=Video,
).run()

print(result.to_dataframe().head())
print(result.to_invalid_dataframe().head())   # rows that failed
```

---

## Limitations (v0.2.1)

This section exists because we'd rather you know the boundaries than trust a
feature that isn't there yet.

### Drift detection

- **Only numeric columns are profiled for drift.** String/categorical columns
  are not checked for distribution or category changes.
- **Median, variance, and KS-test drift are not implemented.** Only mean and
  min/max are compared.
- **The baseline is set from the first run and never updates automatically.**
  No reset, no promotion command. Delete
  `.chronicle/projects/<id>/baseline.json` to reset manually.
- **Comparison is only to the baseline**, not also to the previous run. On the
  roadmap for v0.3.0.
- **Dtype drift is not detected.** A column that was `float64` in the baseline
  and is `object` now will not raise a drift finding on its own.
- **Extra schema fields are silently ignored.** If the current run returns a
  column the schema doesn't declare, it is not surfaced as drift.

### Parsing

- **Integer coercion truncates silently.** A value like `12.9` in an `int`
  field is currently converted to `12` without warning. Fix is planned for
  v0.3.0 as a P0 correctness issue.
- **Pagination is `?page=N` only.** Path-based pagination (e.g.
  `books.toscrape.com/catalogue/page-N.html`) is not supported.
- **Currency parsing** strips only `$`. `£`, `€`, and suffixes like `1.2B` are
  not handled. `coerce_to_float` grabs the first numeric-looking run, so
  `"2023-05-01"` becomes `2023.0`.

### Storage

- **One bad column can fail the whole run.** Mixed-type columns (e.g.
  `"1,000"` / `"900"` / `"N/A"`) can cause the Parquet write to fail, which
  means no manifest is written and no baseline is saved.
- **Raw HTML is stored uncompressed.**

### Errors

- **`ParseError` is not caught inside `run()`.** A page that fails to parse
  ends the whole scrape.
- **The CLI has no `--selectors` flag**, so `chronicle scrape <url>` only
  works on table pages.
- **No `robots.txt` check.** Respect target sites manually.
- **Rate limiting is per-instance, not per-host.** The `RateLimiter` is
  attached to each `Fetcher`, so multiple `Scrape` instances targeting the
  same host will not coordinate. The docstring is being corrected in v0.3.0.

### Coverage

- `test_fetch.py` and `test_profile.py` are still empty.
- `cli.py` and `playwright_fetcher.py` are at 0% test coverage.
- The end-to-end pipeline test (`tests/test_end_to_end_drift.py`) covers the
  full scrape → store → drift path.

### Replay

- The storage is designed to be replayable, but there is no `replay(run_id)`
  API yet. `storage.load_run(project_id, run_id)` exists as a low-level
  method.

---

## Status

🚧 **Alpha — v0.2.1.** Actively developed. Expect breaking changes before v1.0.

### What's built
- [x] HTTP layer (`fetch.py`) — retries, exponential backoff, per-instance rate limiting, file cache (opt-in)
- [x] Playwright fetcher (`playwright_fetcher.py`) — headless Chromium for JS-rendered pages
- [x] HTML parsing (`parse.py`) — selector mode + auto table detection (with caveats above)
- [x] Schema validation (`normalize.py`) — pydantic-based, quarantines invalid rows
- [x] `Scrape` class (`core.py`) — one-line API, `ScrapeResult`, fail-fast selector validation
- [x] Storage (`storage.py`) — versioned artifacts + SQLite run index, microsecond-precision run IDs
- [x] Profile generation (`profile.py`) — per-column statistical fingerprint
- [x] Drift detection (`drift.py`) — zero-row, schema, null, mean, range, selector health
- [x] CLI (`cli.py`) — `version`, `scrape`, `runs` commands (limited; see above)
- [x] 23 tests (18 unit + 5 end-to-end pipeline), CI across Ubuntu / Windows / macOS × Python 3.10 / 3.11 / 3.12
- [x] Published to PyPI as `chronicle-ds`

### Roadmap (priority order)
1. **v0.3.0** — integer coercion safety (P0), dtype drift, categorical drift, statistical drift integration (KS/PSI), extra-field surfacing, row-count drift, baseline management, per-column type inference, path-based pagination
2. **v0.3.x** — CLI `--selectors` and `drift` commands, `robots.txt`, `Retry-After` handling, cache TTL, per-host rate limiter, `replay(run_id)`
3. **v0.4** — async Playwright fetcher, JSON API mode, data contracts, self-healing selectors

---

## Follow

- **LinkedIn:** [linkedin.com/company/chronicle-ds](https://www.linkedin.com/company/chronicle-ds)
- **PyPI:** [pypi.org/project/chronicle-ds](https://pypi.org/project/chronicle-ds/)
- **GitHub:** [github.com/TheDataNormad/chronicle](https://github.com/TheDataNormad/chronicle)

---

## Contributing

Chronicle is in early, fast-moving development. Issues, ideas, and PRs are
welcome. If you find a bug, [open an issue](https://github.com/TheDataNormad/chronicle/issues)
with a minimal reproduction.

```bash
git clone git@github.com:TheDataNormad/chronicle.git
cd chronicle
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# or: .\.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -e ".[dev]"
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

Built by [@TheDataNormad](https://github.com/TheDataNormad).