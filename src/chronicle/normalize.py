"""Normalization layer for Chronicle.

Takes raw parsed rows (list of dicts of strings) and turns them into
typed, validated, analysis-ready data.

Handles:
- Type coercion (str → int/float/bool, cleaning $ , % etc.)
- Schema validation via pydantic
- Quarantining invalid rows instead of silently dropping them
- Generating a data quality report

Usage:
    from pydantic import BaseModel
    from chronicle.normalize import normalize_rows

    class Product(BaseModel):
        name: str
        price: float
        rating: float | None = None

    result = normalize_rows(rows, schema=Product)
    df = result.to_dataframe()
    report = result.quality_report()
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Union, get_args, get_origin

import pandas as pd
from pydantic import BaseModel, ValidationError

from chronicle.exceptions import SchemaError

# ---------------------------------------------------------------- coercion

_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")
_NULL_STRINGS = {"n/a", "na", "null", "none", "-", "--", "nan", ""}
_TRUE_VALUES = {"true", "yes", "y", "1", "t"}
_FALSE_VALUES = {"false", "no", "n", "0", "f"}


def coerce_to_float(value: Any) -> float | None:
    """Coerce '$1,234.56' or '45%' to a float. Returns None if not numeric."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in _NULL_STRINGS:
        return None
    match = _NUMERIC_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def coerce_to_int(value: Any) -> int | None:
    f = coerce_to_float(value)
    if f is None:
        return None
    try:
        return int(f)
    except (ValueError, OverflowError):
        return None


def coerce_to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def coerce_value(value: Any, target_type: type) -> Any:
    """Coerce a single value to a target python type."""
    if value is None:
        return None
    if target_type is float:
        return coerce_to_float(value)
    if target_type is int:
        return coerce_to_int(value)
    if target_type is bool:
        return coerce_to_bool(value)
    if target_type is str:
        return str(value).strip()
    return value


# ---------------------------------------------------------------- result


@dataclass
class NormalizeResult:
    """Result of normalizing rows against a schema."""

    valid_rows: list[dict[str, Any]] = field(default_factory=list)
    invalid_rows: list[dict[str, Any]] = field(default_factory=list)
    schema: type[BaseModel] | None = None
    columns: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.valid_rows) + len(self.invalid_rows)

    @property
    def success_rate(self) -> float:
        return len(self.valid_rows) / self.total if self.total else 0.0

    def to_dataframe(self) -> pd.DataFrame:
        if not self.valid_rows:
            return pd.DataFrame(columns=self.columns)
        return pd.DataFrame(self.valid_rows)

    def to_invalid_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.invalid_rows) if self.invalid_rows else pd.DataFrame()

    def quality_report(self) -> dict[str, Any]:
        df = self.to_dataframe()
        report: dict[str, Any] = {
            "rows_total": self.total,
            "rows_valid": len(self.valid_rows),
            "rows_invalid": len(self.invalid_rows),
            "success_rate": round(self.success_rate, 4),
            "columns": {},
        }
        if df.empty:
            return report

        for col in df.columns:
            series = df[col]
            non_null = series.dropna()
            info: dict[str, Any] = {
                "dtype": str(series.dtype),
                "null_pct": round(float(series.isna().mean()), 4),
                "unique_count": int(series.nunique(dropna=True)),
            }
            if pd.api.types.is_numeric_dtype(series) and len(non_null) > 0:
                info.update({
                    "min": float(non_null.min()),
                    "max": float(non_null.max()),
                    "mean": round(float(non_null.mean()), 4),
                    "median": float(non_null.median()),
                    "std": round(float(non_null.std()), 4) if len(non_null) > 1 else 0.0,
                })
            else:
                info["sample_values"] = [str(v) for v in non_null.head(3).tolist()]
            report["columns"][col] = info

        return report


# ---------------------------------------------------------------- main


def normalize_rows(
    rows: list[dict[str, Any]],
    schema: type[BaseModel] | None = None,
    strict: bool = False,
) -> NormalizeResult:
    """Normalize and validate parsed rows.

    Args:
        rows: Raw rows from parse_html() — usually all strings.
        schema: Optional pydantic BaseModel class to validate against.
        strict: If True, raise SchemaError on the first invalid row.
                If False (default), quarantine invalid rows.

    Returns:
        A NormalizeResult with valid_rows, invalid_rows, and helpers.
    """
    if not rows:
        return NormalizeResult(schema=schema)

    columns = list(rows[0].keys())

    # No schema: best-effort inference
    if schema is None:
        return NormalizeResult(
            valid_rows=[_infer_types(r) for r in rows],
            schema=None,
            columns=columns,
        )

    field_types = _extract_field_types(schema)

    valid_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        # Coerce each field to its target type
        coerced = dict(row)
        for field_name, target_type in field_types.items():
            if field_name in coerced:
                coerced[field_name] = coerce_value(coerced[field_name], target_type)

        # Validate with pydantic
        try:
            validated = schema.model_validate(coerced)
            valid_rows.append(validated.model_dump())
        except ValidationError as e:
            if strict:
                raise SchemaError(f"Row {i} failed validation: {e}") from e
            invalid_rows.append({
                **row,
                "_error": str(e),
                "_row_index": i,
            })

    return NormalizeResult(
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        schema=schema,
        columns=list(field_types.keys()) or columns,
    )


# ---------------------------------------------------------------- helpers


def _extract_field_types(schema: type[BaseModel]) -> dict[str, type]:
    """Extract {field_name: python_type} from a pydantic model."""
    return {
        name: _unwrap_optional(info.annotation)
        for name, info in schema.model_fields.items()
    }


def _unwrap_optional(annotation: Any) -> type:
    """Return X from Optional[X] or Union[X, None]. Otherwise return annotation."""
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if args:
            return args[0]
    return annotation if isinstance(annotation, type) else str


def _infer_types(row: dict[str, Any]) -> dict[str, Any]:
    """Best-effort inference when no schema is given."""
    return {k: _infer_value(v) for k, v in row.items()}


def _infer_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    text = str(v).strip()
    if not text:
        return None
    lower = text.lower()
    if lower in _TRUE_VALUES:
        return True
    if lower in _FALSE_VALUES:
        return False
    # Only infer numeric if the ENTIRE cleaned string is numeric
    cleaned = text.replace(",", "").replace("$", "").replace("%", "").strip()
    if re.fullmatch(r"-?\d+", cleaned):
        try:
            return int(cleaned)
        except ValueError:
            pass
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        try:
            return float(cleaned)
        except ValueError:
            pass
    return text