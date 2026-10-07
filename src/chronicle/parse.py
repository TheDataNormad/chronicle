"""HTML parsing layer for Chronicle.

Turns raw HTML into structured rows (list of dicts).

Two modes:
1. Selector mode — you define CSS selectors for each field.
2. Table auto mode — detects the largest <table>.

Usage:
    from chronicle.parse import parse_html

    rows = parse_html(
        html,
        container="div.product",
        selectors={"name": "h2", "price": "span.price"},
    )
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

# selectolax 1.x deprecated the "modest" backend (selectolax.parser)
# in favor of lexbor. This try/except keeps Chronicle working on both.
try:
    # selectolax >= 1.0
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
except ImportError:
    # selectolax < 1.0
    from selectolax.parser import HTMLParser

from chronicle.exceptions import ParseError

# Matches footnote-style markers: [1], [a], [citation needed], etc.
# Used by detect_tables to clean cell text.
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")


def parse_html(
    html: str,
    container: str,
    selectors: dict[str, str],
    base_url: str | None = None,
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


def _extract_field(node, selector: str, base_url: str | None) -> Any:
    """Extract a single field from a node using a CSS selector.

    Special syntax:
        "a@href"    -> extract the href attribute of the <a> tag
        "img@src"   -> extract the src attribute
        "span@text" -> extract the text of the <span>
        "@text"     -> extract the node's own text
    """
    # Attribute OR text extraction: "selector@attr" or "selector@text"
    if "@" in selector:
        sel, _, attr = selector.partition("@")
        target = node.css_first(sel) if sel else node
        if target is None:
            return None
        # Special-case @text to mean "the node's text" rather than
        # "the attribute named 'text'". Before this fix, "@text" and
        # "span@text" silently returned None.
        if attr == "text":
            return target.text(strip=True)
        value = target.attributes.get(attr)
        if value and base_url and attr in ("href", "src"):
            value = urljoin(base_url, value)
        return value

    # Plain text extraction (no "@")
    target = node.css_first(selector)
    if target is None:
        return None
    return target.text(strip=True)


def detect_tables(html: str) -> list[dict[str, Any]]:
    """Auto-detect the largest <table> and return its rows as dicts.

    Simple heuristic for the common case of "data lives in a table".
    Picks the table with the most rows.

    Handles:
    - `<th scope="row">` row headers (e.g. Wikipedia's rank column) so
      columns don't shift when a table has both <th> and <td> cells
    - Footnote markers like `[1]`, `[a]`, `[citation needed]` are
      stripped from cell text so downstream type casting works
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
        _clean_cell_text(cell.text(strip=True)) or f"col_{i}"
        for i, cell in enumerate(header_cells)
    ]

    data: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.css("th, td")
        if not cells:
            continue
        values = [_clean_cell_text(c.text(strip=True)) for c in cells]
        data.append(dict(zip(headers, values)))

    return data


def _clean_cell_text(text: str) -> str:
    """Strip footnote markers from a table cell's text.

    Examples:
        'Baby Shark Dance[4]'      -> 'Baby Shark Dance'
        'China[a]'                 -> 'China'
        'Foo[citation needed]'     -> 'Foo'
    """
    if not text:
        return text
    return _FOOTNOTE_RE.sub("", text).strip()