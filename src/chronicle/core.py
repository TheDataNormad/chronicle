"""Core orchestrator for Chronicle.

The Scrape class ties together fetch + parse + normalize into one
clean, DS-native API.

Usage:
    from pydantic import BaseModel
    from chronicle import Scrape

    class Product(BaseModel):
        name: str
        price: float
        rating: float | None = None

    result = Scrape(
        url="https://example.com/products",
        selectors={"_container": "div.product", "name": "h2", "price": "span.price"},
        schema=Product,
        pages=3,
        rate_limit=2.0,
    ).run()

    df = result.to_dataframe()
    print(result.quality_report())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel

from chronicle.exceptions import ScrapeError
from chronicle.fetch import Fetcher
from chronicle.normalize import NormalizeResult, normalize_rows
from chronicle.parse import detect_tables, parse_html


@dataclass
class ScrapeResult:
    """Result of running a Scrape.

    Bundles the normalized data, invalid rows, quality report, and
    metadata about what happened during the run.
    """

    url: str
    pages_requested: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0
    rows_scraped: int = 0
    normalize_result: Optional[NormalizeResult] = None
    errors: list[str] = field(default_factory=list)

    # ---------- data access ----------

    def to_dataframe(self) -> pd.DataFrame:
        """Return the valid rows as a pandas DataFrame."""
        if self.normalize_result is None:
            return pd.DataFrame()
        return self.normalize_result.to_dataframe()

    def to_invalid_dataframe(self) -> pd.DataFrame:
        """Return rows that failed schema validation."""
        if self.normalize_result is None:
            return pd.DataFrame()
        return self.normalize_result.to_invalid_dataframe()

    @property
    def invalid_rows(self) -> list[dict[str, Any]]:
        if self.normalize_result is None:
            return []
        return self.normalize_result.invalid_rows

    # ---------- reports ----------

    def quality_report(self) -> dict[str, Any]:
        """Return the data quality report."""
        if self.normalize_result is None:
            return {}
        return self.normalize_result.quality_report()

    def summary(self) -> dict[str, Any]:
        """Return a summary of the scrape run."""
        return {
            "url": self.url,
            "pages_requested": self.pages_requested,
            "pages_succeeded": self.pages_succeeded,
            "pages_failed": self.pages_failed,
            "rows_scraped": self.rows_scraped,
            "rows_valid": (
                len(self.normalize_result.valid_rows)
                if self.normalize_result else 0
            ),
            "rows_invalid": (
                len(self.normalize_result.invalid_rows)
                if self.normalize_result else 0
            ),
            "success_rate": (
                round(self.normalize_result.success_rate, 4)
                if self.normalize_result else 0.0
            ),
            "errors": self.errors,
        }

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"<ScrapeResult url={s['url']!r} "
            f"rows={s['rows_scraped']} "
            f"valid={s['rows_valid']} "
            f"invalid={s['rows_invalid']} "
            f"pages={s['pages_succeeded']}/{s['pages_requested']}>"
        )


class Scrape:
    """The main entry point for Chronicle.

    Examples:
        Simple — auto-detect a table:
            result = Scrape("https://en.wikipedia.org/wiki/...").run()

        Selector-based with schema:
            result = Scrape(
                url="https://example.com/products",
                selectors={
                    "_container": "div.product",
                    "name": "h2",
                    "price": "span.price",
                },
                schema=Product,
                pages=3,
            ).run()
    """

    def __init__(
        self,
        url: str,
        selectors: Optional[dict[str, str]] = None,
        schema: Optional[type[BaseModel]] = None,
        pages: int = 1,
        rate_limit: float = 1.0,
        strict: bool = False,
        use_cache: bool = True,
    ) -> None:
        self.url = url
        self.selectors = selectors or {}
        self.schema = schema
        self.pages = pages
        self.rate_limit = rate_limit
        self.strict = strict
        self.use_cache = use_cache
        self._fetcher = Fetcher(rate_limit=rate_limit, use_cache=use_cache)

    # ---------- public API ----------

    def run(self) -> ScrapeResult:
        """Execute the scrape and return a ScrapeResult."""
        result = ScrapeResult(url=self.url, pages_requested=self.pages)
        all_rows: list[dict[str, Any]] = []

        for page in range(1, self.pages + 1):
            page_url = self._page_url(page)
            try:
                html = self._fetcher.get(page_url)
                result.pages_succeeded += 1
            except ScrapeError as e:
                result.pages_failed += 1
                result.errors.append(f"page {page}: {e}")
                continue
            except Exception as e:
                result.pages_failed += 1
                result.errors.append(f"page {page}: {type(e).__name__}: {e}")
                continue

            rows = self._parse(html)
            all_rows.extend(rows)

        result.rows_scraped = len(all_rows)
        result.normalize_result = normalize_rows(
            all_rows,
            schema=self.schema,
            strict=self.strict,
        )
        return result

    def to_dataframe(self) -> pd.DataFrame:
        """Convenience: run and return the DataFrame directly."""
        return self.run().to_dataframe()

    # ---------- internals ----------

    def _parse(self, html: str) -> list[dict[str, Any]]:
        if self.selectors:
            container = self.selectors.get("_container")
            if not container:
                raise ScrapeError(
                    "selectors dict must include '_container' key pointing to the "
                    "CSS selector for the repeating row element."
                )
            field_selectors = {
                k: v for k, v in self.selectors.items() if k != "_container"
            }
            return parse_html(
                html,
                container=container,
                selectors=field_selectors,
                base_url=self.url,
            )
        # No selectors → auto-detect table
        return detect_tables(html)

    def _page_url(self, page: int) -> str:
        """Build the URL for a given page number.

        Simple heuristic: page 1 is the base URL, subsequent pages append
        ?page=N or &page=N. Users with custom pagination needs can override.
        """
        if page == 1:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}page={page}"

    def close(self) -> None:
        self._fetcher.close()

    def __enter__(self) -> "Scrape":
        return self

    def __exit__(self, *args) -> None:
        self.close()