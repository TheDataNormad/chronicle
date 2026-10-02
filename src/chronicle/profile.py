"""Profile generation for Chronicle.

Turns a DataFrame into a statistical fingerprint:
- Row/column counts
- Per-column: dtype, null %, unique count
- Numeric columns: min, max, mean, median, std
- String columns: sample values

This fingerprint is what drift detection compares against.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def generate_profile(df: pd.DataFrame) -> dict[str, Any]:
    """Build a statistical profile of a DataFrame.

    Args:
        df: A pandas DataFrame.

    Returns:
        A nested dict with row/column counts and per-column stats.
    """
    profile: dict[str, Any] = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": {},
    }

    for col in df.columns:
        series = df[col]
        non_null = series.dropna()

        col_profile: dict[str, Any] = {
            "dtype": str(series.dtype),
            "null_pct": round(float(series.isna().mean()), 4),
            "unique_count": int(series.nunique(dropna=True)),
        }

        # Numeric columns: full stats
        if pd.api.types.is_numeric_dtype(series) and len(non_null) > 0:
            col_profile.update({
                "min": float(non_null.min()),
                "max": float(non_null.max()),
                "mean": round(float(non_null.mean()), 4),
                "median": float(non_null.median()),
                "std": (
                    round(float(non_null.std()), 4)
                    if len(non_null) > 1 else 0.0
                ),
            })
        else:
            # String / categorical
            col_profile["sample_values"] = [
                str(v) for v in non_null.head(3).tolist()
            ]

        profile["columns"][col] = col_profile

    return profile