"""Tests for chronicle.drift."""

from chronicle.drift import detect_drift, ks_drift

BASELINE = {
    "row_count": 100,
    "columns": {
        "name": {"dtype": "object", "null_pct": 0.0, "unique_count": 100},
        "price": {
            "dtype": "float64", "null_pct": 0.02, "unique_count": 90,
            "min": 10.0, "max": 500.0, "mean": 145.0, "median": 120.0,
        },
        "rating": {
            "dtype": "float64", "null_pct": 0.05, "unique_count": 8,
            "min": 1.0, "max": 5.0, "mean": 4.2, "median": 4.3,
        },
    },
}


def test_no_drift_when_identical():
    report = detect_drift(BASELINE, BASELINE)
    assert not report.has_drift
    assert report.severity == "NORMAL"


def test_severe_null_drift_flagged():
    current = {
        "row_count": 100,
        "columns": {
            **BASELINE["columns"],
            "price": {**BASELINE["columns"]["price"], "null_pct": 0.98},
        },
    }
    report = detect_drift(BASELINE, current)
    assert report.has_drift
    assert report.severity == "SEVERE"
    # Should flag null drift
    null_findings = [f for f in report.findings if f.kind == "null"]
    assert len(null_findings) == 1
    assert null_findings[0].severity == "SEVERE"


def test_schema_drift_column_removed():
    current = {
        "row_count": 100,
        "columns": {
            "name": BASELINE["columns"]["name"],
            # price and rating both missing
        },
    }
    report = detect_drift(BASELINE, current)
    assert report.has_drift
    removed = [f for f in report.findings if "disappeared" in f.message]
    assert len(removed) == 2


def test_schema_drift_column_added():
    current = {
        "row_count": 100,
        "columns": {
            **BASELINE["columns"],
            "brand": {"dtype": "object", "null_pct": 0.0, "unique_count": 5},
        },
    }
    report = detect_drift(BASELINE, current)
    added = [f for f in report.findings if "appeared" in f.message]
    assert len(added) == 1
    assert added[0].severity == "WARNING"


def test_ks_drift_identical_samples():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    result = ks_drift(a, a)
    assert not result["drift"]


def test_ks_drift_very_different_samples():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    b = [100.0, 200.0, 300.0, 400.0, 500.0] * 10
    result = ks_drift(a, b)
    assert result["drift"]
    assert result["severity"] == "SEVERE"