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
import types
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Union, get_args, get_origin

import pandas as pd
from pydantic import BaseModel, ValidationError

from chronicle.clean import (
    NULL_TOKENS,
    CleaningIssue,
    CleaningReport,
    ColumnCleaning,
    clean_date_column,
    looks_like_date_column,
)
from chronicle.exceptions import SchemaError

# ---------------------------------------------------------------- coercion

_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")
_NULL_STRINGS = NULL_TOKENS
_TRUE_VALUES = {"true", "yes", "y", "1", "t"}
_FALSE_VALUES = {"false", "no", "n", "0", "f"}
# When NO schema says "this is a boolean", only unmistakable words count.
# "1", "0", "F", "N", "y" are far more often numbers, grades or codes.
_INFER_TRUE = {"true", "yes"}
_INFER_FALSE = {"false", "no"}

_THOUSANDS_RE = re.compile(r"-?\d{1,3}(,\d{3})+(\.\d+)?")
_PLAIN_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
_CURRENCY_CHARS = "$€£¥₦₹%"


def _strip_numeric_decoration(text: str) -> str:
    return text.strip().strip(_CURRENCY_CHARS).strip()


def _comma_is_ambiguous(text: str) -> bool:
    """True if a comma might be a decimal separator ('12,5', '1.234,56')."""
    core = _strip_numeric_decoration(text)
    if "," not in core:
        return False
    return not _THOUSANDS_RE.fullmatch(core)


def _numeric_flag(original: Any, target: type) -> tuple[str, str] | None:
    """Describe a risky numeric conversion without changing its result."""
    if not isinstance(original, str):
        return None
    text = original.strip()
    if text.lower() in _NULL_STRINGS:
        return None
    core = _strip_numeric_decoration(text)
    if _comma_is_ambiguous(text):
        return ("ambiguous_number",
                "comma may be a decimal separator, not a thousands separator")
    plain = core.replace(",", "")
    if not _PLAIN_NUMBER_RE.fullmatch(plain):
        return ("partial_parse",
                "text around the number was ignored (first number was used)")
    if target is int and "." in plain and float(plain) != int(float(plain)):
        return ("lossy_int", "fractional part was truncated")
    return None


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
    cleaning: CleaningReport = field(default_factory=CleaningReport)

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
    dayfirst: bool | None = None,
    date_formats: dict[str, str] | None = None,
    typed_source: bool = False,
) -> NormalizeResult:
    """Normalize and validate parsed rows.

    Args:
        rows: Raw rows from parse_html() — usually all strings.
        schema: Optional pydantic BaseModel class to validate against.
        strict: If True, raise SchemaError on the first invalid row.
                If False (default), quarantine invalid rows.
        dayfirst: Declare day/month order for ambiguous numeric dates such
                as 03/04/2026. None (default) means "infer from the column,
                and flag what cannot be inferred".
        date_formats: Per-column strptime formats, e.g.
                {"published": "%d/%m/%Y"}. These win over everything else.
        typed_source: True when values already carry real types (JSON).
                Strings are then NOT re-interpreted as numbers or booleans:
                a JSON string "00123" stays "00123".

    Returns:
        A NormalizeResult with valid_rows, invalid_rows, a cleaning report,
        and helpers.
    """
    if not rows:
        return NormalizeResult(schema=schema)

    date_formats = date_formats or {}
    columns = list(rows[0].keys())
    report = CleaningReport(rows_total=len(rows))
    work = [dict(r) for r in rows]  # never mutate the caller's rows

    field_types = _extract_field_types(schema) if schema is not None else {}

    # ---- dates: decided per COLUMN, because evidence is column-wide
    date_cols: dict[str, bool] = {}  # column -> as_date
    for col in date_formats:
        if col in columns:
            date_cols[col] = field_types.get(col) is date
    if schema is not None:
        for name, tp in field_types.items():
            if tp in (datetime, date) and name in columns:
                date_cols[name] = tp is date
    else:
        for col in columns:
            if col not in date_cols and looks_like_date_column(
                [r.get(col) for r in work]
            ):
                date_cols[col] = False
    for col, as_date in date_cols.items():
        clean_date_column(
            work, col, report,
            dayfirst=dayfirst,
            date_format=date_formats.get(col),
            as_date=as_date,
        )

    # No schema: best-effort inference
    if schema is None:
        inferred = [
            _infer_types(r, i, report, skip=set(date_cols), typed=typed_source)
            for i, r in enumerate(work)
        ]
        _resolve_columns(inferred, work, report, skip=set(date_cols))
        _warn_empty_columns(inferred, columns, report)
        return NormalizeResult(
            valid_rows=inferred,
            schema=None,
            columns=columns,
            cleaning=report,
        )

    valid_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    for i, row in enumerate(work):
        # Coerce each field to its target type
        coerced = dict(row)
        for field_name, target_type in field_types.items():
            if field_name in coerced and field_name not in date_cols:
                original = coerced[field_name]
                converted = coerce_value(original, target_type)
                converted = _record_coercion(
                    report, i, field_name, original, converted, target_type
                )
                coerced[field_name] = converted

        # Validate with pydantic
        try:
            validated = schema.model_validate(coerced)
            valid_rows.append(validated.model_dump())
        except ValidationError as e:
            if strict:
                raise SchemaError(f"Row {i} failed validation: {e}") from e
            invalid_rows.append({
                **rows[i],
                "_error": str(e),
                "_row_index": i,
            })

    report.rows_quarantined = len(invalid_rows)
    _warn_empty_columns(work, [c for c in columns if c in field_types], report)
    return NormalizeResult(
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        schema=schema,
        columns=list(field_types.keys()) or columns,
        cleaning=report,
    )


