"""Unit tests for run-to-run comparison (diff.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronicle.diff import compute_diff


def _df(rows):
    return pd.DataFrame(rows)


PREV = _df([
    {"name": "Widget", "price": 10.0},
    {"name": "Gadget", "price": 20.0},
    {"name": "Gizmo", "price": 30.0},
])


def test_identical_runs_have_no_changes():
    d = compute_diff(PREV, PREV.copy(), key="name")
    assert not d.has_changes
    assert d.rows_unchanged == 3
    assert d.rows_added == d.rows_removed == d.rows_changed == 0


def test_keyed_detects_added_removed_changed():
    cur = _df([
        {"name": "Widget", "price": 12.0},   # changed
        {"name": "Gadget", "price": 20.0},   # same
        {"name": "Doohickey", "price": 5.0}, # added (Gizmo removed)
    ])
    d = compute_diff(PREV, cur, key="name")
    assert d.rows_added == 1 and d.added.iloc[0]["name"] == "Doohickey"
    assert d.rows_removed == 1 and d.removed.iloc[0]["name"] == "Gizmo"
    assert d.rows_changed == 1
    cell = d.changed[0]
    assert (cell.key, cell.column, cell.old, cell.new) == (
        ("Widget",), "price", 10.0, 12.0
    )
    assert d.rows_unchanged == 1
    assert d.has_changes


def test_keyless_reports_edit_as_removed_plus_added():
    cur = PREV.copy()
    cur.loc[0, "price"] = 11.0
    d = compute_diff(PREV, cur)
    assert d.rows_added == 1 and d.rows_removed == 1
    assert d.rows_changed == 0
    assert d.rows_unchanged == 2
    assert "no key given" in d.pretty()


def test_float_noise_is_not_a_change():
    cur = PREV.copy()
    cur.loc[0, "price"] = 10.0 + 1e-13
    assert not compute_diff(PREV, cur, key="name").has_changes


def test_missing_in_both_runs_is_not_a_change():
    a = _df([{"id": 1, "v": np.nan}, {"id": 2, "v": "x"}])
    b = _df([{"id": 1, "v": None}, {"id": 2, "v": "x"}])
    assert not compute_diff(a, b, key="id").has_changes


def test_value_becoming_missing_is_a_change():
    a = _df([{"id": 1, "v": 5.0}])
    b = _df([{"id": 1, "v": np.nan}])
    d = compute_diff(a, b, key="id")
    assert d.rows_changed == 1
    assert d.changed[0].old == 5.0 and d.changed[0].new is None


def test_strings_are_not_silently_normalised():
    a = _df([{"id": 1, "v": "Widget"}])
    b = _df([{"id": 1, "v": "widget "}])
    assert compute_diff(a, b, key="id").rows_changed == 1


def test_duplicate_keys_are_flagged_not_dropped():
    a = _df([{"id": 1, "v": "a"}, {"id": 1, "v": "b"}])
    b = _df([{"id": 1, "v": "a"}, {"id": 1, "v": "c"}])
    d = compute_diff(a, b, key="id")
    assert d.ambiguous_rows == 4
    assert any("duplicated" in w for w in d.warnings)
    assert d.rows_added == 1 and d.rows_removed == 1
    assert d.rows_unchanged == 1


def test_zero_rows_now_lists_everything_removed_with_warning():
    d = compute_diff(PREV, pd.DataFrame(), key="name")
    assert d.rows_removed == 3
    assert d.has_changes
    assert any("0 rows" in w for w in d.warnings)


def test_previous_empty_lists_everything_added():
    d = compute_diff(pd.DataFrame(), PREV, key="name")
    assert d.rows_added == 3


def test_schema_changes_reported():
    cur = PREV.assign(stock=1).drop(columns=[])
    d = compute_diff(PREV, cur, key="name")
    assert d.columns_added == ["stock"]
    cur2 = PREV.drop(columns=["price"])
    assert compute_diff(PREV, cur2, key="name").columns_removed == ["price"]


def test_dtype_change_reported():
    cur = PREV.copy()
    cur["price"] = cur["price"].astype(str)
    d = compute_diff(PREV, cur, key="name")
    assert "price" in d.dtype_changes


def test_missing_key_column_falls_back_with_warning():
    d = compute_diff(PREV, PREV.copy(), key="sku")
    assert d.key is None
    assert any("not in both runs" in w for w in d.warnings)


def test_incomplete_run_is_flagged():
    d = compute_diff(PREV, PREV.iloc[:2], key="name", complete=False)
    assert d.rows_removed == 1
    assert not d.complete
    assert any("failed pages" in w for w in d.warnings)


def test_composite_key():
    a = _df([{"a": 1, "b": "x", "v": 1}, {"a": 1, "b": "y", "v": 2}])
    b = _df([{"a": 1, "b": "x", "v": 1}, {"a": 1, "b": "y", "v": 3}])
    d = compute_diff(a, b, key=["a", "b"])
    assert d.rows_changed == 1 and d.changed[0].key == (1, "y")


def test_to_dataframe_and_dict_are_serialisable():
    cur = _df([{"name": "Widget", "price": 12.0}])
    d = compute_diff(PREV, cur, key="name")
    tbl = d.to_dataframe()
    assert set(tbl["change"]) == {"changed", "removed"}
    import json
    json.dumps(d.to_dict(), default=str)
