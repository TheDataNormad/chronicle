"""Tests for datetime cleaning and the cleaning report (clean.py).

The rule under test (CORE principle 4): a date is converted only when the
evidence allows exactly one reading. Otherwise it is flagged, with the
original text kept in the report.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from chronicle import Scrape
from chronicle.clean import CleaningReport, clean_date_column, looks_like_date_column
from chronicle.normalize import normalize_rows
from tests.test_end_to_end_drift import http_server


def _clean(values, **kw):
    rows = [{"d": v} for v in values]
    report = CleaningReport(rows_total=len(rows))
    clean_date_column(rows, "d", report, **kw)
    return [r["d"] for r in rows], report


# ------------------------------------------------------------ unambiguous


def test_iso_and_month_names_convert():
    out, rep = _clean(["2026-04-03", "3 Apr 2026", "April 3, 2026", "3rd April 2026"])
    assert out == [datetime(2026, 4, 3)] * 4
    assert not rep.has_issues


def test_time_and_timezone_parse():
    out, _ = _clean(["2026-04-03T14:30:00+01:00", "2026-04-03 2:30 PM"][:1])
    assert out[0] == datetime(2026, 4, 3, 13, 30, tzinfo=timezone.utc)


def test_am_pm():
    out, _ = _clean(["3 Apr 2026 2:30 PM", "3 Apr 2026 12:05 AM"])
    assert out[0] == datetime(2026, 4, 3, 14, 30)
    assert out[1] == datetime(2026, 4, 3, 0, 5)


def test_day_over_12_is_unambiguous_on_its_own():
    out, rep = _clean(["25/04/2026"])
    assert out == [datetime(2026, 4, 25)] and not rep.has_issues
    out, _ = _clean(["04/25/2026"])
    assert out == [datetime(2026, 4, 25)]


def test_same_either_way_is_unambiguous():
    out, rep = _clean(["05/05/2026"])
    assert out == [datetime(2026, 5, 5)] and not rep.has_issues


# ------------------------------------------------------------ the core rule


def test_ambiguous_alone_is_flagged_not_guessed():
    out, rep = _clean(["03/04/2026"])
    assert out == [None]
    (issue,) = rep.issues
    assert issue.kind == "ambiguous_date"
    assert issue.original == "03/04/2026"          # original preserved
    assert set(issue.candidates) == {
        "2026-04-03T00:00:00", "2026-03-04T00:00:00",
    }
    assert rep.ambiguous_dates == 1


def test_column_evidence_resolves_day_first():
    out, rep = _clean(["03/04/2026", "25/04/2026"])
    assert out == [datetime(2026, 4, 3), datetime(2026, 4, 25)]
    assert not rep.has_issues
    assert "day-first" in rep.columns["d"].resolution


def test_column_evidence_resolves_month_first():
    out, _ = _clean(["03/04/2026", "04/25/2026"])
    assert out[0] == datetime(2026, 3, 4)


def test_conflicting_evidence_resolves_nothing():
    out, rep = _clean(["03/04/2026", "25/04/2026", "04/25/2026"])
    assert out[0] is None
    assert out[1] == datetime(2026, 4, 25) and out[2] == datetime(2026, 4, 25)
    assert [i.kind for i in rep.issues] == ["ambiguous_date"]
    assert any("both day-first and month-first" in n for n in rep.columns["d"].notes)


def test_evidence_does_not_cross_separator_styles():
    out, _ = _clean(["03/04/2026", "25.04.2026"])
    assert out[0] is None  # '/' style has no evidence of its own


# ------------------------------------------------------------ declared


def test_dayfirst_declaration_resolves_ambiguous():
    out, rep = _clean(["03/04/2026"], dayfirst=True)
    assert out == [datetime(2026, 4, 3)] and not rep.has_issues
    out, _ = _clean(["03/04/2026"], dayfirst=False)
    assert out == [datetime(2026, 3, 4)]


def test_declared_format_wins():
    out, rep = _clean(["03/04/26", "05-Jan-26"][:1], date_format="%d/%m/%y")
    assert out == [datetime(2026, 4, 3)] and not rep.has_issues


def test_declared_format_mismatch_is_flagged():
    out, rep = _clean(["not a date"], date_format="%d/%m/%Y")
    assert out == [None]
    assert rep.issues[0].kind == "format_mismatch"


def test_declaration_contradicted_by_data_is_reported_but_converted():
    out, rep = _clean(["04/25/2026"], dayfirst=True)
    assert out == [datetime(2026, 4, 25)]
    assert rep.issues[0].kind == "date_order_conflict"


# ------------------------------------------------------------ invalid / missing


def test_impossible_dates_are_flagged():
    out, rep = _clean(["31/02/2026", "2026-13-01"])
    assert out == [None, None]
    assert {i.kind for i in rep.issues} == {"invalid_date"}


def test_missing_tokens_are_counted_not_flagged():
    out, rep = _clean(["N/A", "", None, "2026-01-02"])
    assert out[:3] == [None, None, None]
    assert rep.columns["d"].missing_in_source == 3
    assert not rep.has_issues


def test_unrecognised_text_is_flagged():
    out, rep = _clean(["2026-01-02", "next tuesday"])
    assert out[1] is None and rep.issues[0].kind == "unparseable_date"


def test_mixed_timezone_naive_and_aware_not_converted():
    out, rep = _clean(["2026-01-02T10:00:00Z", "2026-01-02 10:00"])
    assert out == [None, None]
    assert {i.kind for i in rep.issues} == {"mixed_timezone"}


def test_aware_values_normalised_to_utc():
    out, rep = _clean(["2026-01-02T10:00:00+01:00", "2026-01-02T10:00:00Z"])
    assert out[0] == datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
    assert any("UTC" in n for n in rep.columns["d"].notes)


# ------------------------------------------------------------ detection


def test_date_column_detection():
    assert looks_like_date_column(["2026-01-02", "2026-01-03", "N/A"])
    assert not looks_like_date_column(["apple", "banana"])
    assert not looks_like_date_column(["2024", "2025"])      # bare years
    assert not looks_like_date_column(["3.14", "2.71"])      # decimals


# ------------------------------------------------------------ normalize


def test_normalize_infers_date_column_and_reports():
    rows = [
        {"name": "a", "published": "03/04/2026"},
        {"name": "b", "published": "25/04/2026"},
        {"name": "c", "published": "01/05/2026"},
    ]
    res = normalize_rows(rows)
    df = res.to_dataframe()
    assert str(df["published"].dtype).startswith("datetime64")
    assert df["published"].tolist()[0] == datetime(2026, 4, 3)
    assert not res.cleaning.has_issues


def test_normalize_ambiguous_becomes_nat_and_original_kept_in_report():
    res = normalize_rows([{"d": "03/04/2026"}, {"d": "2026-01-02"}])
    df = res.to_dataframe()
    assert df["d"].isna().tolist() == [True, False]
    assert res.cleaning.issues[0].original == "03/04/2026"
    assert res.cleaning.issues[0].row_index == 0


def test_normalize_does_not_mutate_input():
    rows = [{"d": "2026-01-02"}]
    normalize_rows(rows)
    assert rows == [{"d": "2026-01-02"}]


def test_schema_date_field_uses_cleaner():
    class Post(BaseModel):
        title: str
        published: date

    res = normalize_rows(
        [{"title": "a", "published": "2026-04-25"},
         {"title": "b", "published": "03/04/2026"}],
        schema=Post,
    )
    assert res.valid_rows[0]["published"] == date(2026, 4, 25)
    # the ambiguous one cannot be guessed, so the row is quarantined
    assert len(res.invalid_rows) == 1
    assert res.invalid_rows[0]["published"] == "03/04/2026"
    assert res.cleaning.rows_quarantined == 1


def test_schema_optional_datetime_ambiguous_is_none_and_reported():
    class Post(BaseModel):
        title: str
        published: datetime | None = None

    res = normalize_rows([{"title": "a", "published": "03/04/2026"}], schema=Post)
    assert res.valid_rows[0]["published"] is None
    assert res.cleaning.ambiguous_dates == 1


def test_date_formats_argument_in_normalize():
    res = normalize_rows(
        [{"d": "03/04/2026"}], date_formats={"d": "%d/%m/%Y"}
    )
    assert res.valid_rows[0]["d"] == datetime(2026, 4, 3)


# ------------------------------------------------------------ other cleaning


def test_inference_no_longer_turns_codes_into_booleans():
    res = normalize_rows([{"n": "1", "g": "F"}, {"n": "0", "g": "A"}, {"n": "2", "g": "N"}])
    assert [r["n"] for r in res.valid_rows] == [1, 0, 2]
    assert [r["g"] for r in res.valid_rows] == ["F", "A", "N"]


def test_inference_still_reads_true_false_words():
    res = normalize_rows([{"x": "true"}, {"x": "No"}])
    assert [r["x"] for r in res.valid_rows] == [True, False]


def test_ambiguous_comma_is_not_converted_and_original_is_reported():
    res = normalize_rows([{"p": "12,5"}, {"p": "1,234"}, {"p": "7"}])
    # '12,5' could be 12.5 or 125: it becomes missing, original kept in report
    assert [r["p"] for r in res.valid_rows] == [None, 1234, 7]
    assert res.cleaning.issues_of("ambiguous_number")[0].original == "12,5"


def test_ambiguous_comma_in_a_text_column_stays_text():
    res = normalize_rows([{"p": "12,5"}, {"p": "apple"}])
    assert [r["p"] for r in res.valid_rows] == ["12,5", "apple"]


class _P(BaseModel):
    name: str
    price: float | None = None
    qty: int | None = None


def test_value_that_becomes_missing_is_recorded():
    res = normalize_rows([{"name": "a", "price": "TBD", "qty": "N/A"}], schema=_P)
    issues = res.cleaning.issues
    assert [(i.column, i.kind) for i in issues] == [("price", "became_missing")]


def test_partial_parse_and_lossy_int_are_not_converted():
    """'12-15' must not become 12.0, and '4.5' must not become 4."""
    res = normalize_rows(
        [{"name": "a", "price": "12-15", "qty": "4.5"}], schema=_P
    )
    kinds = {(i.column, i.kind) for i in res.cleaning.issues}
    assert kinds == {("price", "partial_parse"), ("qty", "lossy_int")}
    assert res.valid_rows[0]["price"] is None
    assert res.valid_rows[0]["qty"] is None
    assert {i.original for i in res.cleaning.issues} == {"12-15", "4.5"}


class _Req(BaseModel):
    name: str
    price: float


def test_required_field_with_unconvertible_number_is_quarantined():
    res = normalize_rows(
        [{"name": "a", "price": "12-15"}, {"name": "b", "price": "$9.99"}],
        schema=_Req,
    )
    assert len(res.valid_rows) == 1 and res.valid_rows[0]["name"] == "b"
    assert res.invalid_rows[0]["price"] == "12-15"        # original preserved
    assert res.cleaning.rows_quarantined == 1


def test_clean_currency_values_are_not_flagged():
    res = normalize_rows(
        [{"name": "a", "price": "$1,234.56", "qty": "7"},
         {"name": "b", "price": "€12.50", "qty": "3"},
         {"name": "c", "price": "45%", "qty": "1,000"}],
        schema=_P,
    )
    assert not res.cleaning.has_issues


# ------------------------------------------------------------ end to end


HTML_DATES = """
<html><body>
<div class="post"><span class="t">One</span><span class="d">{d1}</span></div>
<div class="post"><span class="t">Two</span><span class="d">{d2}</span></div>
</body></html>
"""
SEL = {"_container": "div.post", "title": "span.t", "published": "span.d"}


def _run(url, root, **kw):
    return Scrape(
        url=url, selectors=SEL, storage_root=root, rate_limit=1000.0, **kw
    ).run()


def test_e2e_ambiguous_flagged_and_cleaning_json_saved(tmp_path):
    root = str(tmp_path / "chronicle")
    html = HTML_DATES.format(d1="03/04/2026", d2="2026-01-02")
    with http_server(html) as url:
        r = _run(url, root)
    assert r.cleaning.ambiguous_dates == 1
    assert r.to_dataframe()["published"].isna().tolist() == [True, False]
    m = r.manifest
    path = Path(root) / "projects" / m["project_id"] / "runs" / m["run_id"] / "cleaning.json"
    data = json.loads(path.read_text())
    assert data["issue_details"][0]["original"] == "03/04/2026"
    assert data["issue_details"][0]["kind"] == "ambiguous_date"


def test_e2e_declaring_dayfirst_resolves_it(tmp_path):
    root = str(tmp_path / "chronicle")
    html = HTML_DATES.format(d1="03/04/2026", d2="25/04/2026")
    with http_server(html) as url:
        r = _run(url, root, dayfirst=True)
    assert not r.cleaning.has_issues
    assert r.to_dataframe()["published"].iloc[0] == datetime(2026, 4, 3)



# ------------------------------------------------------------ column typing


def test_numeric_column_with_na_stays_numeric():
    """One 'N/A' used to make the column mixed (and the run unsaveable)."""
    res = normalize_rows([{"v": "10"}, {"v": "20"}, {"v": "N/A"}])
    assert [r["v"] for r in res.valid_rows] == [10, 20, None]
    assert res.cleaning.columns["v"].missing_in_source == 1
    assert any("N/A" in n for n in res.cleaning.columns["v"].notes)


def test_na_in_a_text_column_is_not_turned_into_missing():
    """'NA' is Namibia; only numeric columns treat markers as missing."""
    res = normalize_rows([{"c": "NA"}, {"c": "ZA"}])
    assert [r["c"] for r in res.valid_rows] == ["NA", "ZA"]


def test_mixed_numbers_and_text_kept_as_original_text():
    res = normalize_rows([{"v": "10"}, {"v": "abc"}, {"v": "007"}])
    assert [r["v"] for r in res.valid_rows] == ["10", "abc", "007"]
    assert any("mixed" in n for n in res.cleaning.columns["v"].notes)


def test_leading_zeros_are_preserved():
    res = normalize_rows([{"sku": "00123", "zip": "0803", "n": "0", "x": "12"}])
    row = res.valid_rows[0]
    assert row["sku"] == "00123" and row["zip"] == "0803"
    assert row["n"] == 0 and row["x"] == 12


def test_typed_source_does_not_reinterpret_strings():
    res = normalize_rows(
        [{"id": "00123", "flag": "yes", "n": 5}], typed_source=True
    )
    assert res.valid_rows[0] == {"id": "00123", "flag": "yes", "n": 5}


def test_lists_and_dicts_pass_through_inference():
    res = normalize_rows([{"tags": ["a", "b"], "meta": {"k": 1}}])
    assert res.valid_rows[0] == {"tags": ["a", "b"], "meta": {"k": 1}}


def test_empty_column_is_warned_about():
    res = normalize_rows([{"a": "x", "b": None}, {"a": "y", "b": ""}])
    assert any("'b'" in w and "empty" in w for w in res.cleaning.warnings)


def test_mixed_type_run_can_be_stored(tmp_path):
    """Regression: a mixed column used to raise StorageError on save."""
    from chronicle.storage import Storage

    res = normalize_rows([{"v": "10"}, {"v": "N/A"}, {"v": "abc"}])
    m = Storage(str(tmp_path)).save_run("http://x/", res.to_dataframe())
    assert m.row_count == 3


def test_storage_fallback_stores_mixed_object_column_as_text(tmp_path):
    import pandas as pd

    from chronicle.storage import Storage

    df = pd.DataFrame({"v": [1, "x", None]})   # bypasses normalize on purpose
    m = Storage(str(tmp_path)).save_run("http://x/", df)
    assert any("mixed types" in e for e in m.errors)


def test_em_dash_is_a_missing_marker_and_other_currencies_parse():
    res = normalize_rows([{"v": "10"}, {"v": "\u2014"}, {"v": "\u00a31,299.50"}])
    assert [r["v"] for r in res.valid_rows] == [10, None, 1299.5]
