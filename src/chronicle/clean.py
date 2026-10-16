"""Datetime cleaning and the cleaning report for Chronicle.

CORE principle 4: never guess, never drop, never silently convert.

Dates are where guessing is most dangerous. ``03/04/2026`` is 3 April or
4 March. A wrong pick is still a perfectly valid-looking datetime, so no
later check will ever catch it. Chronicle therefore resolves a date only
when the evidence allows exactly one reading, and otherwise flags it.

Resolution order for every value in a date column:

1. DECLARED   ``date_formats={"col": "%d/%m/%Y"}`` is applied first. A
              global ``dayfirst=True/False`` settles numeric day/month order.
2. UNAMBIGUOUS  ISO (2026-04-03), month names (3 Apr 2026), and numeric
              dates where only one reading is a real date (25/04/2026), or
              where both readings are identical (05/05/2026).
3. COLUMN EVIDENCE  If any value in the column (same separator) can only be
              day-first, e.g. 25/04/2026, every value in that style is
              day-first. Contradicting evidence (both orders seen) resolves
              nothing.
4. FLAGGED    Anything still ambiguous or unparseable becomes missing in the
              DataFrame, and the ORIGINAL text is kept in the cleaning
              report with the reason and the candidate readings.

Nothing here ever picks a reading silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

NULL_TOKENS = {"n/a", "na", "null", "none", "-", "--", "nan", "", "\u2014", "\u2013"}

# A column is auto-detected as a date column when at least this share of its
# non-missing values are recognised date strings.
DATE_COLUMN_MIN_FRACTION = 0.8

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_WEEKDAYS = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    "mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
)

_TIME = (
    r"(?P<time>(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.(?P<f>\d{1,6}))?)?"
    r"\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?"
    r"\s*(?P<tz>Z|UTC|GMT|[+-]\d{2}:?\d{2})?)"
)
_SEP_TIME = r"(?:(?:\s+|T|,\s*)" + _TIME + r")?"

_RE_YMD = re.compile(
    r"^(?P<y>\d{4})(?P<sep>[-/.])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})"
    + _SEP_TIME + r"$"
)
_RE_NUM = re.compile(
    r"^(?P<a>\d{1,2})(?P<sep>[-/.])(?P<b>\d{1,2})(?P=sep)(?P<y>\d{4})"
    + _SEP_TIME + r"$"
)
_RE_DMY_NAME = re.compile(
    r"^(?P<d>\d{1,2})(?:st|nd|rd|th)?[\s\-/.]+(?:of\s+)?(?P<mon>[A-Za-z]{3,9})\.?"
    r"[\s\-/.,]+(?P<y>\d{4})" + _SEP_TIME + r"$"
)
_RE_MDY_NAME = re.compile(
    r"^(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?"
    r"[\s,]+(?P<y>\d{4})" + _SEP_TIME + r"$"
)
_RE_WEEKDAY = re.compile(r"^(?:" + _WEEKDAYS + r")\.?,?\s+", re.IGNORECASE)


# ---------------------------------------------------------------- report


@dataclass
class CleaningIssue:
    """One value Chronicle did not convert cleanly. The original is kept."""

    row_index: int
    column: str
    original: Any
    kind: str
    reason: str
    candidates: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "column": self.column,
            "original": self.original,
            "kind": self.kind,
            "reason": self.reason,
            "candidates": self.candidates,
        }


@dataclass
class ColumnCleaning:
    """What happened to one column during cleaning."""

    name: str
    kind: str = "datetime"
    parsed: int = 0
    missing_in_source: int = 0
    flagged: int = 0
    resolution: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "parsed": self.parsed,
            "missing_in_source": self.missing_in_source,
            "flagged": self.flagged,
            "resolution": self.resolution,
            "notes": self.notes,
        }


@dataclass
class CleaningReport:
    """Everything cleaning converted, flagged, or could not convert."""

    rows_total: int = 0
    rows_quarantined: int = 0
    columns: dict[str, ColumnCleaning] = field(default_factory=dict)
    issues: list[CleaningIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    @property
    def ambiguous_dates(self) -> int:
        return sum(1 for i in self.issues if i.kind == "ambiguous_date")

    def issues_of(self, kind: str) -> list[CleaningIssue]:
        return [i for i in self.issues if i.kind == kind]

    @property
    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for i in self.issues:
            counts[i.kind] = counts.get(i.kind, 0) + 1
        return {
            "rows_total": self.rows_total,
            "rows_quarantined": self.rows_quarantined,
            "date_columns": [
                n for n, c in self.columns.items() if c.kind == "datetime"
            ],
            "issues": len(self.issues),
            "issues_by_kind": counts,
            "warnings": self.warnings,
        }

    def to_dict(self) -> dict[str, Any]:
        d = self.summary
        d["columns"] = {n: c.to_dict() for n, c in self.columns.items()}
        d["issue_details"] = [i.to_dict() for i in self.issues]
        return d

    def to_dataframe(self) -> pd.DataFrame:
        """One row per flagged value, original text preserved."""
        cols = ["row_index", "column", "original", "kind", "reason", "candidates"]
        if not self.issues:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([i.to_dict() for i in self.issues], columns=cols)

    def __repr__(self) -> str:
        return (
            f"<CleaningReport rows={self.rows_total} "
            f"quarantined={self.rows_quarantined} issues={len(self.issues)}>"
        )

    def pretty(self, max_items: int = 10) -> str:
        lines = ["=" * 70, "CLEANING REPORT", "=" * 70]
        lines.append(
            f"  rows: {self.rows_total}   quarantined: {self.rows_quarantined}"
            f"   flagged values: {len(self.issues)}"
        )
        for w in self.warnings:
            lines.append(f"  ⚠️  {w}")
        for name, c in self.columns.items():
            lines.append(
                f"  {name} [{c.kind}]: {c.parsed} parsed, "
                f"{c.missing_in_source} missing in source, {c.flagged} flagged"
            )
            if c.resolution:
                lines.append(f"      order: {c.resolution}")
            for n in c.notes:
                lines.append(f"      note: {n}")
        if self.issues:
            lines.append("")
            lines.append("  Not converted (original text kept here):")
            for i in self.issues[:max_items]:
                cand = f"  candidates: {i.candidates}" if i.candidates else ""
                lines.append(
                    f"    row {i.row_index} {i.column}: {i.original!r} "
                    f"[{i.kind}] {i.reason}{cand}"
                )
            if len(self.issues) > max_items:
                lines.append(f"    ... and {len(self.issues) - max_items} more")
        else:
            lines.append("  Nothing flagged.")
        return "\n".join(lines)


# ---------------------------------------------------------------- parsing


@dataclass
class _Parsed:
    """Result of recognising one string. Exactly one of the shapes is set."""

    kind: str  # "exact" | "numeric" | "invalid"
    value: datetime | None = None
    has_time: bool = False
    # numeric (day/month order undecided)
    a: int = 0
    b: int = 0
    year: int = 0
    sep: str = ""
    time_kw: dict[str, Any] | None = None
    reason: str = ""


def _time_parts(m: re.Match) -> tuple[dict[str, int], bool, str | None]:
    """Extract time-of-day kwargs from a regex match. Returns (kw, has_time, err)."""
    if not m.groupdict().get("time"):
        return {}, False, None
    h, mi = int(m.group("h")), int(m.group("mi"))
    s = int(m.group("s") or 0)
    f = m.group("f")
    micro = int((f + "000000")[:6]) if f else 0
    ampm = (m.group("ampm") or "").replace(".", "").lower()
    if ampm:
        if not 1 <= h <= 12:
            return {}, True, "12-hour time needs an hour from 1 to 12"
        h = h % 12 + (12 if ampm == "pm" else 0)
    if h > 23 or mi > 59 or s > 59:
        return {}, True, "time of day out of range"
    return {"hour": h, "minute": mi, "second": s, "microsecond": micro}, True, None


def _tz_of(m: re.Match) -> timezone | None:
    tz = m.groupdict().get("tz")
    if not tz:
        return None
    if tz.upper() in {"Z", "UTC", "GMT"}:
        return timezone.utc
    sign = 1 if tz[0] == "+" else -1
    digits = tz[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))


def _build(y: int, mo: int, d: int, m: re.Match) -> _Parsed:
    kw, has_time, err = _time_parts(m)
    if err:
        return _Parsed("invalid", reason=err)
    try:
        dt = datetime(y, mo, d, **kw, tzinfo=_tz_of(m))
    except ValueError as e:
        return _Parsed("invalid", reason=f"not a real calendar date ({e})")
    return _Parsed("exact", value=dt, has_time=has_time)


def recognise(text: str) -> _Parsed | None:
    """Recognise a date string. None means "does not look like a date"."""
    t = text.strip()
    t = _RE_WEEKDAY.sub("", t)
    if m := _RE_YMD.match(t):
        return _build(int(m["y"]), int(m["m"]), int(m["d"]), m)
    if m := _RE_DMY_NAME.match(t):
        mon = _MONTHS.get(m["mon"].lower())
        if mon is None:
            return None
        return _build(int(m["y"]), mon, int(m["d"]), m)
    if m := _RE_MDY_NAME.match(t):
        mon = _MONTHS.get(m["mon"].lower())
        if mon is None:
            return None
        return _build(int(m["y"]), mon, int(m["d"]), m)
    if m := _RE_NUM.match(t):
        a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
        kw, has_time, err = _time_parts(m)
        if err:
            return _Parsed("invalid", reason=err)
        p = _Parsed("numeric", a=a, b=b, year=y, sep=m["sep"], has_time=has_time)
        p.time_kw = {"m": m, "kw": kw}
        return p
    return None


def _resolve_numeric(p: _Parsed, dayfirst: bool) -> _Parsed:
    day, month = (p.a, p.b) if dayfirst else (p.b, p.a)
    kw = p.time_kw["kw"]
    try:
        dt = datetime(p.year, month, day, **kw, tzinfo=_tz_of(p.time_kw["m"]))
    except ValueError as e:
        return _Parsed("invalid", reason=f"not a real calendar date ({e})")
    return _Parsed("exact", value=dt, has_time=p.has_time)


def _numeric_readings(p: _Parsed) -> tuple[_Parsed, _Parsed]:
    return _resolve_numeric(p, True), _resolve_numeric(p, False)


def _label(p: _Parsed) -> str:
    return p.value.isoformat() if p.value else p.reason


def _is_missing_token(v: Any) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(v, str) and v.strip().lower() in NULL_TOKENS


# ---------------------------------------------------------------- columns


def looks_like_date_column(values: list[Any]) -> bool:
    """True if enough of the non-missing text values are recognised dates."""
    texts = [str(v) for v in values if not _is_missing_token(v)]
    if not texts:
        return False
    hits = sum(1 for t in texts if recognise(t) is not None)
    return hits / len(texts) >= DATE_COLUMN_MIN_FRACTION


def clean_date_column(
    rows: list[dict[str, Any]],
    column: str,
    report: CleaningReport,
    dayfirst: bool | None = None,
    date_format: str | None = None,
    as_date: bool = False,
) -> None:
    """Resolve ``column`` in place: values become datetimes or None.

    Anything that cannot be resolved with certainty becomes None and is
    recorded in ``report`` with its original text.
    """
    info = ColumnCleaning(name=column, kind="datetime")
    report.columns[column] = info

    # ---- first pass: recognise everything and gather column evidence
    recognised: dict[int, _Parsed | None] = {}
    day_first_evidence = month_first_evidence = 0
    evidence: dict[str, list[int]] = {}  # sep -> [dayfirst votes, monthfirst votes]
    for i, row in enumerate(rows):
        v = row.get(column)
        if _is_missing_token(v):
            continue
        if isinstance(v, (datetime, date)):
            recognised[i] = None
            continue
        p = recognise(str(v))
        recognised[i] = p
        if p is not None and p.kind == "numeric":
            votes = evidence.setdefault(p.sep, [0, 0])
            if p.a > 12 and p.b <= 12:
                votes[0] += 1
            elif p.b > 12 and p.a <= 12:
                votes[1] += 1

    order_by_sep: dict[str, bool | None] = {}
    for sep, (df_votes, mf_votes) in evidence.items():
        if df_votes and mf_votes:
            order_by_sep[sep] = None
            info.notes.append(
                f"style '{sep}' contains both day-first and month-first "
                "values; day/month order cannot be inferred for it"
            )
        elif df_votes:
            order_by_sep[sep] = True
        elif mf_votes:
            order_by_sep[sep] = False
        day_first_evidence += df_votes
        month_first_evidence += mf_votes

    if date_format:
        info.resolution = f"declared format {date_format}"
    elif dayfirst is not None:
        info.resolution = "declared " + ("day-first" if dayfirst else "month-first")
    elif day_first_evidence and not month_first_evidence:
        info.resolution = (
            f"day-first, inferred from {day_first_evidence} value(s) with day > 12"
        )
    elif month_first_evidence and not day_first_evidence:
        info.resolution = (
            f"month-first, inferred from {month_first_evidence} value(s) "
            "with day > 12"
        )

    # ---- second pass: convert or flag
    results: dict[int, datetime | None] = {}
    tz_flavours: set[bool] = set()

    def flag(i: int, original: Any, kind: str, reason: str,
             candidates: list[str] | None = None) -> None:
        report.issues.append(
            CleaningIssue(i, column, original, kind, reason, candidates)
        )
        info.flagged += 1
        results[i] = None

    for i, row in enumerate(rows):
        v = row.get(column)
        if _is_missing_token(v):
            info.missing_in_source += 1
            results[i] = None
            continue
        if isinstance(v, (datetime, date)):
            dt = (
                v if isinstance(v, datetime)
                else datetime(v.year, v.month, v.day)
            )
            results[i] = dt
            info.parsed += 1
            tz_flavours.add(dt.tzinfo is not None)
            continue

        text = str(v)

        # 1. declared format
        if date_format:
            try:
                results[i] = datetime.strptime(
                    text.strip(), date_format
                )
                info.parsed += 1
                tz_flavours.add(results[i].tzinfo is not None)
                continue
            except ValueError:
                pass  # try unambiguous forms below, else flag

        p = recognised.get(i)
        if p is None:
            reason = (
                f"does not match declared format {date_format}"
                if date_format else "not a recognised date format"
            )
            flag(i, v, "format_mismatch" if date_format else "unparseable_date", reason)
            continue
        if p.kind == "invalid":
            flag(i, v, "invalid_date", p.reason)
            continue

        if p.kind == "exact":
            results[i] = p.value
            info.parsed += 1
            tz_flavours.add(p.value.tzinfo is not None)
            continue

        # numeric: day/month order must be decided
        df_reading, mf_reading = _numeric_readings(p)
        df_ok, mf_ok = df_reading.kind == "exact", mf_reading.kind == "exact"

        if not df_ok and not mf_ok:
            flag(i, v, "invalid_date", "not a real date in either day/month order")
            continue
        if df_ok and mf_ok and df_reading.value == mf_reading.value:
            results[i] = df_reading.value  # e.g. 05/05/2026: same either way
            info.parsed += 1
            tz_flavours.add(df_reading.value.tzinfo is not None)
            continue
        if df_ok != mf_ok:
            only = df_reading if df_ok else mf_reading
            only_dayfirst = df_ok
            if dayfirst is not None and dayfirst != only_dayfirst:
                info.notes.append(
                    f"row {i}: {text!r} can only be "
                    f"{'day' if only_dayfirst else 'month'}-first, which "
                    f"contradicts the declared order; read as "
                    f"{'day' if only_dayfirst else 'month'}-first"
                )
                report.issues.append(CleaningIssue(
                    i, column, v, "date_order_conflict",
                    "only one reading is a real date and it contradicts the "
                    "declared day/month order; converted using the only valid "
                    "reading", [only.value.isoformat()],
                ))
            results[i] = only.value
            info.parsed += 1
            tz_flavours.add(only.value.tzinfo is not None)
            continue

        # truly ambiguous: both readings are real, different dates
        if dayfirst is not None:
            chosen = df_reading if dayfirst else mf_reading
        elif order_by_sep.get(p.sep) is not None:
            chosen = df_reading if order_by_sep[p.sep] else mf_reading
        else:
            flag(
                i, v, "ambiguous_date",
                "day and month could be either way round; set dayfirst=True/"
                "False or date_formats={...} to resolve",
                [df_reading.value.isoformat(), mf_reading.value.isoformat()],
            )
            continue
        results[i] = chosen.value
        info.parsed += 1
        tz_flavours.add(chosen.value.tzinfo is not None)

    # ---- timezone consistency
    if tz_flavours == {True, False}:
        for i, row in enumerate(rows):
            if results.get(i) is not None:
                results[i] = None
                report.issues.append(CleaningIssue(
                    i, column, row.get(column), "mixed_timezone",
                    "column mixes timezone-aware and naive values; not converted",
                ))
        info.flagged += info.parsed
        info.parsed = 0
        info.notes.append("mixed timezone-aware and naive values; nothing converted")
    elif tz_flavours == {True}:
        info.notes.append("timezone-aware values normalised to UTC")
        for i, dt in list(results.items()):
            if dt is not None:
                results[i] = dt.astimezone(timezone.utc)

    # ---- write back
    for i, row in enumerate(rows):
        dt = results.get(i)
        if dt is not None and as_date and dt.time() == datetime.min.time() \
                and dt.tzinfo is None:
            row[column] = dt.date()
        else:
            row[column] = dt
