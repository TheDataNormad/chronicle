# Chronicle

> **The web scraping library that remembers.**

Chronicle is a data-science-native web scraping framework with **temporal versioning** and **drift detection** built in. Every scrape is stored, profiled, and compared to history — so you know exactly when your data changes and why.

[![tests](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml/badge.svg)](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.2-blue.svg)](https://pypi.org/project/chronicle-ds/)
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
| Scrapy | Large-scale crawling | No drift detection, no reproducibility |
| Selenium | JS-rendered pages | No data pipeline, no versioning |
| lxml | Fast parsing | No fetching, no schema |

Chronicle sits **on top of** these tools and adds the layer data scientists actually need:

- 🕐 **Temporal versioning** — every scrape is stored, versioned, and replayable
- 🔍 **Drift detection** — catch broken selectors before they poison your pipeline
- 📊 **DataFrame-native** — one line from URL to analysis-ready data
- 📜 **Reproducible** — every run produces a self-contained artifact
- ✅ **Schema validation** — data quality enforced at scrape time
- 🧠 **DS-first design** — built for Jupyter, pandas, and real pipelines

---

## Install

```bash
pip install chronicle-ds
```

Chronicle requires **Python 3.10+**.

---

## Quickstart

```python
from chronicle import Scrape

# Works best on pages with a clear HTML <table>
df = Scrape(
    "https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos"
).to_dataframe()

print(df.head())
```

> **Note on auto-detection:** The one-liner works on pages with clear HTML
> `<table>` elements. For anything else — product grids, news sites, SPAs,
> JSON APIs — pass `selectors={...}`. See "More Control" below.

## More Control

Selector mode works on any HTML page, no matter how it's structured:

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
    pages=5,
    rate_limit=2.0,
).run()

df = result.to_dataframe()
print(df.head())
```

---

## The Core Idea

Chronicle treats every scrape as a **versioned event**. Every run writes an artifact to `.chronicle/`:

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

On the second and subsequent runs, Chronicle compares the new profile to the baseline and flags:

- **Schema drift** — columns added, removed, or renamed
- **Null drift** — fields suddenly returning `None`
- **Distribution drift** — mean, median, or variance shifts
- **Range drift** — values outside historical bounds
- **Selector health** — a column that used to be full is now mostly empty

```python
from chronicle import Scrape

result = Scrape("https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos").run()

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

### Quality Reports

Every `ScrapeResult` carries a `quality_report()` — row counts, success rate, per-column null %, dtype, and stats for numeric columns.

```python
result.quality_report()
# {
#   "rows_total": 100,
#   "rows_valid": 98,
#   "rows_invalid": 2,
#   "success_rate": 0.98,
#   "columns": {
#     "price": {"dtype": "float64", "null_pct": 0.02, "mean": 145.3, ...},
#     ...
#   }
# }
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

## Status

🚧 **Alpha — v0.1.2.** Actively developed. APIs may shift before v1.0.

### What's built
- [x] HTTP layer (`fetch.py`) — retries, exponential backoff, rate limiting, file cache
- [x] HTML parsing (`parse.py`) — selector mode + auto table detection
- [x] Schema validation (`normalize.py`) — pydantic-based, quarantines invalid rows
- [x] `Scrape` class (`core.py`) — one-line API, pagination, `ScrapeResult`
- [x] Storage (`storage.py`) — versioned artifacts + SQLite run index
- [x] Profile generation (`profile.py`) — statistical fingerprint of each run
- [x] Drift detection (`drift.py`) — null / distribution / range / schema / selector
- [x] CLI (`cli.py`) — `version`, `scrape`, `runs` commands
- [x] 18 unit tests, CI across Ubuntu / Windows / macOS × Python 3.10 / 3.11 / 3.12
- [x] Published to PyPI as `chronicle-ds`

### Roadmap
- **Playwright support** — JS-rendered pages (YouTube, most modern SPAs)
- **JSON API mode** — first-class handling of sites that serve data via internal JSON APIs
- **History API** — `Scrape.history(url)` and `drift_timeline(url)` for time-series queries
- **Data contracts** — declare "good data" (completeness, ranges, uniqueness) and fail the run when violated
- **Self-healing selectors** — auto-detect broken selectors and suggest alternatives

---

## Contributing

Chronicle is in early, fast-moving development. Issues, ideas, and PRs are welcome.

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
```

---

## What changed (and why each change matters)

| Change | Reason |
|--------|--------|
| Badge: `v0.1.0` → `v0.1.2` | Matches what's on PyPI |
| "v0.1.0 in progress" → "Alpha — v0.1.2" | Truthful status |
| Quickstart URL: `example.com/products` → real Wikipedia URL | The one-liner only works on tables — this example actually works |
| Removed `Scrape.history()` and `drift_timeline()` examples | **Those methods don't exist yet.** Advertising fake APIs is the fastest way to lose user trust. Moved to roadmap. |
| `result.drift_detected` → `result.has_drift()` | Correct API — it's a method, not a property |
| `result.drift_report()` → `result.drift_report.pretty()` | `drift_report` is an attribute; `.pretty()` is the method that formats it |
| "auto table/list detection" → "auto table detection" | We removed `detect_lists` in v0.1.1 |
| Added real storage layout diagram | Shows people what `.chronicle/` actually contains |
| Added quality report example | It's a built feature, should be visible |
| Added schema validation example | Same reason |
| Roadmap reordered by priority | Playwright + JSON API are the real next moves |
| Contributing: added PowerShell activation | Windows users were getting stuck |

---