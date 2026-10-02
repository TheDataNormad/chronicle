"""Tests for chronicle.normalize."""

import pytest
from pydantic import BaseModel

from chronicle.normalize import (
    coerce_to_bool,
    coerce_to_float,
    coerce_to_int,
    normalize_rows,
)


class Product(BaseModel):
    name: str
    price: float
    rating: float | None = None


# ---- coercion ----

def test_coerce_float_handles_currency():
    assert coerce_to_float("$1,234.56") == pytest.approx(1234.56)
    assert coerce_to_float("45%") == pytest.approx(45.0)
    assert coerce_to_float("12") == pytest.approx(12.0)


def test_coerce_float_handles_nulls():
    assert coerce_to_float("N/A") is None
    assert coerce_to_float("") is None
    assert coerce_to_float(None) is None
    assert coerce_to_float("null") is None


def test_coerce_int():
    assert coerce_to_int("42") == 42
    assert coerce_to_int("$42.99") == 42
    assert coerce_to_int("not-a-number") is None


def test_coerce_bool():
    assert coerce_to_bool("yes") is True
    assert coerce_to_bool("false") is False
    assert coerce_to_bool("1") is True
    assert coerce_to_bool("maybe") is None


# ---- normalize_rows ----

def test_normalize_with_schema_splits_valid_and_invalid():
    rows = [
        {"name": "Widget", "price": "$1,234.56", "rating": "4.5"},
        {"name": "Gadget", "price": "$99", "rating": "N/A"},
        {"name": "Gizmo", "price": "free", "rating": "5.0"},  # bad price
    ]
    result = normalize_rows(rows, schema=Product)
    assert len(result.valid_rows) == 2
    assert len(result.invalid_rows) == 1
    assert result.valid_rows[0]["price"] == pytest.approx(1234.56)
    assert result.valid_rows[1]["rating"] is None


def test_normalize_quality_report_shape():
    rows = [
        {"name": "A", "price": "$10", "rating": "4.0"},
        {"name": "B", "price": "$20", "rating": "4.5"},
    ]
    result = normalize_rows(rows, schema=Product)
    report = result.quality_report()
    assert report["rows_total"] == 2
    assert report["rows_valid"] == 2
    assert report["success_rate"] == pytest.approx(1.0)
    assert "price" in report["columns"]
    assert report["columns"]["price"]["mean"] == pytest.approx(15.0)


def test_normalize_no_schema_infers_types():
    rows = [{"a": "42", "b": "3.14", "c": "hello"}]
    result = normalize_rows(rows)
    assert result.valid_rows[0]["a"] == 42
    assert result.valid_rows[0]["b"] == pytest.approx(3.14)
    assert result.valid_rows[0]["c"] == "hello"