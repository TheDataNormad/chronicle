# Chronicle

> **The web scraping library that remembers.**

Chronicle is a data-science-native web scraping framework with **temporal versioning** and **drift detection** built in. Every scrape is stored, profiled, and compared to history — so you know exactly when your data changes and why.

[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue.svg)](https://pypi.org/project/chronicle-ds/)
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

df = Scrape("https://example.com/products").to_dataframe()
print(df.head())
```

> **Note on auto-detection:** The one-liner works best on pages with
> HTML `<table>` elements or simple repeated list structures. It can
> pick the wrong structure on pages with navigation chrome (news sites,
> SPAs). For those, pass `selectors={...}` — see "More Control" below.

## More Control

```python
from chronicle import Scrape

df = Scrape(
    url="https://example.com/products",
    selectors={
        "_container": "div.product",
        "name": "h2",
        "price": "span.price",
        "rating": "span.rating",
    },
    pages=5,
    rate_limit=2.0,
).to_dataframe()
```

---

## The Core Idea

Chronicle treats every scrape as a **versioned event**. This unlocks two things no other scraping library provides:

### 1. Temporal Versioning

Every run is stored with:
- Raw HTML
- Parsed data (Parquet)
- Statistical profile
- Run metadata (timestamps, config, library version)

```python
# Query the full history of a scraper
history = Scrape.history("https://example.com/products")
history.drift_timeline()
```

### 2. Drift Detection

Chronicle compares each new run against a baseline and detects:
- **Schema drift** — columns added, removed, or renamed
- **Null drift** — fields suddenly returning `None`
- **Distribution drift** — mean, median, or variance shifts
- **Range drift** — values outside historical bounds
- **Selector health** — selectors returning zero elements

```python
result = Scrape("https://example.com/products").run()

if result.drift_detected:
    print(result.drift_report())
    # ⚠️ price null% jumped from 0.02 → 0.98 (selector likely broken)
```

---

## Status

🚧 **Early development.** v0.1.0 in progress.

### What's built
- [x] HTTP layer (`fetch.py`) — retries, rate limiting, caching
- [x] HTML parsing (`parse.py`) — selector + auto table/list detection
- [x] Schema validation (`normalize.py`) — pydantic + quality reports
- [x] `Scrape` class (`core.py`) — one-line API
- [x] Storage + versioning (`storage.py`) — versioned artifacts + SQLite
- [x] Profile generation (`profile.py`) — statistical fingerprints
- [x] Drift detection (`drift.py`) — null / distribution / range / schema
- [x] CLI (`cli.py`) — version, scrape, runs commands
- [x] 18 passing unit tests
- [x] Published to PyPI as `chronicle-ds`

### Roadmap
- Data contracts (schema + quality guarantees)
- Reproducibility artifacts
- Self-healing selectors
- Playwright support for JS-rendered pages
- CLI + recipe system

---

## Contributing

Chronicle is in early, fast-moving development. Issues, ideas, and PRs are welcome.

```bash
git clone git@github.com:TheDataNormad/chronicle.git
cd chronicle
python -m venv .venv
source .venv/Scripts/activate  # Windows
pip install -e ".[dev]"
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

Built by [@TheDataNormad](https://github.com/TheDataNormad).