def _record_coercion(
    report: CleaningReport,
    row_index: int,
    column: str,
    original: Any,
    converted: Any,
    target_type: type,
) -> Any:
    """Record risky conversions and refuse the ones that would be wrong.

    Returns the value to use. A number read out of surrounding text
    ("12-15" -> 12), a truncated integer ("4.5" -> 4) or a comma that may be
    a decimal separator ("12,5" -> 125) is NOT converted: the value becomes
    missing, the original text stays in the report, and a required field
    quarantines the row (CORE principle 4).
    """
    if isinstance(original, str):
        text = original.strip()
        if text and text.lower() not in _NULL_STRINGS and converted is None:
            report.issues.append(CleaningIssue(
                row_index, column, original, "became_missing",
                f"could not be read as {getattr(target_type, '__name__', target_type)}",
            ))
            return None
    if target_type in (int, float):
        flag = _numeric_flag(original, target_type)
        if flag:
            report.issues.append(CleaningIssue(
                row_index, column, original, flag[0],
                flag[1] + "; not converted",
            ))
            return None
    return converted


def _warn_empty_columns(
    rows: list[dict[str, Any]], columns: list[str], report: CleaningReport
) -> None:
    """A column that is missing in every row usually means a wrong selector."""
    if not rows:
        return
    for col in columns:
        if all(_is_blank(r.get(col)) for r in rows):
            report.warnings.append(
                f"column '{col}' is empty in all {len(rows)} rows; check the "
                "selector or field path"
            )


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() in _NULL_STRINGS
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _resolve_columns(
    inferred: list[dict[str, Any]],
    originals: list[dict[str, Any]],
    report: CleaningReport,
    skip: set[str],
) -> None:
    """Give every column ONE type (CORE principle 5), in place.

    Per-cell inference can leave ``[10, 20, "N/A"]`` in one column, which is
    not a typed column and cannot even be stored as parquet. Rules:

    - numbers + missing-markers ("N/A", "-") + ambiguous numbers -> numeric
      column; the markers become missing and are counted in the report.
    - numbers/booleans mixed with real text -> the whole column is kept as
      the ORIGINAL text, and the report says so.
    """
    if not inferred:
        return
    for col in inferred[0]:
        if col in skip:
            continue
        kinds: set[str] = set()
        for r in inferred:
            v = r.get(col)
            if v is None:
                continue
            if isinstance(v, bool):
                kinds.add("bool")
            elif isinstance(v, (int, float)):
                kinds.add("num")
            elif isinstance(v, str):
                kinds.add("str")
            else:
                kinds.add("other")
        if "other" in kinds or kinds in (set(), {"bool"}, {"num"}, {"str"}):
            continue
        strings = [r[col] for r in inferred if isinstance(r.get(col), str)]
        ambiguous = {
            i.original for i in report.issues
            if i.column == col and i.kind == "ambiguous_number"
        }
        only_markers = all(
            st.strip().lower() in _NULL_STRINGS or st in ambiguous for st in strings
        )
        info = report.columns.setdefault(col, ColumnCleaning(name=col, kind="inferred"))
        if kinds == {"num", "str"} and only_markers:
            became = 0
            for r in inferred:
                v = r.get(col)
                if isinstance(v, str):
                    r[col] = None
                    became += 1
            info.missing_in_source += became
            info.notes.append(
                f"{became} value(s) such as 'N/A' were read as missing so the "
                "column could stay numeric"
            )
        else:
            for r, o in zip(inferred, originals):
                v = r.get(col)
                if v is not None and not isinstance(v, str):
                    r[col] = str(o.get(col)).strip()
            info.notes.append(
                "mixed numbers/booleans and text; kept as the original text"
            )


