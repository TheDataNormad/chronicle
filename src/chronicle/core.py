"""Core orchestrator for Chronicle.

The Scrape class ties together:
    fetch + parse + normalize + profile + storage + drift

Every run is versioned, profiled, and compared to a baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from pydantic import BaseModel

from chronicle.clean import CleaningReport
from chronicle.diff import DiffReport, compute_diff, normalize_key
from chronicle.drift import DriftFinding, DriftReport, detect_drift
from chronicle.exceptions import ConfigurationError, ScrapeError, StorageError
from chronicle.fetch import Fetcher, FetchResponse
from chronicle.jsonapi import looks_like_json, parse_json
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
    normalize_result: NormalizeResult | None = None
    errors: list[str] = field(default_factory=list)
    drift_report: DriftReport | None = None
    changes: DiffReport | None = None
    previous_run_id: str | None = None
    manifest: dict[str, Any] | None = None
    baseline_used: str | None = None
    is_first_run: bool = False
    source_kind: str = "html"  # "html" or "json"

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

    @property
    def cleaning(self) -> CleaningReport:
        """What cleaning converted, flagged, or could not convert."""
        if self.normalize_result is None:
            return CleaningReport()
        return self.normalize_result.cleaning

    def has_drift(self) -> bool:
        return bool(self.drift_report and self.drift_report.has_drift)

    def has_changes(self) -> bool:
        """True if data changed since the previous run."""
        return bool(self.changes and self.changes.has_changes)

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
            "source": self.source_kind,
            "cleaning_issues": len(self.cleaning.issues),
            "changes": (
                {
                    "added": self.changes.rows_added,
                    "removed": self.changes.rows_removed,
                    "changed": self.changes.rows_changed,
                    "complete": self.changes.complete,
                }
                if self.changes else None
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
        selectors: dict[str, str] | None = None,
        schema: type[BaseModel] | None = None,
        key: str | list[str] | None = None,
        dayfirst: bool | None = None,
        date_formats: dict[str, str] | None = None,
        pages: int = 1,
        rate_limit: float = 1.0,
        strict: bool = False,
        use_cache: bool = False,
        store: bool = True,
        detect_drift: bool = True,
        storage_root: str | None = None,
        use_playwright: bool = False,
        wait_for: str | None = None,
        wait_until: str = "domcontentloaded",
        cookies: list[dict] | None = None,
        records_path: str | None = None,
        fields: dict[str, str] | None = None,
        source: str = "auto",
        headers: dict[str, str] | None = None,
        respect_robots: bool = True,
        page_param: str = "page",
    ) -> None:
        # Fail fast: validate selectors before any network call.
        if selectors is not None:
            if "_container" not in selectors:
                raise ScrapeError(
                    "selectors dict must include a '_container' key pointing to "
                    "the CSS selector for the repeating row element."
                )
            if not selectors["_container"].strip():
                raise ScrapeError(
                    "selectors['_container'] cannot be empty."
                )

        if source not in ("auto", "html", "json"):
            raise ConfigurationError("source must be 'auto', 'html' or 'json'")
        if selectors is not None and (records_path or fields or source == "json"):
            raise ConfigurationError(
                "selectors are for HTML pages; records_path / fields are for "
                "JSON endpoints. Use one or the other."
            )
        if (records_path or fields) and source == "html":
            raise ConfigurationError("records_path / fields cannot be used with source='html'")
        if headers and use_playwright:
            raise ConfigurationError("headers are not supported with use_playwright=True")

        self.key = normalize_key(key)
        self.dayfirst = dayfirst
        self.date_formats = dict(date_formats or {})
        if self.key:
            if schema is not None:
                valid_cols = list(schema.model_fields)
            elif selectors is not None:
                valid_cols = [k for k in selectors if k != "_container"]
            elif fields:
                valid_cols = list(fields)
            else:
                valid_cols = None  # columns only known after the first fetch
            if valid_cols is not None:
                unknown = [k for k in self.key if k not in valid_cols]
                if unknown:
                    raise ConfigurationError(
                        f"key column(s) {unknown} are not in the scraped "
                        f"columns. Available: {valid_cols}"
                    )

        self.url = url
        self.selectors = selectors or {}
        self.schema = schema
        self.pages = pages
        self.rate_limit = rate_limit
        self.strict = strict
        self.use_cache = use_cache
        self.store = store
        self.detect_drift = detect_drift
        self.use_playwright = use_playwright
        self.records_path = records_path
        self.fields = dict(fields) if fields else None
        self.source = source
        self.respect_robots = respect_robots
        self.page_param = page_param

        if use_playwright:
            # Imported locally so non-Playwright users don't pay the import cost
            from chronicle.playwright_fetcher import PlaywrightFetcher
            self._fetcher = PlaywrightFetcher(
                rate_limit=rate_limit,
                use_cache=use_cache,
                wait_for=wait_for,
                wait_until=wait_until,
                cookies=cookies,
                respect_robots=respect_robots,
            )
        else:
            self._fetcher = Fetcher(
                rate_limit=rate_limit,
                use_cache=use_cache,
                respect_robots=respect_robots,
                headers=headers,
            )

        self._storage = Storage(root=storage_root) if store else None

    # ---------- public API ----------

    def run(self) -> ScrapeResult:
        result = ScrapeResult(url=self.url, pages_requested=self.pages)
        all_rows: list[dict[str, Any]] = []
        raw_pages: list[str] = []
        kind: str | None = None

        try:
            for page in range(1, self.pages + 1):
                page_url = self._page_url(page)
                try:
                    response = self._fetch(page_url)
                except Exception as e:
                    result.pages_failed += 1
                    result.errors.append(f"page {page}: {type(e).__name__}: {e}")
                    continue

                # A page that fetches but cannot be parsed is a FAILED page,
                # reported like any other (it must not abort the whole run).
                try:
                    page_kind = self._kind_of(response)
                    if kind is None:
                        kind = page_kind
                    elif page_kind != kind:
                        raise ScrapeError(
                            f"page {page} returned {page_kind.upper()} but "
                            f"earlier pages returned {kind.upper()}"
                        )
                    rows = (
                        self._parse_json(response.text)
                        if kind == "json"
                        else self._parse(response.text)
                    )
                except ConfigurationError:
                    raise  # a mistake in how Scrape was set up: fail loudly
                except Exception as e:
                    result.pages_failed += 1
                    result.errors.append(f"page {page}: {type(e).__name__}: {e}")
                    continue

                result.pages_succeeded += 1
                raw_pages.append(response.text)
                all_rows.extend(rows)

                # An API that has run out of pages returns an empty list:
                # stop there (that is the end of the data, not a failure).
                if kind == "json" and page > 1 and not rows:
                    result.pages_requested = page
                    break
        finally:
            self._fetcher.close()

        result.source_kind = kind or "html"
        result.rows_scraped = len(all_rows)
        result.normalize_result = normalize_rows(
            all_rows,
            schema=self.schema,
            strict=self.strict,
            dayfirst=self.dayfirst,
            date_formats=self.date_formats,
            typed_source=(result.source_kind == "json"),
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

        # Load the previous run BEFORE saving this one, then diff.
        try:
            previous = self._storage.previous_run(project_id)
        except StorageError as e:
            previous = None
            result.errors.append(f"changes since last run are unavailable: {e}")
        diff_report = self._compute_changes(previous, df, result)
        result.changes = diff_report
        result.previous_run_id = previous["run_id"] if previous else None

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
                    "records_path": self.records_path,
                    "fields": self.fields,
                    "source": self.source,
                    "key": self.key,
                    "dayfirst": self.dayfirst,
                    "date_formats": self.date_formats,
                    "pages": self.pages,
                    "page_param": self.page_param,
                    "rate_limit": self.rate_limit,
                    "respect_robots": self.respect_robots,
                    "schema": self.schema.__name__ if self.schema else None,
                    "use_playwright": self.use_playwright,
                },
                raw_ext="json" if result.source_kind == "json" else "html",
                pages_requested=result.pages_requested,
                pages_succeeded=result.pages_succeeded,
                pages_failed=result.pages_failed,
                errors=result.errors,
                diff=diff_report.to_dict() if diff_report else None,
                cleaning=result.cleaning.to_dict(),
                previous_run_id=result.previous_run_id,
            )
            result.manifest = manifest.to_dict()
        except Exception as e:
            result.errors.append(f"storage: {type(e).__name__}: {e}")
            return

        if baseline is None:
            if profile is not None:
                self._storage.save_baseline(project_id, profile)
            result.baseline_used = "self (first run)"
        elif self.detect_drift:
            result.baseline_used = baseline.get("_run_id", "baseline")
            result.drift_report = self._compute_drift(baseline, profile, df)

    def _compute_changes(
        self,
        previous: dict[str, Any] | None,
        df: pd.DataFrame,
        result: ScrapeResult,
    ) -> DiffReport | None:
        """Diff this run against the previous one (None on the first run)."""
        if previous is None:
            return None
        prev_manifest = previous.get("manifest", {})
        warnings: list[str] = []
        complete = result.pages_failed == 0
        if prev_manifest.get("pages_failed", 0) > 0:
            complete = False
        return compute_diff(
            previous["data"],
            df,
            key=self.key,
            previous_run_id=previous["run_id"],
            previous_timestamp=prev_manifest.get("timestamp"),
            complete=complete,
            extra_warnings=warnings,
        )

    def _compute_drift(
        self,
        baseline: dict[str, Any],
        profile: dict[str, Any] | None,
        df: pd.DataFrame,
    ) -> DriftReport | None:
        """Compare the current run to the baseline.

        Handles the zero-row case explicitly. If the baseline had N>0
        rows and the current run has 0 rows, that is SEVERE drift — most
        likely a broken selector — and must not be silent.
        """
        baseline_rows = int(baseline.get("row_count", 0))
        current_rows = len(df)

        if current_rows == 0 and baseline_rows > 0:
            return DriftReport(findings=[
                DriftFinding(
                    column="<all>",
                    kind="row_count",
                    severity="SEVERE",
                    message=(
                        f"Scrape returned 0 rows after a baseline of "
                        f"{baseline_rows} rows. Selector likely broken "
                        f"or page structure changed."
                    ),
                    baseline_value=baseline_rows,
                    current_value=0,
                )
            ])

        if profile is None:
            return None

        return detect_drift(baseline, profile)

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
        """URL for page N: page 1 is the URL as given; later pages set the
        page query parameter (replacing any existing one, keeping fragments)."""
        if page == 1:
            return self.url
        parts = urlsplit(self.url)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k != self.page_param
        ]
        query.append((self.page_param, str(page)))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _fetch(self, url: str) -> FetchResponse:
        fetch = getattr(self._fetcher, "fetch", None)
        if fetch is not None:
            return fetch(url)
        return FetchResponse(text=self._fetcher.get(url), url=url)

    def _kind_of(self, response: FetchResponse) -> str:
        """Decide whether a response is JSON or HTML, without guessing wildly."""
        if self.source in ("html", "json"):
            kind = self.source
        elif response.is_json or (
            looks_like_json(response.text) and (self.records_path or self.fields)
        ):
            kind = "json"
        else:
            kind = "html"
        if kind == "json" and self.selectors:
            raise ConfigurationError(
                "the response is JSON, so CSS selectors do not apply; use "
                "records_path= / fields= instead of selectors="
            )
        if kind == "html" and (self.records_path or self.fields):
            raise ConfigurationError(
                "records_path / fields were given but the response is HTML, not JSON"
            )
        return kind

    def _parse_json(self, text: str) -> list[dict[str, Any]]:
        return parse_json(text, records_path=self.records_path, fields=self.fields)

    def close(self) -> None:
        self._fetcher.close()

    def __enter__(self) -> Scrape:
        return self

    def __exit__(self, *args) -> None:
        self.close()