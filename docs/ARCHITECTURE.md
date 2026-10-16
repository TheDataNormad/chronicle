# Chronicle — Architecture

This document describes the structure of Chronicle: what each module is
responsible for, how data flows through the system, and the design
decisions that shape the code. It is the reference for anyone modifying,
reviewing, extending, or evaluating the project.

It is written for four audiences. Skip to what you need:

| If you are… | Start here |
|-------------|------------|
| **A new user** evaluating whether Chronicle fits your project | [§1 What Chronicle is](#1-what-chronicle-is-and-isnt) |
| **A contributor** adding a feature or fixing a bug | [§3 Five-layer model](#3-the-five-layer-model) and [§7 Extensibility](#7-extensibility-where-to-add-new-things) |
| **A capstone examiner** evaluating the design | [§2 The problem](#2-the-problem-this-architecture-solves) and [§6 Design decisions](#6-design-decisions-and-tradeoffs) |
| **A future maintainer** inheriting the project | Read all of it, especially [§9 Limitations](#9-architectural-limitations) |

For the *principles* the architecture serves, see [`../CORE.md`](../CORE.md).
For *what exists today* versus *what is planned*, see the README's Status
section and [`../CHANGELOG.md`](../CHANGELOG.md).

---

## 1. What Chronicle is (and isn't)

**Chronicle is a data-acquisition library for data scientists.**

It fetches web pages, turns them into typed pandas DataFrames, stores
every run with full reproducibility, and tells you what changed between
runs — both statistically (drift) and row by row (diff).

**Chronicle is not a scraping framework.**

It does not try to be Scrapy. There is no crawl frontier, no request
scheduler, no distributed queue, no plugin registry, no YAML-based rule
engine. It is a library you `import` and call, designed for notebook use
and small-to-medium pipelines.

**Chronicle is not a data platform.**

There is no server, no database tier, no job runner, no cloud account
required. Storage is files plus a SQLite index. The whole thing runs on
one laptop.

Those two choices — *library, not framework* and *local, not cloud* —
shape every architectural decision below.

---

## 2. The problem this architecture solves

Chronicle answers a question most scraping libraries ignore:

> **"Is the data I just collected still the data I thought I was
> collecting?"**

Every scraper in the world fetches data. Almost none of them remember
what yesterday's data looked like. So when a website changes quietly — a
CSS class renamed, a field silently empty, a category list replaced — the
scraper keeps running. It keeps returning data. It just keeps being
wrong.

Fixing that requires three things that don't fit in a single script:

1. **Persistence** — every run has to be stored, not overwritten.
2. **Comparison** — the current run has to be diffed against history.
3. **Honesty** — ambiguous values must be flagged, not guessed.

Each of those is a distinct responsibility. The architecture separates
them so that each can be reasoned about, tested, and replaced
independently.

---

## 3. The five-layer model

Chronicle is organised as five layers. Each layer depends only on the
layers below it — never on the layers above.

```
┌───────────────────────────────────────────────────────────────────┐
│  Interface       cli.py · __init__.py                             │
│                  The user-facing surface.                         │
├───────────────────────────────────────────────────────────────────┤
│  Orchestration   core.py                                          │
│                  Wires the lower layers together.                 │
├───────────────────────────────────────────────────────────────────┤
│  Memory          storage.py · profile.py · diff.py · drift.py     │
│                  Remembers what was collected and what changed.   │
├───────────────────────────────────────────────────────────────────┤
│  Refinement      parse.py · clean.py · normalize.py               │
│                  Turns raw bytes into typed, honest data.         │
├───────────────────────────────────────────────────────────────────┤
│  Acquisition     fetch.py · playwright_fetcher.py · jsonapi.py    │
│                  Gets bytes from the web.                         │
├───────────────────────────────────────────────────────────────────┤
│  Foundation      exceptions.py · _version.py                      │
│                  Primitives every layer uses.                     │
└───────────────────────────────────────────────────────────────────┘
```

### The dependency rule

**A layer may import from the layers below it. A layer may not import
from the layers above it.**

Concretely:

- ✅ `fetch.py` may import from `exceptions.py`
- ✅ `core.py` may import from `storage.py`
- ❌ `fetch.py` may **not** import from `core.py`
- ❌ `parse.py` may **not** import from `storage.py`
- ❌ `exceptions.py` may **not** import anything from Chronicle except itself

This rule is why the drift detector can be reasoned about without loading
the whole scrape pipeline, and why a change to storage never requires
reading the fetchers.

### Why layers, not modules-by-feature

An alternative design would organise by *feature* — a `fetchers/` folder,
a `parsers/` folder, a `drift/` folder, each containing the full stack
for that feature. That works for large teams with dedicated owners per
feature. It fails here because:

1. Chronicle is one person's project. Layers are easier to hold in your
   head than a feature matrix.
2. The comparison engine (`diff.py` / `drift.py`) has no business
   knowing how HTML is parsed. Layers make that separation visible.
3. Testing is easier when each layer has a clear input and output.

---

## 4. Data flow through one run

A single `Scrape(url).run()` call moves through all five layers:

```
  URL
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. ACQUISITION                                                  │
│    Fetcher.get(url)  →  FetchResponse(text, content_type, ...)  │
│    Or: PlaywrightFetcher.get(url)  →  rendered HTML             │
│    Or: direct JSON endpoint  →  JSON text                       │
└─────────────────────────────────────────────────────────────────┘
   │
   ▼  bytes (str)
┌─────────────────────────────────────────────────────────────────┐
│ 2. REFINEMENT                                                   │
│    parse_html / parse_json  →  list[dict] (raw strings)         │
│    clean_date_column        →  dates converted or flagged       │
│    normalize_rows           →  (valid_rows, invalid_rows,       │
│                                  cleaning_report)               │
└─────────────────────────────────────────────────────────────────┘
   │
   ▼  pandas.DataFrame + CleaningReport
┌─────────────────────────────────────────────────────────────────┐
│ 3. MEMORY                                                       │
│    generate_profile         →  statistical fingerprint          │
│    storage.save_run         →  versioned artifacts + index      │
│    storage.previous_run     →  the run before this one          │
│    compute_diff             →  DiffReport (row-by-row)          │
│    storage.load_baseline    →  long-term reference profile      │
│    detect_drift             →  DriftReport (statistical)        │
└─────────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. ORCHESTRATION                                                │
│    Scrape.run() returns a ScrapeResult with:                    │
│      .to_dataframe()      the validated DataFrame               │
│      .invalid_rows        quarantined rows                      │
│      .quality_report()    column stats                          │
│      .cleaning            what was converted / flagged          │
│      .changes             DiffReport vs previous run            │
│      .drift_report        DriftReport vs baseline               │
│      .errors              fetch / parse / storage failures      │
│      .manifest            run metadata                          │
└─────────────────────────────────────────────────────────────────┘
```

Each arrow is a real function call. Nothing is hidden behind a
middleware layer or a callback registry. If you want to know what
happens between "fetch" and "parse," you read `core.py`'s `run()` method
top to bottom.

---

## 5. Layer-by-layer responsibilities

Each module has a single, well-defined job. If a change doesn't fit in
one of these rows, it belongs somewhere else.

### Foundation

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `exceptions.py` | The exception hierarchy. `ChronicleError` at the root; `ScrapeError`, `ConfigurationError`, `FetchError`, `ParseError`, `SchemaError`, `StorageError` beneath it. | Deciding when to raise. |
| `_version.py` | Single source of truth for the version string. | Anything else. |

### Acquisition

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `fetch.py` | HTTP fetching with retries, `Retry-After`, per-host rate limiting, `robots.txt`, opt-in caching. Returns `FetchResponse`. | Parsing. Interpreting. Anything past "here are the bytes." |
| `playwright_fetcher.py` | Same interface as `fetch.Fetcher`, but renders JavaScript in a headless Chromium. | Bot evasion. Per CORE principle 9. |
| `jsonapi.py` | Locating a record list inside a JSON document, flattening nested objects, extracting named fields by dotted path. Never guesses which list. | Fetching. HTML parsing. |

### Refinement

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `parse.py` | CSS selector extraction and HTML table detection. Returns a list of dicts. | Type coercion. Cleaning. |
| `clean.py` | Date resolution with the four-step confidence ladder. Produces the `CleaningReport`. Refuses to guess. | Numeric coercion. Schema validation. |
| `normalize.py` | Schema-driven coercion, per-column type inference, quarantine of invalid rows. Attaches the `CleaningReport`. | Dates (handed to `clean.py`). Statistics. |

### Memory

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `profile.py` | Per-column statistical fingerprint of a DataFrame. The thing drift compares. | Comparing. Judging. |
| `storage.py` | Versioned run artifacts on disk, SQLite index, baselines, URL redaction, Arrow-safe writes. | Deciding when to store. Comparing runs. |
| `diff.py` | Row-by-row comparison between two DataFrames. Never guesses which rows match. | Statistical comparison. Baselines. |
| `drift.py` | Statistical comparison between two profiles (baseline vs current). Produces a `DriftReport` with severities. | Row-by-row diffs. Storage. |

### Orchestration

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `core.py` | `Scrape` (constructor, `run()`, config) and `ScrapeResult` (the return type). Wires the four lower layers together. | Anything that could live in a lower layer. |

### Interface

| Module | Responsibility | Not responsible for |
|--------|---------------|---------------------|
| `cli.py` | Thin command-line wrapper over `Scrape`. | Business logic. |
| `__init__.py` | The public API surface: `Scrape`, `ScrapeResult`, `__version__`. | Internal modules. |

### The two comparison modes

This is the design decision that shapes the Memory layer. Chronicle
answers two different questions:

| Question | File | Compares | Answers |
|----------|------|----------|---------|
| "What changed since last time?" | `diff.py` | current run vs **previous run** | Which rows were added, removed, or edited |
| "Is the shape still healthy?" | `drift.py` | current profile vs **long-term baseline** | Has the distribution, null rate, or column set shifted |

Both run automatically on every `Scrape.run()` when a prior run exists.
They are deliberately separate because:

- A single row change is worth reporting even if the distribution didn't move (diff catches this).
- A distribution shift is worth reporting even if no individual row changed names (drift catches this).
- Merging them would hide which comparison fired and why.

**Never collapse these into one function.**

---

## 6. Design decisions and tradeoffs

Each decision below was made deliberately. For capstone purposes, the
"alternatives considered" column is the important one — it shows what
was rejected and why.

### 6.1 Schema-driven coercion, not automatic

**The rule:** The pydantic schema declares the intent. The library
executes. The library never guesses.

| Input | Declared schema | Behavior |
|-------|-----------------|----------|
| `"2023-05-01"` | `date` | → `date(2023, 5, 1)` |
| `"2023-05-01"` | `float` | ❌ Quarantine — ambiguous |
| `"42.99"` | `float` | → `42.99` |
| `"42.99"` | `int` | ❌ Quarantine — schema mismatch |
| `"€1.299,50"` | `float`, `locale="de_DE"` | → `1299.50` |
| `"€1.299,50"` | `float`, no locale | ❌ Quarantine — ambiguous |

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| Best-effort coercion (always convert if you can) | Produces plausible-looking wrong values. Violates CORE principle 4. |
| Ask the user on every ambiguous value | Impossible in a non-interactive pipeline. |
| Refuse to run when any value is ambiguous | Too brittle; one bad cell kills the run. |
| Quarantine + report (current) | The run continues; the user sees exactly what failed and why. |

**Consequences:** More rows end up in `invalid_rows` than a "smart"
coercer would produce. That is intentional. A quarantined row is honest;
a guessed row is a future bug.

### 6.2 Files plus SQLite, not a database server

**The rule:** All persistent state lives under `.chronicle/` in the
current working directory. Data is Parquet; index is SQLite; config is
JSON.

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| Postgres / MySQL | Requires the user to run a server. Fails the "works on a laptop" goal. |
| DuckDB | Great for analytics, but not needed — the run index is small. |
| A single JSON file | Doesn't scale to thousands of runs; no concurrent reads. |
| Files + SQLite (current) | No server, no account, easy to inspect, easy to back up, easy to delete. |

**Consequences:** Not multi-user. Two processes writing to the same
`.chronicle/` will race on the SQLite index. Acceptable for a
single-user library; a coordinated multi-writer mode is future work.

### 6.3 One fetcher interface, three implementations

**The rule:** All fetchers expose `get(url) -> str` (and the newer
`fetch(url) -> FetchResponse`). Adding a new fetcher means adding a new
class, not changing `core.py`.

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| A single fetcher with mode flags | Becomes a god-object. Hard to test. |
| A plugin registry with entry points | Overkill for one person. Adds packaging complexity. |
| A class hierarchy with base class | Adds ceremony; the interface is one method. |
| Duck-typed classes (current) | Simplest thing that works. |

**Consequences:** `core.py` uses `getattr(fetcher, "fetch", None)` to
fall back to the older `.get()` on custom fetchers. This is a small
compatibility shim, not a design pattern. A future `Protocol` class
would formalize this.

### 6.4 No anti-bot evasion, ever

**The rule:** Chronicle does not ship CAPTCHA solving, fingerprint
spoofing, proxy rotation, or credential circumvention.

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| Ship stealth mode (like Crawl4AI) | Violates CORE principle 9. Would require infrastructure Chronicle can't maintain. |
| Add opt-in evasion as an "extra" | Splits the library's identity. Some users would enable it by mistake. |
| No evasion, document the limit (current) | Honest. Keeps the library's ethical position unambiguous. |

**Consequences:** Some sites (YouTube, Twitter, LinkedIn) cannot be
scraped with Chronicle. Users are pointed to official APIs. That is
the correct answer, not a workaround.

### 6.5 Local-first, no server, no account

**The rule:** Chronicle is a Python package. `pip install` and go. No
signup, no API key, no telemetry, no phone-home.

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| Cloud service with local client | Adds infrastructure. Creates a business model. Not what the project is. |
| Local server with HTTP API | Adds a moving part. Not needed for notebook use. |
| Pure library (current) | Simplest. Works offline. Works in CI. Works forever. |

**Consequences:** No collaborative features (shared runs, team
dashboards). If those are wanted later, they are a new module layered
on top, not a rewrite.

### 6.6 Tests run against a local HTTP server

**The rule:** Every integration test uses `tests/_server.py`, a local
multi-route HTTP server, not a real website.

**Alternatives considered:**

| Alternative | Why rejected |
|-------------|--------------|
| Hit real websites in CI | Slow, flaky, and impolite to the target sites. |
| Use `responses` / `respx` mocking | Fine for unit tests, but doesn't exercise the real HTTP stack. |
| Local server (current) | Real HTTP, real sockets, deterministic results. |

**Consequences:** The tests can't catch site-specific bugs (a real
change to Wikipedia's HTML would not be caught by CI). That is fine —
site-specific bugs are found by users, not by unit tests.

---

## 7. Extensibility: where to add new things

If you want to add a feature, this table tells you where it goes.

| I want to add… | Add it to | Because |
|----------------|-----------|---------|
| A new fetcher (e.g. `curl`-based, gRPC) | A new module in Acquisition (`my_fetcher.py`) with `get()`/`fetch()` | Fetchers are duck-typed. |
| A new parser (e.g. RSS, XML) | A new function in `parse.py` (or a new `parse_*.py` module) | Parsing is a pure function of bytes → rows. |
| A new coercion rule (e.g. currency, unit parsing) | `normalize.py` or `clean.py` | Depends on whether it's per-value or per-column. |
| A new statistical check (e.g. variance drift) | `drift.py` — new function, called from `detect_drift()` | Drift checks are independent tests on two profiles. |
| A new row-comparison rule (e.g. fuzzy matching) | `diff.py` — new function called from `compute_diff()` | Same idea, different layer. |
| A new storage backend (e.g. S3) | A new class in `storage.py` implementing the same method signatures | Planned as v0.4.0 work; the current class is the reference. |
| A new comparison output format (e.g. HTML report) | A new method on `DiffReport` or `DriftReport` | The reports are dataclasses; adding render methods is safe. |
| A new CLI subcommand | `cli.py` — new `@app.command()` | CLI is thin by design. |

### What NOT to add

- **A plugin registry.** There aren't enough extension points to justify it.
- **Dependency injection.** `Scrape` takes constructor arguments; that's the wiring.
- **A base class hierarchy for fetchers.** Duck typing is enough.
- **Asynchronous paths in the public API** (until v0.4). Sync is the design.
- **Anti-bot evasion.** Per CORE principle 9.

---

## 8. Testing strategy mirrors the layers

Tests are organised the same way as the source. Each test file answers
"does this layer do its job?" not "does the whole system work?"

| Layer | Test file | What it covers |
|-------|-----------|----------------|
| Acquisition | `tests/test_fetch.py` | Retries, `Retry-After`, `robots.txt`, per-host limits, cache |
| Acquisition | `tests/test_jsonapi.py` | Record location, flatten, field paths, "never guess" errors |
| Refinement | `tests/test_clean.py` | Date resolution ladder, all four steps |
| Refinement | `tests/test_normalize.py` | Coercion, quarantine, per-column inference |
| Refinement | `tests/test_parse.py` | Selector extraction, table detection |
| Memory | `tests/test_diff.py` | Keyed and keyless comparison |
| Memory | `tests/test_drift.py` | Statistical comparisons, severities |
| Memory | `tests/test_profile.py` | Statistical fingerprint generation |
| Orchestration | `tests/test_end_to_end_drift.py` | Full pipeline with a local HTTP server |
| Orchestration | `tests/test_end_to_end_json.py` | Full JSON pipeline with a local HTTP server |

### The two rules of the test suite

1. **Every bug fix adds a case to the matching end-to-end test.** If
   the bug was in `diff.py`, the case goes in `test_end_to_end_drift.py`
   or `test_end_to_end_json.py`. Unit tests catch the specific failure;
   the end-to-end test proves it can't come back.

2. **Tests never hit real websites.** `tests/_server.py` is the only
   HTTP server the suite talks to. Everything else is offline.

Coverage is a means, not a goal. The suite prioritizes paths that could
silently corrupt data over lines that are easy to cover.

---

## 9. Architectural limitations

Honest list of what this architecture cannot do, and why.

### 9.1 No concurrent multi-writer support

The SQLite index and the run directory assume one writer at a time.
Two `Scrape` processes writing to the same `.chronicle/` will race.

**Why:** The problem hasn't come up. When it does, the fix is a lock
file plus a status column in the index (planned for v0.3.x).

### 9.2 Storage is not transactional

`baseline.json` is written atomically (`os.replace`). The run directory
and the SQLite index are **not** published together. A crash between
"artifacts written" and "index updated" leaves the two inconsistent.

**Why:** Transactionality requires a two-phase publish (write to temp,
fsync, rename, then insert index row). It's on the v0.3.x roadmap. The
current compromise is: `baseline.json` (the file most likely to be
corrupted by a crash) is atomic; the run directory is best-effort.

### 9.3 Drift detection has blind spots

Currently detected: row-count, schema, null, mean, range, selector
health. Currently **not** detected: same-mean distribution shifts,
categorical drift, dtype drift, duplicate-rate changes.

**Why:** Those checks require storing quantile sketches and category
frequency counts in the profile. That's an addition to `profile.py`
and `drift.py`, not a redesign. Planned for v0.3.x.

### 9.4 No SSRF protection

User-supplied URLs are not validated against private, loopback,
link-local, or cloud-metadata addresses. A malicious URL could target
internal services.

**Why:** The library is currently used locally by trusted users. The
fix is a URL validator before every request and every redirect, and is
planned for v0.3.x. If Chronicle is ever exposed as a service, this
becomes urgent.

### 9.5 No async paths

The public API is synchronous. Jupyter and most data-science code is
synchronous, so this is correct for the audience. But it means the
library cannot saturate a network connection for high-throughput use.

**Why:** Async requires an entirely parallel public API to avoid
breaking existing users. Scheduled for v0.4.0.

### 9.6 No offline replay API

The artifacts (`raw/page_1.html`, `data.parquet`) are sufficient to
replay a run without a network call. But there is no `Scrape.replay
(run_id)` method to make that convenient. Currently possible via
`storage.load_run()` — not user-friendly.

**Why:** Low priority. Planned for v0.3.x.

### 9.7 The CLI is thin

`chronicle scrape` currently works only on table pages — no
`--selectors` flag. `chronicle drift` and `chronicle history` don't
exist.

**Why:** The Python API is the primary interface. The CLI is a
convenience. Improvements are backlogged.

---

## 10. Architectural roadmap

Which layers change, and when. Each row is a capability, not a rewrite.

| Change | Affects | Version | Reason |
|--------|---------|---------|--------|
| Run status + transactional publish | `storage.py`, `core.py` | v0.3.x | Fix §9.2 |
| Content hashes + `chronicle verify` | `storage.py`, new `verify.py` | v0.3.x | Reproducibility |
| Quantile sketches + category counts | `profile.py` | v0.3.x | Fix §9.3 |
| Spread-scaled + categorical drift | `drift.py` | v0.3.x | Fix §9.3 |
| URL validator (SSRF) | `fetch.py`, `playwright_fetcher.py` | v0.3.x | Fix §9.4 |
| Baseline management API | new `baseline.py` | v0.3.x | Reproducibility |
| Pagination strategies | `core.py` | v0.3.x | Path/cursor support |
| Storage backend abstraction | `storage.py` split into `StorageBackend` + `LocalStorage` | v0.4.0 | Cloud/RAD support |
| Async fetchers | new `async_fetch.py`, `async_playwright_fetcher.py` | v0.4.0 | Fix §9.5 |
| RAD module | new sibling repo (`chronicle-service`) | v0.4.0 | Deployment |
| Data contracts | new `contracts.py` | v0.5.0 | Feature |

**None of these break the five-layer model.** Each one is either an
addition to an existing layer or a new small module that sits inside
one layer.

---

## Appendix A — Glossary

| Term | Meaning in Chronicle |
|------|---------------------|
| **Acquisition** | Getting bytes from a URL. The bottom layer above Foundation. |
| **Baseline** | The long-term reference profile drift is compared against. |
| **Cleaning** | Resolving ambiguous values (dates, numbers) into typed values, or flagging them. |
| **Diff** | Row-by-row comparison between two runs. Answers "what changed?" |
| **Drift** | Statistical comparison between two profiles. Answers "is the shape healthy?" |
| **Fetcher** | A class that returns bytes for a URL. |
| **Manifest** | Metadata about a run: config, timestamps, errors, `previous_run_id`. |
| **Profile** | Statistical fingerprint of a DataFrame: per-column dtype, null %, stats. |
| **Refinement** | Turning raw bytes into typed, honest rows. |
| **Run** | One call to `Scrape.run()`. Has a unique `run_id`, a directory, and a manifest. |
| **ScrapeResult** | The object returned by `Scrape.run()`. |
| **Snapshot** | Synonym for "run" in storage contexts. |

---

## Appendix B — Further reading

- **[CORE.md](../CORE.md)** — The ten principles the architecture serves.
- **[COERCION.md](COERCION.md)** — The type coercion contract in detail.
- **[../CHANGELOG.md](../CHANGELOG.md)** — Every release and what changed.
- **[../README.md](../README.md)** — The user-facing introduction.
- **C4 model** — https://c4model.com/ — The layered diagram style used here is influenced by this.
- **RFC 9309** — https://www.rfc-editor.org/rfc/rfc9309 — The `robots.txt` standard Chronicle follows.

---

## The floor plan in one sentence

> **Acquisition gets the bytes; Refinement turns them into honest typed
> data; Memory remembers what was collected and what changed;
> Orchestration wires it together; Interface is where users knock on
> the door.**

---

*Last reviewed: 2026-10-16 · Matches v0.3.0-alpha*