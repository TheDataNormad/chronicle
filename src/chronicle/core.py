"""Core orchestrator for Chronicle.

The Scrape class ties together:
    fetch + parse + normalize + profile + storage + drift

Every run is versioned, profiled, and compared to a baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel

from chronicle.drift import DriftReport, detect_drift
from chronicle.exceptions import ScrapeError
from chronicle.fetch import Fetcher
from chronicle.normalize import NormalizeResult, normalize_rows
from chronicle.parse import detect_tables, parse_html
from chronicle.profile import generate_profile
from chronicle.storage import Storage


@dataclass
class ScrapeResult:
    """Result of running a Scrape."""

    url: str
    pages_requested: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0
    rows_scraped: int = 0
    normalize_result: Optional[NormalizeResult] = None
    errors: list[str] = field(default_factory=list)
    drift_report: Optional[DriftReport] = None
    manifest: Optional[dict[str, Any]] = None
    baseline_used: Optional[str] = None
    is_first_run: bool = False

    # ---------- data access ----------

    def to_dataframe(self) -> pd.DataFrame:
        if self.normalize_result is None:
            return pd.DataFrame()
        return self.normalize_result.to_dataframe()

    def to_invalid_dataframe(self) -> pd.DataFrame:
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
        if self.normalize_result is None:
            return {}
        return self.normalize_result.quality_report()

    def has_drift(self) -> bool:
        return bool(self.drift_report and self.drift_report.has_drift)

    def summary(self) -> dict[str, Any]:
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
            "is_first_run": self.is_first_run,
            "drift_severity": (
                self.drift_report.severity if self.drift_report else None
            ),
        }

    def __repr__(self) -> str:
        s = self.summary()
        drift = f" drift={s['drift_severity']}" if s["drift_severity"] else ""
        return (
            f"<ScrapeResult rows={s['rows_scraped']} "
            f"valid={s['rows_valid']} "
            f"invalid={s['rows_invalid']} "
            f"pages={s['pages_succeeded']}/{s['pages_requested']}"
            f"{drift}>"
        )


class Scrape:
    """The main entry point for Chronicle."""

    def __init__(
        self,
        url: str,
        selectors: Optional[dict[str, str]] = None,
        schema: Optional[type[BaseModel]] = None,
        pages: int = 1,
        rate_limit: float = 1.0,
        strict: bool = False,
        use_cache: bool = True,
        store: bool = True,
        detect_drift: bool = True,
        storage_root: Optional[str] = None,
    ) -> None:
        self.url = url
        self.selectors = selectors or {}
        self.schema = schema
        self.pages = pages
        self.rate_limit = rate_limit
        self.strict = strict
        self.use_cache = use_cache
        self.store = store
        self.detect_drift = detect_drift
        self._fetcher = Fetcher(rate_limit=rate_limit, use_cache=use_cache)
        self._storage = Storage(root=storage_root) if store else None

    # ---------- public API ----------

    def run(self) -> ScrapeResult:
        result = ScrapeResult(url=self.url, pages_requested=self.pages)
        all_rows: list[dict[str, Any]] = []
        raw_pages: list[str] = []

        for page in range(1, self.pages + 1):
            page_url = self._page_url(page)
            try:
                html = self._fetcher.get(page_url)
                result.pages_succeeded += 1
                raw_pages.append(html)
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

        if self.store and self._storage is not None:
            self._post_process(result, raw_pages)

        return result

    def to_dataframe(self) -> pd.DataFrame:
        return self.run().to_dataframe()

    # ---------- internals ----------

    def _post_process(
        self, result: ScrapeResult, raw_pages: list[str]
    ) -> None:
        df = result.to_dataframe()
        invalid_df = result.to_invalid_dataframe()
        project_id = self._storage.project_id_for(self.url)

        profile = generate_profile(df) if not df.empty else None

        baseline = self._storage.load_baseline(project_id)
        result.is_first_run = baseline is None

        try:
            manifest = self._storage.save_run(
                url=self.url,
                data=df,
                invalid=invalid_df if not invalid_df.empty else None,
                profile=profile,
                raw_pages=raw_pages,
                config={
                    "selectors": self.selectors,
                    "pages": self.pages,
                    "schema": self.schema.__name__ if self.schema else None,
                },
                pages_requested=result.pages_requested,
                pages_succeeded=result.pages_succeeded,
                pages_failed=result.pages_failed,
                errors=result.errors,
            )
            result.manifest = manifest.to_dict()
        except Exception as e:
            result.errors.append(f"storage: {type(e).__name__}: {e}")
            return

        if baseline is None:
            if profile is not None:
                self._storage.save_baseline(project_id, profile)
            result.baseline_used = "self (first run)"
        elif self.detect_drift and profile is not None:
            result.baseline_used = baseline.get("_run_id", "baseline")
            result.drift_report = detect_drift(baseline, profile)

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
        return detect_tables(html)

    def _page_url(self, page: int) -> str:
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