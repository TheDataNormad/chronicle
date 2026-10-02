"""Storage layer for Chronicle.

Gives Chronicle its memory: every scrape run is versioned, stored, and
indexed so it can be queried, replayed, and compared across time.

Layout:
    <root>/
    ├── index.db                    # SQLite index
    └── projects/<project_id>/
        ├── baseline.json
        └── runs/<run_id>/
            ├── manifest.json
            ├── profile.json
            ├── data.parquet
            ├── invalid.parquet
            └── raw/page_N.html
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from chronicle.exceptions import StorageError

DEFAULT_ROOT = Path.cwd() / ".chronicle"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    url              TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    row_count        INTEGER,
    valid_count      INTEGER,
    invalid_count    INTEGER,
    pages_requested  INTEGER,
    pages_succeeded  INTEGER,
    pages_failed     INTEGER,
    artifact_path    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_runs_url ON runs(url);
"""


@dataclass
class RunManifest:
    """Metadata about a single scrape run."""

    run_id: str
    project_id: str
    url: str
    timestamp: str
    chronicle_version: str
    config: dict[str, Any] = field(default_factory=dict)
    pages_requested: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0
    row_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunManifest:
        return cls(**data)


class Storage:
    """Manages the .chronicle/ directory and run artifacts."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "projects").mkdir(exist_ok=True)
        self.db_path = self.root / "index.db"
        self._init_db()

    # ---------------------------------------------------------------- db

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as e:
            raise StorageError(f"Failed to initialize index: {e}") from e

    def _insert_run(self, manifest: RunManifest, artifact_path: Path) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs
                    (run_id, project_id, url, timestamp, row_count, valid_count,
                     invalid_count, pages_requested, pages_succeeded, pages_failed,
                     artifact_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.run_id,
                        manifest.project_id,
                        manifest.url,
                        manifest.timestamp,
                        manifest.row_count,
                        manifest.valid_count,
                        manifest.invalid_count,
                        manifest.pages_requested,
                        manifest.pages_succeeded,
                        manifest.pages_failed,
                        str(artifact_path),
                    ),
                )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to insert run: {e}") from e

    # ---------------------------------------------------------------- ids

    @staticmethod
    def project_id_for(url: str) -> str:
        """Stable project id derived from the URL host + path."""
        parsed = urlparse(url)
        slug = f"{parsed.netloc}{parsed.path}".strip("/")
        if not slug:
            slug = "root"
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in slug)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        return f"{safe[:60]}-{digest}"

    @staticmethod
    def new_run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

    # ---------------------------------------------------------------- paths

    def project_dir(self, project_id: str) -> Path:
        path = self.root / "projects" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_dir(self, project_id: str, run_id: str) -> Path:
        path = self.project_dir(project_id) / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "raw").mkdir(exist_ok=True)
        return path

    def baseline_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "baseline.json"

    # ---------------------------------------------------------------- save

    def save_run(
        self,
        url: str,
        data: pd.DataFrame,
        invalid: pd.DataFrame | None = None,
        profile: dict[str, Any] | None = None,
        raw_pages: list[str] | None = None,
        config: dict[str, Any] | None = None,
        pages_requested: int = 0,
        pages_succeeded: int = 0,
        pages_failed: int = 0,
        errors: list[str] | None = None,
        chronicle_version: str = "0.1.0",
    ) -> RunManifest:
        """Persist a scrape run to disk and index it."""
        from chronicle import __version__ as _v  # local import to avoid cycle
        project_id = self.project_id_for(url)
        run_id = self.new_run_id()
        run_path = self.run_dir(project_id, run_id)

        # Data
        try:
            data.to_parquet(run_path / "data.parquet", index=False)
            if invalid is not None and not invalid.empty:
                invalid.to_parquet(run_path / "invalid.parquet", index=False)
        except Exception as e:
            raise StorageError(f"Failed to write parquet: {e}") from e

        # Raw pages
        if raw_pages:
            for i, html in enumerate(raw_pages, start=1):
                (run_path / "raw" / f"page_{i}.html").write_text(
                    html, encoding="utf-8"
                )

        # Profile
        if profile:
            (run_path / "profile.json").write_text(
                json.dumps(profile, indent=2, default=str),
                encoding="utf-8",
            )

        # Manifest
        manifest = RunManifest(
            run_id=run_id,
            project_id=project_id,
            url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            chronicle_version=_v if chronicle_version == "0.1.0" else chronicle_version,
            config=config or {},
            pages_requested=pages_requested,
            pages_succeeded=pages_succeeded,
            pages_failed=pages_failed,
            row_count=len(data),
            valid_count=len(data),
            invalid_count=len(invalid) if invalid is not None else 0,
            errors=errors or [],
        )
        (run_path / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

        # Index
        self._insert_run(manifest, run_path)

        return manifest

    # ---------------------------------------------------------------- load

    def load_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        run_path = self.project_dir(project_id) / "runs" / run_id
        if not run_path.exists():
            raise StorageError(f"Run not found: {run_id}")

        manifest = json.loads((run_path / "manifest.json").read_text())
        profile_path = run_path / "profile.json"
        profile = (
            json.loads(profile_path.read_text())
            if profile_path.exists() else None
        )
        data = (
            pd.read_parquet(run_path / "data.parquet")
            if (run_path / "data.parquet").exists() else pd.DataFrame()
        )
        return {"manifest": manifest, "profile": profile, "data": data, "path": run_path}

    def list_runs(
        self,
        url: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List runs, newest first."""
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if url:
            query += " AND url = ?"
            params.append(url)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        return rows

    def latest_run(self, url: str) -> dict[str, Any] | None:
        runs = self.list_runs(url=url, limit=1)
        return runs[0] if runs else None

    # ---------------------------------------------------------------- baseline

    def save_baseline(self, project_id: str, profile: dict[str, Any]) -> None:
        self.baseline_path(project_id).write_text(
            json.dumps(profile, indent=2, default=str),
            encoding="utf-8",
        )

    def load_baseline(self, project_id: str) -> dict[str, Any] | None:
        path = self.baseline_path(project_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())