"""JSON / API connector for Chronicle (CORE principle 8, paths 1 and 2).

Official APIs and public JSON endpoints are the most legitimate way to get
data, and they need no HTML selectors. Examples this module is built for:

- World Bank API   ``[ {"page":1,"pages":3,...}, [ {...}, {...} ] ]``
- Shopify catalogue ``/products.json``  ->  ``{"products": [ {...} ]}``
- Plain REST lists  ``[ {...}, {...} ]``  or  ``{"data": {"items": [...]}}``

Rules (CORE principle 4, never guess):

- If there is exactly ONE obvious list of records, Chronicle uses it.
- If there are several candidates, or none, it raises an error that names
  them and asks for ``records_path``. It never picks one silently.
- Values keep their JSON types. A JSON string "00123" stays "00123".
"""

from __future__ import annotations

import json
from typing import Any

from chronicle.exceptions import ParseError

_MISSING = object()


def looks_like_json(text: str) -> bool:
    """True if ``text`` plausibly starts a JSON document."""
    t = text.lstrip()[:1]
    return t in ("{", "[")


def get_path(data: Any, path: str) -> Any:
    """Follow a dotted path. Segments index dicts by key, lists by integer.

    ``get_path(d, "data.items")``, ``get_path(d, "1")`` (second element of a
    top-level list, as in the World Bank API), ``get_path(rec, "a.b.0.c")``.
    Returns the module-level ``_MISSING`` sentinel when the path is absent
    (``None`` is a legitimate JSON value, so it cannot signal absence).
    """
    cur = data
    for seg in path.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return _MISSING
            cur = cur[seg]
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return cur


def _is_record_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(v, dict) for v in value)
    )


def locate_records(data: Any, records_path: str | None = None) -> list[dict[str, Any]]:
    """Find the list of record dicts inside a parsed JSON document."""
    if records_path:
        found = get_path(data, records_path)
        if found is _MISSING:
            raise ParseError(
                f"records_path {records_path!r} was not found in the JSON response"
            )
        if isinstance(found, list):
            bad = [v for v in found if not isinstance(v, dict)]
            if bad:
                raise ParseError(
                    f"records_path {records_path!r} is a list, but not every "
                    "item is an object"
                )
            return found
        raise ParseError(
            f"records_path {records_path!r} does not point to a list of objects"
        )

    if _is_record_list(data):
        return data
    if isinstance(data, list) and not data:
        return []

    candidates: dict[str, list[dict[str, Any]]] = {}
    if isinstance(data, list):
        for i, item in enumerate(data):
            if _is_record_list(item):
                candidates[str(i)] = item
    elif isinstance(data, dict):
        for key, value in data.items():
            if _is_record_list(value):
                candidates[str(key)] = value
        if not candidates:
            # one level down, e.g. {"data": {"items": [...]}}
            for key, value in data.items():
                if isinstance(value, dict):
                    for k2, v2 in value.items():
                        if _is_record_list(v2):
                            candidates[f"{key}.{k2}"] = v2

    if len(candidates) == 1:
        return next(iter(candidates.values()))
    if not candidates:
        # an empty page of a known API shape: treat "all lists are empty" as zero rows
        if _only_empty_lists(data):
            return []
        raise ParseError(
            "No list of records found in the JSON response. Set "
            "records_path='dotted.path.to.the.list'."
        )
    raise ParseError(
        f"Several lists of records found ({', '.join(sorted(candidates))}); "
        "Chronicle will not guess. Set records_path to the one you want."
    )


def _only_empty_lists(data: Any) -> bool:
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        return bool(lists) and all(len(v) == 0 for v in lists)
    if isinstance(data, list):
        lists = [v for v in data if isinstance(v, list)]
        return bool(lists) and all(len(v) == 0 for v in lists)
    return False


def flatten_record(record: dict[str, Any], sep: str = ".", _prefix: str = "") -> dict[str, Any]:
    """Flatten nested objects into dotted columns. Lists are kept as lists."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{_prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(flatten_record(value, sep, f"{name}{sep}"))
        else:
            out[name] = value
    return out


def extract_fields(record: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    """Pick named fields by dotted path. An absent path becomes None."""
    row: dict[str, Any] = {}
    for name, path in fields.items():
        value = get_path(record, path)
        row[name] = None if value is _MISSING else value
    return row


def parse_json(
    text: str,
    records_path: str | None = None,
    fields: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Turn a JSON response body into rows (a list of flat dicts)."""
    if not text or not text.strip():
        raise ParseError("Empty response passed to parse_json")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ParseError(f"Response is not valid JSON: {e}") from e

    records = locate_records(data, records_path)
    if fields:
        return [extract_fields(r, fields) for r in records]
    return [flatten_record(r) for r in records]
