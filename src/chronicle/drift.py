"""Drift detection for Chronicle.

Compares a current profile against a baseline profile and reports
schema, null, distribution, and range drift.

Severity levels:
    NORMAL  — no meaningful change
    WARNING — noticeable but not catastrophic change
    SEVERE  — likely a broken selector or site change
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy import stats


# ---------------------------------------------------------------- thresholds

NULL_DRIFT_WARN = 0.10        # +10pp null% change = warning
NULL_DRIFT_SEVERE = 0.50      # +50pp null% change = severe
MEAN_DRIFT_WARN = 0.30        # ±30% mean shift = warning
MEAN_DRIFT_SEVERE = 0.80      # ±80% mean shift = severe
UNIQUE_DRIFT_WARN = 0.30      # ±30% unique count change = warning
KS_PVALUE_THRESHOLD = 0.05    # statistical significance for KS test


# ---------------------------------------------------------------- types


@dataclass
class DriftFinding:
    """A single drift finding for one column or schema-level check."""

    column: str
    kind: str                  # "schema" | "null" | "distribution" | "range"
    severity: str              # "NORMAL" | "WARNING" | "SEVERE"
    message: str
    baseline_value: Any = None
    current_value: Any = None
    delta: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "delta": self.delta,
        }


@dataclass
class DriftReport:
    """Full drift report for a run."""

    findings: list[DriftFinding] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(f.severity in ("WARNING", "SEVERE") for f in self.findings)

    @property
    def severity(self) -> str:
        severities = [f.severity for f in self.findings]
        if "SEVERE" in severities:
            return "SEVERE"
        if "WARNING" in severities:
            return "WARNING"
        return "NORMAL"

    @property
    def summary(self) -> dict[str, int]:
        counts = {"NORMAL": 0, "WARNING": 0, "SEVERE": 0}
        for f in self.findings:
            counts[f.severity] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "severity": self.severity,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_dataframe(self):
        import pandas as pd
        if not self.findings:
            return pd.DataFrame(
                columns=["column", "kind", "severity", "message"]
            )
        return pd.DataFrame([f.to_dict() for f in self.findings])

    def __repr__(self) -> str:
        s = self.summary
        return (
            f"<DriftReport severity={self.severity} "
            f"warnings={s['WARNING']} severe={s['SEVERE']}>"
        )

    def pretty(self) -> str:
        """Human-readable text report."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"DRIFT REPORT   severity: {self.severity}")
        lines.append("=" * 70)
        if not self.findings:
            lines.append("  No drift detected.")
            return "\n".join(lines)

        for f in sorted(
            self.findings,
            key=lambda x: {"SEVERE": 0, "WARNING": 1, "NORMAL": 2}[x.severity],
        ):
            icon = {"SEVERE": "🔴", "WARNING": "⚠️ ", "NORMAL": "✓ "}[f.severity]
            lines.append(f"\n{icon} [{f.severity}] {f.column} ({f.kind})")
            lines.append(f"    {f.message}")
            if f.baseline_value is not None and f.current_value is not None:
                lines.append(
                    f"    baseline: {f.baseline_value}  →  current: {f.current_value}"
                )
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------- engine


