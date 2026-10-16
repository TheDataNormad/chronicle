"""Tests for the JSON/API connector (jsonapi.py): record location, flatten,
field paths, and the "never guess" error cases."""

from __future__ import annotations

import pytest

from chronicle.exceptions import ParseError
from chronicle.jsonapi import (
    extract_fields,
    flatten_record,
    locate_records,
    looks_like_json,
    parse_json,
)

WORLD_BANK = [
    {"page": 1, "pages": 1, "per_page": 50, "total": 2},
    [
        {"indicator": {"id": "SL.UEM.1524.ZS", "value": "Youth unemployment"},
         "country": {"id": "NG", "value": "Nigeria"}, "date": "2023", "value": 8.1},
        {"indicator": {"id": "SL.UEM.1524.ZS", "value": "Youth unemployment"},
         "country": {"id": "NG", "value": "Nigeria"}, "date": "2022", "value": None},
    ],
]
SHOPIFY = {"products": [
    {"id": 1, "title": "Mug", "variants": [{"price": "9.99"}], "vendor": "Acme"},
    {"id": 2, "title": "Cup", "variants": [{"price": "7.50"}], "vendor": "Acme"},
]}


def test_looks_like_json():
    assert looks_like_json('  {"a":1}') and looks_like_json("[1]")
    assert not looks_like_json("<html>")


# ------------------------------------------------------------ locate


def test_top_level_list_of_objects():
    assert locate_records([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]


def test_world_bank_shape_is_found_without_a_path():
    assert len(locate_records(WORLD_BANK)) == 2


def test_shopify_shape_is_found_without_a_path():
    assert [p["title"] for p in locate_records(SHOPIFY)] == ["Mug", "Cup"]


def test_nested_one_level_down():
    assert locate_records({"data": {"items": [{"a": 1}]}}) == [{"a": 1}]


def test_explicit_records_path_with_list_index():
    assert len(locate_records(WORLD_BANK, "1")) == 2
    assert locate_records({"a": {"b": [{"x": 1}]}}, "a.b") == [{"x": 1}]


def test_several_candidates_is_an_error_not_a_guess():
    data = {"users": [{"id": 1}], "orders": [{"id": 9}]}
    with pytest.raises(ParseError, match="Several lists.*orders.*users"):
        locate_records(data)
    assert locate_records(data, "orders") == [{"id": 9}]   # path resolves it


def test_no_records_is_an_error():
    with pytest.raises(ParseError, match="records_path"):
        locate_records({"status": "ok", "count": 3})


def test_empty_page_of_known_shape_is_zero_rows_not_an_error():
    assert locate_records({"products": []}) == []
    assert locate_records([]) == []
    assert locate_records([{"page": 3}, []]) == []


def test_bad_records_path():
    with pytest.raises(ParseError, match="not found"):
        locate_records(SHOPIFY, "nope")
    with pytest.raises(ParseError, match="list of objects"):
        locate_records({"a": {"b": 1}}, "a.b")
    with pytest.raises(ParseError, match="not every item"):
        locate_records({"a": [1, {"x": 1}]}, "a")


# ------------------------------------------------------------ shape of rows


def test_flatten_nested_objects_keeps_lists():
    flat = flatten_record({"a": {"b": 1, "c": {"d": 2}}, "tags": ["x", "y"], "e": None})
    assert flat == {"a.b": 1, "a.c.d": 2, "tags": ["x", "y"], "e": None}


def test_extract_fields_by_path_and_missing_is_none():
    rec = {"id": 1, "variants": [{"price": "9.99"}], "meta": {"k": "v"}}
    out = extract_fields(rec, {"id": "id", "price": "variants.0.price",
                               "k": "meta.k", "gone": "meta.nope"})
    assert out == {"id": 1, "price": "9.99", "k": "v", "gone": None}


def test_null_is_a_value_not_a_missing_path():
    out = extract_fields({"a": None}, {"a": "a", "b": "b"})
    assert out == {"a": None, "b": None}


def test_parse_json_world_bank_flat_columns():
    import json

    rows = parse_json(json.dumps(WORLD_BANK))
    assert rows[0]["country.id"] == "NG" and rows[0]["value"] == 8.1
    assert rows[1]["value"] is None


def test_parse_json_errors():
    with pytest.raises(ParseError, match="Empty"):
        parse_json("  ")
    with pytest.raises(ParseError, match="not valid JSON"):
        parse_json("<html>")