# ---------------------------------------------------------------- helpers


def _extract_field_types(schema: type[BaseModel]) -> dict[str, type]:
    """Extract {field_name: python_type} from a pydantic model."""
    return {
        name: _unwrap_optional(info.annotation)
        for name, info in schema.model_fields.items()
    }


def _unwrap_optional(annotation: Any) -> type:
    """Return X from Optional[X] or Union[X, None].

    Handles both typing.Union (from Optional[X]) and types.UnionType
    (from PEP 604 `X | None` syntax on Python 3.10+).

    On Python 3.10+, `get_origin(float | None)` returns `types.UnionType`,
    NOT `typing.Union`. This distinction matters when running across
    multiple Python versions in CI.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if args:
            return args[0]
    return annotation if isinstance(annotation, type) else str


def _infer_types(
    row: dict[str, Any],
    row_index: int = 0,
    report: CleaningReport | None = None,
    skip: set[str] | None = None,
    typed: bool = False,
) -> dict[str, Any]:
    """Best-effort inference when no schema is given.

    With ``typed=True`` (JSON) values already have real types, so strings
    are left exactly as they are.
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        if (skip and k in skip) or typed:
            out[k] = v  # resolved by the date cleaner, or already typed
            continue
        if (
            report is not None
            and isinstance(v, str)
            and _comma_is_ambiguous(v)
            and re.fullmatch(r"-?[\d.,]+", _strip_numeric_decoration(v))
        ):
            report.issues.append(CleaningIssue(
                row_index, k, v, "ambiguous_number",
                "comma may be a decimal separator, not a thousands "
                "separator; not converted",
            ))
        out[k] = _infer_value(v)
    return out


def _infer_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, bool, datetime, date, list, tuple, dict)):
        return v
    text = str(v).strip()
    if not text:
        return None
    lower = text.lower()
    if lower in _INFER_TRUE:
        return True
    if lower in _INFER_FALSE:
        return False
    if _comma_is_ambiguous(text) and re.fullmatch(
        r"-?[\d.,]+", _strip_numeric_decoration(text)
    ):
        return text  # '12,5' is not 125; keep the original text
    # Only infer numeric if the ENTIRE cleaned string is numeric
    cleaned = _strip_numeric_decoration(text).replace(",", "")
    # "00123", "0803..." are identifiers (SKU, phone, ZIP), not numbers:
    # converting them would silently drop the leading zeros.
    if re.fullmatch(r"-?0\d+", cleaned):
        return text
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