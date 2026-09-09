"""Pre-training data-quality/leakage audit (Phase 6 Part D).

`run_audit()` returns a structured `AuditReport` - never a silent pass.
Training (`trainer.py`) MUST refuse to proceed when
`report.passed is False` (i.e. any `CRITICAL` issue was found) - this module
only detects and reports; `trainer.py` is what actually enforces the refusal,
so the policy lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import features as ml_features
from . import labels as ml_labels

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"


@dataclass(frozen=True)
class AuditIssue:
    check: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditReport:
    issues: list[AuditIssue]

    @property
    def passed(self) -> bool:
        """False if ANY critical issue was found - training must refuse to
        proceed in that case (Part D: "Do not silently drop serious
        data-quality problems")."""
        return not self.critical_issues

    @property
    def critical_issues(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_CRITICAL]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    def summary(self) -> str:
        if not self.issues:
            return "Audit clean: no issues found."
        lines = [f"Audit found {len(self.critical_issues)} critical, {len(self.warnings)} warning issue(s):"]
        for issue in self.issues:
            lines.append(f"  [{issue.severity}] {issue.check}: {issue.message}")
        return "\n".join(lines)


def _check_missingness(df: pd.DataFrame, feature_names: list[str], threshold_pct: float) -> list[AuditIssue]:
    issues = []
    for col in feature_names:
        if col not in df.columns:
            continue
        pct = float(df[col].isna().mean() * 100.0)
        if pct >= threshold_pct:
            issues.append(
                AuditIssue(
                    "missingness", SEVERITY_WARNING,
                    f"'{col}' is {pct:.1f}% missing (>= {threshold_pct:.0f}% threshold).",
                    {"column": col, "missing_pct": pct},
                )
            )
    return issues


def _check_constant_columns(df: pd.DataFrame, feature_names: list[str]) -> list[AuditIssue]:
    issues = []
    for col in feature_names:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if len(series) > 1 and series.nunique() <= 1:
            issues.append(
                AuditIssue(
                    "constant_column", SEVERITY_WARNING,
                    f"'{col}' has zero variance (constant) - carries no information for a model.",
                    {"column": col},
                )
            )
    return issues


def _check_duplicate_rows(df: pd.DataFrame) -> list[AuditIssue]:
    if not {"timestamp", "ticker"}.issubset(df.columns):
        return []
    dup_count = int(df.duplicated(subset=["timestamp", "ticker"]).sum())
    if dup_count > 0:
        return [
            AuditIssue(
                "duplicate_rows", SEVERITY_CRITICAL,
                f"{dup_count} duplicate (timestamp, ticker) row(s) found - each should be unique.",
                {"duplicate_count": dup_count},
            )
        ]
    return []


def _check_timestamp_ordering(df: pd.DataFrame) -> list[AuditIssue]:
    """Chronological order per ticker is required for every downstream split
    to be honest - an out-of-order dataset can silently put "future" rows
    ahead of "past" rows within a ticker."""
    if not {"timestamp", "ticker"}.issubset(df.columns):
        return []
    issues = []
    for ticker, group in df.groupby("ticker"):
        ts = pd.to_datetime(group["timestamp"])
        if not ts.is_monotonic_increasing:
            issues.append(
                AuditIssue(
                    "timestamp_ordering", SEVERITY_CRITICAL,
                    f"Rows for '{ticker}' are not in chronological order.",
                    {"ticker": ticker},
                )
            )
    return issues


def _check_target_availability(df: pd.DataFrame, horizon: int) -> list[AuditIssue]:
    col = ml_labels.regression_target_column(horizon)
    if col not in df.columns:
        return [AuditIssue("target_availability", SEVERITY_CRITICAL, f"Label column '{col}' is missing entirely.", {"column": col})]
    available_pct = float(df[col].notna().mean() * 100.0)
    if available_pct == 0.0:
        return [AuditIssue("target_availability", SEVERITY_CRITICAL, f"'{col}' has no known labels at all - nothing to train on.", {"column": col})]
    if available_pct < 50.0:
        return [
            AuditIssue(
                "target_availability", SEVERITY_WARNING,
                f"Only {available_pct:.1f}% of rows have a known '{col}' label.",
                {"column": col, "available_pct": available_pct},
            )
        ]
    return []


def _check_feature_leakage(df: pd.DataFrame, feature_names: list[str], horizon: int, corr_threshold: float = 0.999) -> list[AuditIssue]:
    """Two complementary leakage checks: (1) a feature literally named like a
    label column (a copy/paste or wiring mistake), and (2) a feature whose
    correlation with the target is suspiciously close to +-1.0, which is
    what a target-derived ("cheating") feature looks like even if its name
    doesn't give it away. Neither check is a substitute for the SMC/point-in-
    time causal tests - it's a coarse net for anything that slips past those."""
    issues = []
    label_cols = {ml_labels.regression_target_column(h) for h in ml_labels.HORIZONS}
    for col in feature_names:
        if col in label_cols:
            issues.append(
                AuditIssue("feature_leakage", SEVERITY_CRITICAL, f"Feature list includes a label column: '{col}'.", {"column": col})
            )

    target_col = ml_labels.regression_target_column(horizon)
    if target_col in df.columns:
        target = pd.to_numeric(df[target_col], errors="coerce")
        for col in feature_names:
            if col not in df.columns or col in label_cols:
                continue
            feat = pd.to_numeric(df[col], errors="coerce")
            valid = target.notna() & feat.notna()
            if valid.sum() < 10:
                continue
            corr = feat[valid].corr(target[valid])
            if corr == corr and abs(corr) >= corr_threshold:
                issues.append(
                    AuditIssue(
                        "feature_leakage", SEVERITY_CRITICAL,
                        f"Feature '{col}' correlates {corr:.4f} with '{target_col}' - suspected target leakage.",
                        {"column": col, "correlation": float(corr)},
                    )
                )
    return issues


def _check_extreme_values(df: pd.DataFrame, feature_names: list[str], z_threshold: float = 8.0) -> list[AuditIssue]:
    """Skipped for binary-like columns (strategy-active flags, e.g.) - a
    rare 1 among mostly 0s is an imbalance fact, not an "extreme value" in
    the continuous-outlier sense this check is meant to catch."""
    issues = []
    for col in feature_names:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) < 30 or series.nunique() <= 2:
            continue
        std = series.std()
        if not std or std == 0:
            continue
        z = (series - series.mean()).abs() / std
        extreme_count = int((z >= z_threshold).sum())
        if extreme_count > 0:
            issues.append(
                AuditIssue(
                    "extreme_values", SEVERITY_WARNING,
                    f"'{col}' has {extreme_count} value(s) beyond {z_threshold} std devs from its mean.",
                    {"column": col, "extreme_count": extreme_count},
                )
            )
    return issues


