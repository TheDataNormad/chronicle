"""Run-to-run comparison for Chronicle.

Drift detection (``drift.py``) answers "is the *shape* of the data still
healthy?" by comparing statistical profiles to a long-term baseline.

This module answers the question users actually ask: "what changed since
last time?" It compares the current run to the PREVIOUS run, row by row:

    - rows added
    - rows removed
    - cell values changed (needs a ``key``)
    - columns added / removed / dtype changed

Principles (see CORE.md):
    3. Runs automatically whenever a prior run exists, including when the
       new run returns nothing.
    4. Never guess, never drop, never silently convert. If rows cannot be
       matched reliably (no key, duplicate keys, failed pages) the report
       says so in ``warnings`` instead of presenting a confident wrong diff.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# Relative tolerance when comparing floats. Parquet round-trips and float
# parsing can differ in the last bits; that is not a real change.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_TOL = 1e-12

_MISSING = None  # canonical representation of NaN / None / pd.NA / NaT


# ---------------------------------------------------------------- canonical


def _canon(value: Any) -> Any:
    """Return a hashable, comparable form of a cell value.

    Missing values (NaN, None, pd.NA, NaT) all become ``None`` so a value
    that is missing in both runs is not reported as a change.
    Nothing else is converted: strings stay exactly as scraped.
    """
    if isinstance(value, (list, tuple, np.ndarray)):
        return tuple(_canon(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _canon(v)) for k, v in value.items()))
    try:
        if pd.isna(value):
            return _MISSING
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _same(a: Any, b: Any) -> bool:
    """Equality on canonical values with float tolerance."""
    if a is _MISSING or b is _MISSING:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            return math.isclose(
                float(a), float(b), rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL
            )
        return a == b
    try:
        return bool(a == b)
    except Exception:
        return False


def _row_fingerprint(row: dict[str, Any], columns: list[str]) -> tuple:
    """Hashable identity of a whole row, used when there is no key."""
    out = []
    for c in columns:
        v = _canon(row.get(c))
        if isinstance(v, float):
            v = float(f"{v:.12g}")  # stable under tiny float noise
        out.append(v)
    return tuple(out)


# ---------------------------------------------------------------- types


@dataclass
class ChangedCell:
    """One value that differs between the previous and current run."""

    key: tuple
    column: str
    old: Any
    new: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": list(self.key),
            "column": self.column,
            "old": self.old,
            "new": self.new,
        }


@dataclass
class DiffReport:
    """What changed between the previous run and the current run."""

    previous_run_id: str | None = None
    previous_timestamp: str | None = None
    key: list[str] | None = None
    rows_previous: int = 0
    rows_current: int = 0
    rows_unchanged: int = 0
    added: pd.DataFrame = field(default_factory=pd.DataFrame)
    removed: pd.DataFrame = field(default_factory=pd.DataFrame)
    changed: list[ChangedCell] = field(default_factory=list)
    rows_changed: int = 0
    columns_added: list[str] = field(default_factory=list)
    columns_removed: list[str] = field(default_factory=list)
    dtype_changes: dict[str, tuple[str, str]] = field(default_factory=dict)
    ambiguous_rows: int = 0
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

    # ---------- properties ----------

    @property
    def rows_added(self) -> int:
        return len(self.added)

    @property
    def rows_removed(self) -> int:
        return len(self.removed)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.rows_added
            or self.rows_removed
            or self.changed
            or self.columns_added
            or self.columns_removed
            or self.dtype_changes
        )

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "previous_run_id": self.previous_run_id,
            "key": self.key,
            "rows_previous": self.rows_previous,
            "rows_current": self.rows_current,
            "rows_added": self.rows_added,
            "rows_removed": self.rows_removed,
            "rows_changed": self.rows_changed,
            "cells_changed": len(self.changed),
            "rows_unchanged": self.rows_unchanged,
            "columns_added": self.columns_added,
            "columns_removed": self.columns_removed,
            "dtype_changes": {
                k: list(v) for k, v in self.dtype_changes.items()
            },
            "ambiguous_rows": self.ambiguous_rows,
            "complete": self.complete,
            "has_changes": self.has_changes,
            "warnings": self.warnings,
        }

    # ---------- output ----------

    def to_dict(self) -> dict[str, Any]:
        d = self.summary
        d["previous_timestamp"] = self.previous_timestamp
        d["added"] = self.added.to_dict(orient="records")
        d["removed"] = self.removed.to_dict(orient="records")
        d["changed"] = [c.to_dict() for c in self.changed]
        return d

    def to_dataframe(self) -> pd.DataFrame:
        """Long-format table of every change: one row per change."""
        records: list[dict[str, Any]] = []
        key_cols = self.key or []
        for _, r in self.added.iterrows():
            records.append({
                "change": "added",
                "key": _key_label(r, key_cols),
                "column": None, "old": None, "new": None,
            })
        for _, r in self.removed.iterrows():
            records.append({
                "change": "removed",
                "key": _key_label(r, key_cols),
                "column": None, "old": None, "new": None,
            })
        for c in self.changed:
            records.append({
                "change": "changed",
                "key": ", ".join(str(k) for k in c.key),
                "column": c.column, "old": c.old, "new": c.new,
            })
        return pd.DataFrame(
            records, columns=["change", "key", "column", "old", "new"]
        )

    def __repr__(self) -> str:
        return (
            f"<DiffReport +{self.rows_added} -{self.rows_removed} "
            f"~{self.rows_changed} unchanged={self.rows_unchanged}"
            f"{'' if self.complete else ' INCOMPLETE'}>"
        )

    def pretty(self, max_items: int = 10) -> str:
        """Human-readable text report."""
        lines = ["=" * 70]
        title = "CHANGES SINCE LAST RUN"
        if self.previous_run_id:
            title += f"  (vs {self.previous_run_id})"
        lines += [title, "=" * 70]
        lines.append(
            f"  rows: {self.rows_previous} -> {self.rows_current}   "
            f"+{self.rows_added} added   -{self.rows_removed} removed   "
            f"~{self.rows_changed} changed   {self.rows_unchanged} unchanged"
        )
        if self.key:
            lines.append(f"  matched on key: {', '.join(self.key)}")
        else:
            lines.append(
                "  no key given: rows matched on full content, so edited "
                "values appear as one removed + one added row"
            )

        for w in self.warnings:
            lines.append(f"  ⚠️  {w}")

        if self.columns_added:
            lines.append(f"  columns added:   {', '.join(self.columns_added)}")
        if self.columns_removed:
            lines.append(f"  columns removed: {', '.join(self.columns_removed)}")
        for col, (old, new) in self.dtype_changes.items():
            lines.append(f"  dtype changed:   {col}: {old} -> {new}")

        if not self.has_changes:
            lines.append("  No changes.")
            return "\n".join(lines)

        kc = self.key or []
        if self.rows_added:
            lines.append(f"\n  + added ({self.rows_added})")
            for _, r in self.added.head(max_items).iterrows():
                lines.append(f"      {_key_label(r, kc)}")
        if self.rows_removed:
            lines.append(f"\n  - removed ({self.rows_removed})")
            for _, r in self.removed.head(max_items).iterrows():
                lines.append(f"      {_key_label(r, kc)}")
        if self.changed:
            lines.append(f"\n  ~ changed cells ({len(self.changed)})")
            for c in self.changed[:max_items]:
                k = ", ".join(str(x) for x in c.key)
                lines.append(f"      [{k}] {c.column}: {c.old!r} -> {c.new!r}")
        return "\n".join(lines)


def _key_label(row: pd.Series, key_cols: list[str]) -> str:
    """Short label for a row: its key if known, else its first few values."""
    if key_cols and all(c in row.index for c in key_cols):
        return ", ".join(str(row[c]) for c in key_cols)
    vals = [str(v) for v in row.tolist()[:4]]
    return " | ".join(vals)


# ---------------------------------------------------------------- core


def normalize_key(key: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """Accept ``"title"`` or ``["a", "b"]``; return a list or None."""
    if key is None:
        return None
    if isinstance(key, str):
        return [key]
    cols = list(key)
    return cols or None


def compute_diff(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    key: str | list[str] | None = None,
    previous_run_id: str | None = None,
    previous_timestamp: str | None = None,
    complete: bool = True,
    extra_warnings: list[str] | None = None,
) -> DiffReport:
    """Compare ``current`` to ``previous``.

    Args:
        previous: DataFrame from the previous run (may be empty).
        current: DataFrame from this run (may be empty).
        key: Column(s) that uniquely identify a row. With a key Chronicle
            can report *changed values*. Without one it can only report
            added/removed rows. Chronicle never guesses a key.
        complete: False if either run had failed pages. Removed/added rows
            may then be gaps in coverage rather than real changes.
        extra_warnings: Warnings to carry into the report.
    """
    key_cols = normalize_key(key)
    report = DiffReport(
        previous_run_id=previous_run_id,
        previous_timestamp=previous_timestamp,
        rows_previous=len(previous),
        rows_current=len(current),
        complete=complete,
        warnings=list(extra_warnings or []),
    )

    # ---- zero-row cases are reported, never skipped (CORE principle 3)
    if len(current) == 0 and len(previous) > 0:
        report.warnings.append(
            f"This run returned 0 rows; the previous run had {len(previous)}. "
            "All previous rows are listed as removed. Check your selectors "
            "and the page before trusting this as a real deletion."
        )
        report.removed = previous.reset_index(drop=True)
        report.key = key_cols
        return report
    if len(previous) == 0 and len(current) > 0:
        report.added = current.reset_index(drop=True)
        report.key = key_cols
        if complete:
            report.warnings.append(
                "The previous run had 0 rows, so every row is listed as added."
            )
        return report
    if len(previous) == 0 and len(current) == 0:
        report.key = key_cols
        return report

    # ---- schema comparison
    prev_cols, cur_cols = list(previous.columns), list(current.columns)
    report.columns_added = [c for c in cur_cols if c not in prev_cols]
    report.columns_removed = [c for c in prev_cols if c not in cur_cols]
    shared = [c for c in cur_cols if c in prev_cols]
    for c in shared:
        p, q = str(previous[c].dtype), str(current[c].dtype)
        if p != q and not _dtypes_equivalent(p, q):
            report.dtype_changes[c] = (p, q)

    if not shared:
        report.warnings.append(
            "The previous and current runs share no columns; rows cannot "
            "be compared."
        )
        report.added = current.reset_index(drop=True)
        report.removed = previous.reset_index(drop=True)
        return report

    # ---- validate the key
    if key_cols:
        missing = [c for c in key_cols if c not in shared]
        if missing:
            report.warnings.append(
                f"Key column(s) {missing} are not in both runs; falling "
                "back to full-row comparison."
            )
            key_cols = None
    report.key = key_cols

    prev_rows = previous[shared].to_dict(orient="records")
    cur_rows = current[shared].to_dict(orient="records")

    if key_cols:
        _diff_keyed(report, prev_rows, cur_rows, shared, key_cols)
    else:
        _diff_keyless(report, prev_rows, cur_rows, shared)

    if not complete:
        report.warnings.append(
            "One of the two runs had failed pages. Rows listed as added or "
            "removed may be gaps in coverage, not real changes."
        )
    return report


def _dtypes_equivalent(a: str, b: str) -> bool:
    """Treat parquet round-trip aliases as the same dtype."""
    text = {"object", "str", "string", "string[python]", "string[pyarrow]"}
    if a in text and b in text:
        return True
    ints = {"int64", "Int64", "int32", "Int32"}
    return a in ints and b in ints


def _diff_keyed(
    report: DiffReport,
    prev_rows: list[dict[str, Any]],
    cur_rows: list[dict[str, Any]],
    shared: list[str],
    key_cols: list[str],
) -> None:
    def keyed(rows: list[dict[str, Any]]):
        buckets: dict[tuple, list[dict[str, Any]]] = {}
        for r in rows:
            k = tuple(_canon(r.get(c)) for c in key_cols)
            buckets.setdefault(k, []).append(r)
        return buckets

    prev_b, cur_b = keyed(prev_rows), keyed(cur_rows)

    # Duplicate or missing keys cannot be matched reliably. Report them
    # (never silently drop) and compare those rows as whole-row multisets.
    dup_keys = {
        k for k, v in {**prev_b, **cur_b}.items()
        if len(prev_b.get(k, [])) > 1 or len(cur_b.get(k, [])) > 1
        or any(part is _MISSING for part in k)
    }
    if dup_keys:
        n = sum(len(prev_b.get(k, [])) + len(cur_b.get(k, [])) for k in dup_keys)
        report.ambiguous_rows = n
        report.warnings.append(
            f"{len(dup_keys)} key value(s) are duplicated or missing "
            f"({n} rows across both runs), so they cannot be matched "
            f"one-to-one. Those rows are compared as whole rows: an edited "
            f"value shows as one removed + one added row. "
            f"Is {key_cols} really unique?"
        )

    added_rows: list[dict[str, Any]] = []
    removed_rows: list[dict[str, Any]] = []
    value_cols = [c for c in shared if c not in key_cols]

    for k in prev_b.keys() | cur_b.keys():
        p_list, c_list = prev_b.get(k, []), cur_b.get(k, [])
        if k in dup_keys:
            pc = Counter(_row_fingerprint(r, shared) for r in p_list)
            cc = Counter(_row_fingerprint(r, shared) for r in c_list)
            for r in p_list:
                fp = _row_fingerprint(r, shared)
                if pc[fp] > cc.get(fp, 0):
                    removed_rows.append(r)
                    pc[fp] -= 1
            for r in c_list:
                fp = _row_fingerprint(r, shared)
                if cc[fp] > pc.get(fp, 0):
                    added_rows.append(r)
                    cc[fp] -= 1
            report.rows_unchanged += sum(
                min(pc.get(fp, 0), cc.get(fp, 0)) for fp in pc.keys() | cc.keys()
            )
            continue
        if not p_list:
            added_rows.append(c_list[0])
        elif not c_list:
            removed_rows.append(p_list[0])
        else:
            row_changed = False
            for col in value_cols:
                old, new = _canon(p_list[0].get(col)), _canon(c_list[0].get(col))
                if not _same(old, new):
                    row_changed = True
                    report.changed.append(ChangedCell(k, col, old, new))
            if row_changed:
                report.rows_changed += 1
            else:
                report.rows_unchanged += 1

    report.added = pd.DataFrame(added_rows, columns=shared)
    report.removed = pd.DataFrame(removed_rows, columns=shared)


def _diff_keyless(
    report: DiffReport,
    prev_rows: list[dict[str, Any]],
    cur_rows: list[dict[str, Any]],
    shared: list[str],
) -> None:
    pc = Counter(_row_fingerprint(r, shared) for r in prev_rows)
    cc = Counter(_row_fingerprint(r, shared) for r in cur_rows)

    added_rows, removed_rows = [], []
    p_left, c_left = Counter(pc), Counter(cc)
    for r in cur_rows:
        fp = _row_fingerprint(r, shared)
        if p_left.get(fp, 0) > 0:
            p_left[fp] -= 1
        else:
            added_rows.append(r)
    for r in prev_rows:
        fp = _row_fingerprint(r, shared)
        if c_left.get(fp, 0) > 0:
            c_left[fp] -= 1
        else:
            removed_rows.append(r)

    report.rows_unchanged = sum(
        min(pc.get(fp, 0), cc.get(fp, 0)) for fp in pc.keys() | cc.keys()
    )
    report.added = pd.DataFrame(added_rows, columns=shared)
    report.removed = pd.DataFrame(removed_rows, columns=shared)
