"""HTML parsing layer for Chronicle.

Turns raw HTML into structured rows (list of dicts).

Two modes:
1. Selector mode — you define CSS selectors for each field.
2. Auto mode — Chronicle guesses the repeating structure (tables, lists).

Usage:
    from chronicle.parse import parse_html

    rows = parse_html(
        html,
        container="div.product",
        selectors={"name": "h2", "price": "span.price"},
    )
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from chronicle.exceptions import ParseError


def parse_html(
    html: str,
    container: str,
    selectors: dict[str, str],
    base_url: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Parse HTML into a list of row dicts.

    Args:
        html: Raw HTML string.
        container: CSS selector matching each repeating row.
        selectors: Map of field name -> CSS selector relative to the container.
        base_url: Optional URL used to resolve relative hrefs/srcs.

    Returns:
        List of dicts, one per row.

    Raises:
        ParseError: If the HTML cannot be parsed.
    """
    if not html or not html.strip():
        raise ParseError("Empty HTML passed to parse_html")

    try:
        tree = HTMLParser(html)
    except Exception as e:
        raise ParseError(f"Failed to parse HTML: {e}") from e

    nodes = tree.css(container)
    if not nodes:
        return []

    rows: list[dict[str, Any]] = []
    for node in nodes:
        row: dict[str, Any] = {}
        for field, selector in selectors.items():
            row[field] = _extract_field(node, selector, base_url)
        rows.append(row)

    return rows


def _extract_field(node, selector: str, base_url: Optional[str]) -> Any:
    """Extract a single field from a node using a CSS selector.

    Special syntax:
        "a@href"   -> extract the href attribute of the <a> tag
        "img@src"  -> extract the src attribute
        "@text"    -> extract the node's own text
    """
    # Attribute extraction: "selector@attr"
    if "@" in selector:
        sel, _, attr = selector.partition("@")
        target = node.css_first(sel) if sel else node
        if target is None:
            return None
        value = target.attributes.get(attr)
        if value and base_url and attr in ("href", "src"):
            value = urljoin(base_url, value)
        return value

    # Plain text extraction
    target = node.css_first(selector)
    if target is None:
        return None
    return target.text(strip=True)


def detect_tables(html: str) -> list[dict[str, Any]]:
    """Auto-detect the largest <table> and return its rows as dicts.

    Simple heuristic for the common case of "data lives in a table".
    Picks the table with the most rows.
    """
    tree = HTMLParser(html)
    tables = tree.css("table")
    if not tables:
        return []

    best_table = max(tables, key=lambda t: len(t.css("tr")))
    rows = best_table.css("tr")
    if len(rows) < 2:
        return []

    header_cells = rows[0].css("th, td")
    headers = [
        cell.text(strip=True) or f"col_{i}"
        for i, cell in enumerate(header_cells)
    ]

    data: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.css("td")
        if not cells:
            continue
        values = [c.text(strip=True) for c in cells]
        data.append(dict(zip(headers, values)))

    return data