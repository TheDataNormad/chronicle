# Chronicle — Type Coercion Contract

This document defines how Chronicle converts raw scraped values into
typed, analysis-ready values. It is the authoritative specification for
`src/chronicle/normalize.py` and `src/chronicle/clean.py`.

Every behavior below is implemented and tested. If you find a
discrepancy between this document and the code, that is a bug — please
[open an issue](https://github.com/TheDataNormad/chronicle/issues).

For the design rationale, see [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.1.
For the underlying principles, see [`../CORE.md`](../CORE.md).

---

## The one rule

> **The schema declares the intent. The library executes. The library
> never guesses.**

If a value can be interpreted correctly under the declared schema, it
is coerced. If it cannot, the row is **quarantined** with a machine-
readable reason, and the original text is preserved. Silent conversion
is a bug, not a feature.

Two consequences:

1. **Ambiguous values become missing, not wrong.** A value that could
   be `April 3rd` or `March 4th` becomes `NaT`. It does not become
   whichever one the parser guessed first.
2. **Declared types are honored strictly.** If the schema says `int`
   and the value is `"42.99"`, the row is quarantined. The library does
   not truncate to `42`.

---

## The behavior table

This is the reference. Every row is a tested behavior.

### Dates

| Input | Declared | Result | Notes |
|-------|----------|--------|-------|
| `"2026-04-03"` | `date` or `datetime` | `date(2026, 4, 3)` / `datetime(2026, 4, 3)` | ISO format is unambiguous |
| `"2026-04-03"` | `str` | `"2026-04-03"` | Preserved as text |
| `"2026-04-03"` | `float` | **Quarantine** | Not a number |
| `"3 Apr 2026"` | `date` | `date(2026, 4, 3)` | English month names only |
| `"April 3, 2026"` | `date` | `date(2026, 4, 3)` | US-style month-first |
| `"25/04/2026"` | `date` | `date(2026, 4, 25)` | Only one reading is a real date |
| `"05/05/2026"` | `date` | `date(2026, 5, 5)` | Both readings identical |
| `"03/04/2026"` | `date`, no other context | **Flagged** | Ambiguous; becomes `NaT` |
| `"03/04/2026"` | `date`, `dayfirst=True` | `date(2026, 4, 3)` | Declared |
| `"03/04/2026"` | `date`, `date_formats={"d": "%d/%m/%Y"}` | `date(2026, 4, 3)` | Declared |
| `"31/02/2026"` | `date` | **Flagged** | Not a real calendar date |
| `"next tuesday"` | `date` | **Flagged** | Not a recognized format |

### Numbers

| Input | Declared | Result | Notes |
|-------|----------|--------|-------|
| `"42"` | `int` | `42` | Direct integer |
| `"42"` | `float` | `42.0` | Widening is safe |
| `"42.99"` | `float` | `42.99` | Direct float |
| `"42.99"` | `int` | **Quarantine** | Schema mismatch; recorded as `lossy_int` |
| `"$42.99"` | `float` | `42.99` | Currency symbol stripped |
| `"€1.299,50"` | `float` | **Quarantine** | Ambiguous comma; no locale inference |
| `"1,234.56"` | `float` | `1234.56` | US thousands separator |
| `"12,5"` | `float` | **Quarantine** | Ambiguous; recorded as `ambiguous_number` |
| `"1.2B"` | `float` | **Quarantine** | Suffix not supported |
| `"12-15"` | `float` | **Quarantine** | Range, not a single number; recorded as `partial_parse` |
| `"12-15"` | `str` | `"12-15"` | Preserved as text |
| `"00123"` | `int` | **Quarantine** | Leading zeros would be lost |
| `"00123"` | `str` | `"00123"` | Preserved |
| `"N/A"`, `"—"`, `""` | anything | `None` | Recognized missing markers |

### Booleans

The behavior depends on whether the schema declares a boolean.

| Input | Declared `bool` | No schema | Notes |
|-------|-----------------|-----------|-------|
| `"true"`, `"True"`, `"yes"`, `"Yes"` | `True` | `True` | Unambiguous |
| `"false"`, `"False"`, `"no"`, `"No"` | `False` | `False` | Unambiguous |
| `"1"` | `True` | `1` (integer) | Schema decides |
| `"0"` | `False` | `0` (integer) | Schema decides |
| `"y"`, `"n"`, `"t"`, `"f"` | `True`/`False` | `"y"`/`"n"`/`"t"`/`"f"` | Schema decides |
| `"F"` (a grade) | `False` | `"F"` (string) | Without a schema, `"F"` stays `"F"` |

**This is the F-01 fix.** Before v0.3.0-alpha, `"1"` and `"0"` were
always treated as booleans, which broke any numeric column containing
those values. Now the schema decides.

### Missing markers

The following values are recognized as missing in every context:

```
"n/a", "na", "null", "none", "-", "--", "nan", "", "—", "–"
```

Plus: Python `None`, pandas `NaN`/`NaT`, and empty strings.

A value that becomes missing **during** coercion (because it was
ambiguous) is recorded in the cleaning report as `became_missing`, not
silently dropped. That distinction is what makes the report useful.

---

## The four-step date ladder

Dates are where guessing is most dangerous. `03/04/2026` is either
April 3rd or March 4th, and a wrong pick is a perfectly valid-looking
date that no downstream check will ever catch.

Chronicle resolves a date column in this exact order:

### Step 1 — Declared

The user's declaration wins, always:

```python
Scrape(url, date_formats={"published": "%d/%m/%Y"})   # per-column format
Scrape(url, dayfirst=True)                            # global day/month order
```

If the declaration parses the value, the value is accepted. If it
fails, fall through to step 2.

### Step 2 — Unambiguous

The value has exactly one reading:

- ISO format: `2026-04-03`
- Month name: `3 Apr 2026`, `April 3, 2026`
- Day > 12 for the first number: `25/04/2026` (there is no month 25)
- Both readings identical: `05/05/2026`

Convert.

### Step 3 — Column evidence

If any **other** value in the same column (same separator style) can
only be day-first, then every value in that style is day-first:

```
Column: ["03/04/2026", "25/04/2026"]
           ^^^^^^^^^^^^  ^^^^^^^^^^^^
           ambiguous     day-first evidence
→ whole column resolved day-first: ["2026-04-03", "2026-04-25"]
```

If evidence contradicts (some values can only be day-first, some can
only be month-first), **nothing is resolved.** The column is not
guessed at.

### Step 4 — Flagged

If steps 1–3 don't settle it, the value becomes `NaT`. The original
text is preserved in `result.cleaning` with both candidate readings:

```python
issue = result.cleaning.issues[0]
# CleaningIssue(
#   row_index=0,
#   column="published",
#   original="03/04/2026",
#   kind="ambiguous_date",
#   reason="day and month could be either way round; set dayfirst=True/False...",
#   candidates=["2026-04-03T00:00:00", "2026-03-04T00:00:00"],
# )
```

**A date is never silently picked.** If you see a datetime in your
DataFrame, the ladder resolved it with evidence.

---

## Reason codes

Every flagged value gets a `kind` code. These are stable; you can
filter on them.

### Date cleaning (`clean.py`)

| Kind | Meaning |
|------|---------|
| `ambiguous_date` | Day and month could be either way round |
| `invalid_date` | Not a real calendar date (e.g. Feb 31) |
| `unparseable_date` | Does not match any recognized date format |
| `format_mismatch` | Does not match a declared `date_formats` entry |
| `date_order_conflict` | Only one reading is valid, but it contradicts the declared order |
| `mixed_timezone` | Column mixes timezone-aware and naive values |

### Numeric coercion (`normalize.py`)

| Kind | Meaning |
|------|---------|
| `became_missing` | Value could not be parsed as the declared type |
| `ambiguous_number` | Comma could be decimal or thousands separator |
| `partial_parse` | Number found in surrounding text (e.g. `"12-15"`) |
| `lossy_int` | Fractional value in an `int` field |

### Reports

The full set of issues for a run is at `result.cleaning.issues`. Its
counts by kind are in `result.cleaning.summary["issues_by_kind"]`.

---

## The API

### Declaring types via pydantic

```python
from datetime import date
from pydantic import BaseModel

class Product(BaseModel):
    name: str
    price: float
    released: date | None = None
    in_stock: bool | None = None
```

Each field's declared type drives the coercion for that column.

### Declaring date formats

```python
Scrape(
    url="https://example.com/events",
    schema=Event,
    date_formats={"starts_at": "%d/%m/%Y"},   # per-column override
    dayfirst=True,                             # global fallback
)
```

`date_formats` wins over `dayfirst`. Both win over automatic detection.

### Reading the cleaning report

```python
result = Scrape(url, schema=Product).run()

# Human-readable
print(result.cleaning.pretty())

# Tabular: one row per flagged value
df = result.cleaning.to_dataframe()
# columns: row_index, column, original, kind, reason, candidates

# Machine-readable
result.cleaning.summary
# {
#   "rows_total": 100,
#   "rows_quarantined": 3,
#   "date_columns": ["released"],
#   "issues": 8,
#   "issues_by_kind": {"ambiguous_date": 5, "lossy_int": 3},
#   "warnings": [],
# }
```

### JSON sources

When scraping a JSON API, values already have real types. Pass
`typed_source=True` (this is set automatically by `Scrape` when the
source kind is JSON) to skip type inference:

```python
# A JSON string "00123" stays "00123" — not converted to 123.
# A JSON number 5 stays 5 — not re-parsed from text.
```

---

## What stays as a string

Chronicle does **not** auto-convert:

- Anything declared as `str` in the schema.
- Anything with leading zeros (`"00123"`, `"0803..."`) unless declared
  `str` (in which case it's preserved anyway).
- Anything with mixed letters and numbers (`"1-2 days"`, `"A123"`).
- Anything requiring context to interpret (`"1.2B"` — bytes? billions?
  a product code?).
- Emails, URLs, phone numbers — they are `str`.

The rule: **if the interpretation requires context the library doesn't
have, the value stays as it was scraped.**

---

## What Chronicle does NOT do

| Not supported | Why |
|---------------|-----|
| Locale inference (`de_DE` vs `en_US` for `"1.299,50"`) | Ambiguous without declaration; users get a `ambiguous_number` flag instead |
| Magnitude suffixes (`1.2B` → `1_200_000_000`) | Bytes vs billions vs product codes; requires context |
| Non-English month names | English only in v0.3.0-alpha |
| Two-digit years (`"03/04/26"`) | Requires a declared format (`date_formats={"col": "%d/%m/%y"}`) |
| Auto-detect timezone from context | Timezone-naive is preserved as naive |
| Convert naive to aware (or vice versa) | A column with mixed tz flavors is flagged, not coerced |
| Truncate or round numbers | Schema mismatch (`lossy_int`) is a quarantine, not a fix |

Each of these might change in a future version. They are documented
here so that a user encountering one knows it is a **known design
choice**, not a bug.

---

## Versioning

This contract is versioned with the library. Changes that affect
coercion behavior require:

1. An update to this document
2. A `CHANGELOG.md` entry
3. A test demonstrating both the old behavior (rejected) and the new
   behavior (accepted)

Breaking changes to coercion are treated as **minor** version bumps
(e.g. v0.3 → v0.4), not patch bumps. Users depending on a specific
coercion outcome should pin the minor version.

---

## Appendix — Where to look in the code

| Behavior | Implementation |
|----------|---------------|
| Date recognition | `clean.py` → `recognise()` |
| The four-step ladder | `clean.py` → `clean_date_column()` |
| Boolean token sets | `normalize.py` → `_TRUE_VALUES`, `_INFER_TRUE` |
| Ambiguous comma detection | `normalize.py` → `_comma_is_ambiguous()` |
| Numeric risk flags | `normalize.py` → `_numeric_flag()` |
| Recording coercions | `normalize.py` → `_record_coercion()` |
| Per-column typing | `normalize.py` → `_resolve_columns()` |
| The cleaning report shape | `clean.py` → `CleaningReport`, `CleaningIssue` |

---

*Last reviewed: 2026-10-16 · Matches v0.3.0-alpha*