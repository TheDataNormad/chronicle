"""Tests for chronicle.parse."""

from chronicle.parse import detect_tables, parse_html

SAMPLE_HTML = """
<html><body>
  <div class="product">
    <h2>Widget</h2>
    <span class="price">$10.99</span>
    <a href="/widget">link</a>
  </div>
  <div class="product">
    <h2>Gadget</h2>
    <span class="price">$20.50</span>
    <a href="/gadget">link</a>
  </div>
</body></html>
"""

TABLE_HTML = """
<html><body>
  <table>
    <tr><th>Name</th><th>Age</th></tr>
    <tr><td>Alice</td><td>30</td></tr>
    <tr><td>Bob</td><td>25</td></tr>
  </table>
</body></html>
"""


def test_parse_html_basic():
    rows = parse_html(
        SAMPLE_HTML,
        container="div.product",
        selectors={"name": "h2", "price": "span.price"},
    )
    assert len(rows) == 2
    assert rows[0]["name"] == "Widget"
    assert rows[0]["price"] == "$10.99"
    assert rows[1]["name"] == "Gadget"


def test_parse_html_attribute_extraction():
    rows = parse_html(
        SAMPLE_HTML,
        container="div.product",
        selectors={"name": "h2", "url": "a@href"},
    )
    assert rows[0]["url"] == "/widget"
    assert rows[1]["url"] == "/gadget"


def test_parse_html_resolves_relative_urls():
    rows = parse_html(
        SAMPLE_HTML,
        container="div.product",
        selectors={"url": "a@href"},
        base_url="https://example.com",
    )
    assert rows[0]["url"] == "https://example.com/widget"


def test_parse_html_empty_container_returns_empty():
    rows = parse_html(
        SAMPLE_HTML,
        container="div.nonexistent",
        selectors={"name": "h2"},
    )
    assert rows == []


def test_detect_tables_finds_largest():
    rows = detect_tables(TABLE_HTML)
    assert len(rows) == 2
    assert rows[0] == {"Name": "Alice", "Age": "30"}
    assert rows[1] == {"Name": "Bob", "Age": "25"}