def _check_insufficient_history(df: pd.DataFrame, min_rows: int) -> list[AuditIssue]:
    if len(df) < min_rows:
        return [
            AuditIssue(
                "insufficient_history", SEVERITY_CRITICAL,
                f"Only {len(df)} row(s) available, below the minimum {min_rows} required to train/validate/test.",
                {"row_count": len(df), "min_rows": min_rows},
            )
        ]
    return []


def _check_class_imbalance(df: pd.DataFrame, horizon: int, config: dict[str, Any] | None, min_minority_pct: float = 5.0) -> list[AuditIssue]:
    if ml_labels.regression_target_column(horizon) not in df.columns:
        return []  # already reported as a critical issue by _check_target_availability
    target = ml_labels.build_classification_target(df, horizon, config).dropna()
    if target.empty:
        return []
    positive_pct = float(target.mean() * 100.0)
    minority_pct = min(positive_pct, 100.0 - positive_pct)
    if minority_pct < min_minority_pct:
        return [
            AuditIssue(
                "class_imbalance", SEVERITY_WARNING,
                f"Classification target is heavily imbalanced ({positive_pct:.1f}% positive).",
                {"positive_pct": positive_pct},
            )
        ]
    return []


def _check_ticker_imbalance(df: pd.DataFrame, max_ratio: float = 20.0) -> list[AuditIssue]:
    if "ticker" not in df.columns or df["ticker"].nunique() <= 1:
        return []
    counts = df["ticker"].value_counts()
    ratio = float(counts.max() / max(counts.min(), 1))
    if ratio > max_ratio:
        return [
            AuditIssue(
                "ticker_imbalance", SEVERITY_WARNING,
                f"Row counts across tickers are highly imbalanced (max/min ratio {ratio:.1f}x).",
                {"ratio": ratio, "counts": counts.to_dict()},
            )
        ]
    return []


def run_audit(
    df: pd.DataFrame,
    horizon: int,
    config: dict[str, Any] | None = None,
    feature_names: list[str] | None = None,
    min_rows: int = 100,
    missingness_threshold_pct: float = 50.0,
) -> AuditReport:
    """Run every check and return one structured report. Never raises on a
    data-quality problem itself - it collects issues; `trainer.py` is what
    refuses to proceed on `report.passed is False`."""
    names = feature_names or ml_features.FEATURE_WHITELIST
    issues: list[AuditIssue] = []
    issues.extend(_check_insufficient_history(df, min_rows))
    issues.extend(_check_duplicate_rows(df))
    issues.extend(_check_timestamp_ordering(df))
    issues.extend(_check_target_availability(df, horizon))
    issues.extend(_check_feature_leakage(df, names, horizon))
    issues.extend(_check_missingness(df, names, missingness_threshold_pct))
    issues.extend(_check_constant_columns(df, names))
    issues.extend(_check_extreme_values(df, names))
    issues.extend(_check_class_imbalance(df, horizon, config))
    issues.extend(_check_ticker_imbalance(df))
    return AuditReport(issues=issues)
