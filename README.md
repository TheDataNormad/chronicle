# Chronicle

> **The web scraping library that remembers.**

Chronicle is a data-science-native web scraping library. It stores every run, profiles the result, and compares it to history — so silent changes in a page's structure or content are visible **before they poison your pipeline.**

[![tests](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml/badge.svg)](https://github.com/TheDataNormad/chronicle/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v0.2.1-blue.svg)](https://pypi.org/project/chronicle-ds/)
[![GitHub main](https://img.shields.io/badge/github-v0.3.0--alpha-orange.svg)](https://github.com/TheDataNormad/chronicle)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status--limitations)

[![Follow on LinkedIn](https://img.shields.io/badge/Follow%20on%20LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/chronicle-ds)

> ⚠️ **Alpha software.** Two versions are current:
>
> - **v0.2.1** is on [PyPI](https://pypi.org/project/chronicle-ds/). Fine for text data; has known numeric-handling bugs.
> - **v0.3.0-alpha** is on `main`. Fixes the numeric bugs and adds run-to-run diff, date cleaning, and a JSON connector. **Not on PyPI yet.**
>
> See [Status & Limitations](#status--limitations) before relying on either.

### New in v0.3.0-alpha

Run-to-run **diff**, a four-step **date confidence ladder**, a **JSON API connector**, `robots.txt` and `Retry-After` support, and fixes for every numeric-handling bug in v0.2.1. [Jump to what's new →](#whats-new)

## Why Chronicle?

- **Reproducible**: every run keeps raw HTML, parsed data, config, and a statistical profile
- **Two comparisons**: a row-by-row diff vs. the last run, and a statistical drift check vs. a baseline
- **Honest data**: ambiguous values are flagged with the original text, never guessed
- **DataFrame-native**: one line from URL to a pandas DataFrame
- **Local-first**: files plus SQLite, no server and no account

---

## Quick Start

**1. Install** — see [Installation](#installation) for all options.

```bash
pip install chronicle-ds
```

**2. Scrape a page into a DataFrame**

```python
from chronicle import Scrape

df = Scrape(
    "https://en.wikipedia.org/wiki/List_of_most-viewed_YouTube_videos"
).to_dataframe()

print(df.head())
```

> Auto-detection supports HTML `<table>` elements only. For product grids, news lists, or SPAs, pass `selectors={...}` (see [Usage Examples](#usage-examples)).

**3. Run it again and see what changed**

```python
result = Scrape(url, selectors={...}, key="title").run()

if result.has_changes():
    print(result.changes.pretty())       # +3 added, -1 removed, ~5 changed
if result.has_drift():
    print(result.drift_report.pretty())  # SEVERE: price null % jumped
```

**4. Use the command line**

```bash
chronicle version
chronicle scrape <url>
chronicle runs
```

> The CLI is still thin (no `--selectors` flag yet). Use the Python API for selector mode.

---

## How It Works

```
 URL ──► Acquire ──► Refine ──► Remember ──► ScrapeResult
         fetch.py    parse.py    storage.py   .to_dataframe()
         playwright  clean.py    profile.py   .invalid_rows
         jsonapi.py  normalize   diff.py      .changes  .drift_report
                                 drift.py     .cleaning .errors
```

Five layers, each depending only on the ones below it: **Foundation → Acquisition → Refinement → Memory → Orchestration → Interface**. Full details in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Features

**🌐 Acquisition**
- **HTTP fetcher**: retries, exponential backoff, per-host rate limiting, `Retry-After`, `robots.txt` (RFC 9309), opt-in cache
- **Headless Chromium** via Playwright for JavaScript-rendered pages
- **JSON connector** for API endpoints (Shopify and World Bank shapes included)

**🧹 Refinement**
- **Selector mode** and **auto table detection** for HTML
- **Date cleaning** with a four-step confidence ladder; ambiguous dates become `NaT` with the original text preserved
- **Schema validation** with pydantic: coerces types, quarantines invalid rows instead of dropping them
- **Fail-fast selector validation**

**🕐 Memory**
- **Versioned runs**: raw HTML, parsed data, config, profile, diff, and cleaning report per run
- **Run-to-run diff**: keyed and keyless comparison with honest warnings
- **Drift detection**: zero-row, schema, null, mean, range, and selector-health checks
- **Quality reports**: row counts, success rate, per-column null %, dtypes, numeric stats
- **URL redaction** and microsecond-precision run IDs

**📊 Developer experience**
- DataFrame-native; built for Jupyter, pandas, and real pipelines
- 147 tests; CI on Ubuntu, Windows, and macOS across Python 3.10 to 3.12

---

## Installation

| Option | Command |
|--------|---------|
| Stable (v0.3.0) | `pip install chronicle-ds` |
| JS-rendered pages | `pip install "chronicle-ds[browser]"` then `playwright install chromium` |
| Latest development | `pip install "git+https://github.com/TheDataNormad/chronicle.git"` |
| Source development | see [Contributing](#contributing) |

Chronicle requires **Python 3.10+**.

> **Scraping numeric data (prices, ratings, counts)?** Install from git. v0.2.1 on PyPI has [known numeric bugs](#v021-on-pypi).

---

## Usage Examples

<details open>
<summary><b>📄 Selector mode (any HTML page)</b></summary>

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
```

`@attr` reads an attribute. Pagination currently appends or replaces `?page=N`; path-based and cursor-based pagination are on the roadmap.

</details>

<details>
<summary><b>⚡ JavaScript-rendered pages</b></summary>

```python
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

| Parameter | What it does |
|-----------|--------------|
| `use_playwright=True` | Use headless Chromium instead of httpx |
| `wait_for="<css>"` | Wait for a selector before parsing |
| `wait_until` | `"domcontentloaded"` (default), `"load"`, or `"networkidle"` |
| `cookies=[...]` | Pre-set cookies (consent walls, sessions) |

**Colab / Jupyter:** Playwright's sync API cannot run inside a live asyncio loop. Run the scrape in a separate thread:

```python
import threading
out = {}
def run():
    out["r"] = Scrape(url, selectors={...}, use_playwright=True).run()
t = threading.Thread(target=run); t.start(); t.join()
df = out["r"].to_dataframe()
```

Some sites (YouTube, Twitter, LinkedIn) detect headless browsers and serve reduced content. Use their official APIs. Chronicle does not evade bot detection.

</details>

<details>
<summary><b>🔌 JSON APIs (v0.3.0)</b></summary>

```python
result = Scrape(
    url="https://api.example.com/products.json",
    records_path="products",          # omit if only one list exists
    fields={"name": "title", "price": "variants.0.price"},
).run()
```

- Never guesses which list is the data; raises a clear error if several candidates exist
- Values keep their JSON types: `"00123"` stays `"00123"`
- Dotted paths index dicts by key and lists by integer

</details>

<details>
<summary><b>🔁 Changes since the last run (v0.3.0)</b></summary>

```python
result = Scrape(url, selectors={...}, key="title").run()

if result.has_changes():
    print(result.changes.pretty())
    result.changes.to_dataframe()   # one row per change
```

Pass `key=` so Chronicle can tell an edited value from a new row. Without it, only added and removed rows are reported, and Chronicle **never guesses a key**. If a run had failed pages or returned zero rows, the report says so in `warnings` instead of presenting removed rows as confirmed deletions.

</details>

<details>
<summary><b>📅 Dates and the cleaning report (v0.3.0)</b></summary>

`03/04/2026` could be April 3rd or March 4th, and a wrong guess looks valid. A date is converted only when exactly one reading is possible:

1. **Declared**: `date_formats={"published": "%d/%m/%Y"}` or `dayfirst=True`
2. **Unambiguous**: `2026-04-03`, `3 Apr 2026`, `25/04/2026`, `05/05/2026`
3. **Column evidence**: one value like `25/04/2026` settles day-first for every value in that style
4. **Otherwise flagged**: becomes `NaT`; original text kept in `result.cleaning`

```python
result = Scrape(url, selectors={...}, dayfirst=True).run()
print(result.cleaning.pretty())
result.cleaning.to_dataframe()   # every flagged value, original preserved
```

</details>

<details>
<summary><b>✅ Schema validation</b></summary>

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

result.to_dataframe()
result.to_invalid_dataframe()   # quarantined rows
```

Type coercion rules are documented in [docs/COERCION.md](docs/COERCION.md).

</details>

<details>
<summary><b>📈 Quality reports</b></summary>

```python
result.quality_report()
# rows_total, rows_valid, rows_invalid, success_rate,
# per-column dtype / null_pct / numeric stats
```

</details>

---

## The Core Idea: Every Run Is an Artifact

Every `Scrape(...).run()` writes to `.chronicle/`:

```
.chronicle/projects/<project_id>/
├── baseline.json           # reference profile for drift
└── runs/<run_id>/
    ├── manifest.json       # config, timestamps, library version
    ├── profile.json        # statistical fingerprint of each column
    ├── diff.json           # what changed since the previous run
    ├── cleaning.json       # what was converted, flagged, or left unconverted
    ├── data.parquet        # the validated DataFrame
    ├── invalid.parquet     # quarantined rows (if any)
    └── raw/page_1.html     # exact HTML received
```

### Two comparisons, two questions

| Question | Result attribute | Compares |
|----------|------------------|----------|
| *What changed since last time?* | `result.changes` (`DiffReport`) | current run vs **previous run**, row by row |
| *Is the shape still healthy?* | `result.drift_report` (`DriftReport`) | current profile vs **long-term baseline**, statistically |

Both run automatically on every `Scrape.run()` when a prior run exists. They are deliberately separate so you always know which one fired.

### Drift checks implemented today

| Check | Fires when |
|-------|------------|
| Row-count | A run returns 0 rows after a nonzero baseline (**SEVERE**; catches broken selectors) |
| Schema | Columns added or removed |
| Null | Per-column null % moves above thresholds |
| Mean | Numeric column mean shifts above thresholds (skipped if baseline mean is 0) |
| Range | Values fall outside baseline min/max by more than 2× |
| Selector health | A previously populated column is now almost entirely null |

### Politeness

`robots.txt` respected by default, `Retry-After` honoured on 429/503, per-host rate limiting. Caching is **off by default** because drift detection needs fresh data.

---

## What's New

**v0.3.0 (GitHub `main`)**
- 🔁 Run-to-run diff (`result.changes`), keyed and keyless
- 📅 Date cleaning with a four-step confidence ladder and `result.cleaning`
- 🔌 JSON connector (`records_path`, `fields`)
- 🤝 `robots.txt` and `Retry-After` support
- 🔢 Fixes for all numeric-handling bugs listed under v0.2.1
- 🧪 147 tests

**v0.2.1 (PyPI)**
- Versioned storage with SQLite run index, profile generation, drift detection, Playwright fetcher, pydantic schema validation, CLI, cache off by default

Full history in [CHANGELOG.md](CHANGELOG.md).

---

## Status & Limitations

### Current limitations (v0.3.0)

- **Storage is not fully transactional.** A crash mid-run can leave the run directory and SQLite index inconsistent; no content hashes or orphan recovery
- **No baseline management.** The baseline is the first successful run until you delete it by hand
- **Drift blind spots:** same-mean distribution changes, categorical drift, dtype drift, duplicate-rate changes; near-zero-mean columns can false-alarm
- **No SSRF protection:** loopback, private, link-local, and cloud-metadata addresses are not blocked; redirects are not re-validated
- **No response-size cap or parse-time limit**
- **No offline replay API, no async fetcher, thin CLI**

### Roadmap

- [x] HTTP fetcher with retries, rate limits, `robots.txt`, `Retry-After`
- [x] Playwright fetcher for JS-rendered pages
- [x] JSON connector
- [x] Date cleaning and cleaning report
- [x] Schema validation with quarantine
- [x] Versioned storage, profiles, drift detection
- [x] Run-to-run diff
- [x] **v0.3.0** — numeric bug fixes, date cleaning, run-to-run diff, JSON connector, `robots.txt` support
- [ ] **v0.3.x** *(planned)* — drift detector rework (categorical, dtype, same-mean distribution), baseline management, SSRF protection, pagination strategies
- [ ] **v0.4.0** *(planned)* — storage backend abstraction (local vs. cloud), async fetcher, RAD module
- [ ] **v0.5.0** *(not yet scheduled)* — data contracts, self-healing selectors

---

## Documentation

| Document | What's inside |
|----------|---------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Five-layer structure, data flow, design decisions |
| [CORE.md](CORE.md) | The ten principles the library is built on |
| [docs/COERCION.md](docs/COERCION.md) | The type coercion contract |
| [CHANGELOG.md](CHANGELOG.md) | Every release and what changed |

---

## Contributing

Chronicle is in early, fast-moving development. Issues, ideas, and PRs are welcome. Found a bug? [Open an issue](https://github.com/TheDataNormad/chronicle/issues) with a minimal reproduction.

```bash
git clone git@github.com:TheDataNormad/chronicle.git
cd chronicle
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# or: .\.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -e ".[dev]"
pytest
```

Before opening a PR, read [ARCHITECTURE.md](docs/ARCHITECTURE.md) (layers import downward only) and [CORE.md](CORE.md). Bug fixes should add a case to the matching end-to-end test.

---

## License

MIT. See [LICENSE](LICENSE).

## Citation

```bibtex
@software{chronicle2026,
  author = {TheDataNormad},
  title  = {Chronicle: The web scraping library that remembers},
  year   = {2026},
  url    = {https://github.com/TheDataNormad/chronicle}
}
```

## Contact

- **GitHub:** [@TheDataNormad](https://github.com/TheDataNormad)
- **LinkedIn:** [linkedin.com/company/chronicle-ds](https://www.linkedin.com/company/chronicle-ds)
- **PyPI:** [pypi.org/project/chronicle-ds](https://pypi.org/project/chronicle-ds/)

---

*Happy scraping, and remember everything.* 🕰️