def detect_drift(
    baseline: dict[str, Any],
    current: dict[str, Any],
    null_warn: float = NULL_DRIFT_WARN,
    null_severe: float = NULL_DRIFT_SEVERE,
    mean_warn: float = MEAN_DRIFT_WARN,
    mean_severe: float = MEAN_DRIFT_SEVERE,
) -> DriftReport:
    """Compare two profiles and return a DriftReport."""
    report = DriftReport()

    base_cols = set(baseline.get("columns", {}).keys())
    curr_cols = set(current.get("columns", {}).keys())

    # ----- schema drift -----
    added = curr_cols - base_cols
    removed = base_cols - curr_cols

    for col in sorted(added):
        report.findings.append(DriftFinding(
            column=col,
            kind="schema",
            severity="WARNING",
            message=f"New column '{col}' appeared",
            current_value="present",
        ))
    for col in sorted(removed):
        report.findings.append(DriftFinding(
            column=col,
            kind="schema",
            severity="SEVERE",
            message=f"Column '{col}' disappeared",
            baseline_value="present",
        ))

    # ----- per-column drift -----
    for col in sorted(base_cols & curr_cols):
        b = baseline["columns"][col]
        c = current["columns"][col]

        # Null drift
        b_null = b.get("null_pct", 0.0)
        c_null = c.get("null_pct", 0.0)
        null_delta = c_null - b_null
        if abs(null_delta) >= null_severe:
            report.findings.append(DriftFinding(
                column=col,
                kind="null",
                severity="SEVERE",
                message=(
                    f"Null % jumped from {b_null:.2%} to {c_null:.2%} — "
                    f"selector likely broken"
                ),
                baseline_value=b_null,
                current_value=c_null,
                delta=null_delta,
            ))
        elif abs(null_delta) >= null_warn:
            report.findings.append(DriftFinding(
                column=col,
                kind="null",
                severity="WARNING",
                message=(
                    f"Null % changed from {b_null:.2%} to {c_null:.2%}"
                ),
                baseline_value=b_null,
                current_value=c_null,
                delta=null_delta,
            ))

        # Distribution drift (numeric only)
        if "mean" in b and "mean" in c:
            b_mean = b.get("mean") or 0.0
            c_mean = c.get("mean") or 0.0
            if b_mean != 0:
                pct_change = abs(c_mean - b_mean) / abs(b_mean)
                if pct_change >= mean_severe:
                    report.findings.append(DriftFinding(
                        column=col,
                        kind="distribution",
                        severity="SEVERE",
                        message=(
                            f"Mean shifted {pct_change:.0%} "
                            f"({b_mean:.2f} → {c_mean:.2f})"
                        ),
                        baseline_value=b_mean,
                        current_value=c_mean,
                        delta=c_mean - b_mean,
                    ))
                elif pct_change >= mean_warn:
                    report.findings.append(DriftFinding(
                        column=col,
                        kind="distribution",
                        severity="WARNING",
                        message=(
                            f"Mean shifted {pct_change:.0%} "
                            f"({b_mean:.2f} → {c_mean:.2f})"
                        ),
                        baseline_value=b_mean,
                        current_value=c_mean,
                        delta=c_mean - b_mean,
                    ))

        # Range drift
        if "min" in b and "min" in c and "max" in b and "max" in c:
            b_min, b_max = b["min"], b["max"]
            c_min, c_max = c["min"], c["max"]
            if b_min is not None and c_min is not None:
                if c_min < b_min * 0.5 and b_min > 0:
                    report.findings.append(DriftFinding(
                        column=col,
                        kind="range",
                        severity="WARNING",
                        message=(
                            f"Min dropped below half of baseline "
                            f"({b_min} → {c_min})"
                        ),
                        baseline_value=b_min,
                        current_value=c_min,
                    ))
                if c_max > b_max * 2 and b_max > 0:
                    report.findings.append(DriftFinding(
                        column=col,
                        kind="range",
                        severity="WARNING",
                        message=(
                            f"Max more than doubled "
                            f"({b_max} → {c_max})"
                        ),
                        baseline_value=b_max,
                        current_value=c_max,
                    ))

        # Selector health — was mostly populated, now mostly null
        if b.get("null_pct", 0) < 0.05 and c.get("null_pct", 0) > 0.95:
            report.findings.append(DriftFinding(
                column=col,
                kind="schema",
                severity="SEVERE",
                message=f"Column '{col}' is now almost entirely null",
                baseline_value=b.get("null_pct"),
                current_value=c.get("null_pct"),
            ))

    return report


# ---------------------------------------------------------------- ks test


def ks_drift(values_a: list[float], values_b: list[float]) -> dict[str, Any]:
    """Kolmogorov-Smirnov test between two numeric samples.

    Use this if you have raw values from both runs and want a
    statistical test of distribution equality.
    """
    a = np.asarray([v for v in values_a if v is not None and not np.isnan(v)])
    b = np.asarray([v for v in values_b if v is not None and not np.isnan(v)])
    if len(a) < 2 or len(b) < 2:
        return {"drift": False, "reason": "insufficient data"}
    stat, p = stats.ks_2samp(a, b)
    return {
        "statistic": float(stat),
        "p_value": float(p),
        "drift": bool(p < KS_PVALUE_THRESHOLD),
        "severity": (
            "SEVERE" if stat > 0.5
            else "WARNING" if stat > 0.2
            else "NORMAL"
        ),
    }