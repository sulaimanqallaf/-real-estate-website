"""Model-drift monitoring (Phase 6 Part W): feature distribution drift,
prediction distribution drift, calibration degradation, recent realized hit
rate, and missing-data increase.

This module only ever REPORTS warnings - it never retrains, never swaps a
model, never changes `status` in the registry. A `PERFORMANCE_DRIFT` or
`CALIBRATION_DRIFT` warning is a prompt for a human (or the offline
`trainer.py` workflow, run deliberately) to look at retraining - see Part W:
"Drift should trigger review/retraining workflow," never an automatic
production change, and Part X: "Daily run only loads an explicitly
registered model."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

WARNING_FEATURE_DRIFT = "FEATURE_DRIFT"
WARNING_PERFORMANCE_DRIFT = "PERFORMANCE_DRIFT"
WARNING_CALIBRATION_DRIFT = "CALIBRATION_DRIFT"
WARNING_DATA_QUALITY_DRIFT = "DATA_QUALITY_DRIFT"


@dataclass(frozen=True)
class DriftReport:
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def has_drift(self) -> bool:
        return len(self.warnings) > 0


def detect_feature_drift(
    reference_df: pd.DataFrame, recent_df: pd.DataFrame, feature_names: list[str], z_threshold: float = 1.5
) -> tuple[bool, dict[str, Any]]:
    """Compares each feature's RECENT mean against its TRAINING (reference)
    mean/std - a shift beyond `z_threshold` reference standard deviations is
    flagged. Uses the reference distribution's own std (never the recent
    one) so a feature that's simply become more volatile doesn't mask its
    own mean shift."""
    shifted = {}
    for col in feature_names:
        if col not in reference_df.columns or col not in recent_df.columns:
            continue
        ref = pd.to_numeric(reference_df[col], errors="coerce").dropna()
        recent = pd.to_numeric(recent_df[col], errors="coerce").dropna()
        if len(ref) < 30 or len(recent) < 5:
            continue
        std = ref.std()
        if not std or std == 0:
            continue
        shift = abs(recent.mean() - ref.mean()) / std
        if shift >= z_threshold:
            shifted[col] = {"reference_mean": float(ref.mean()), "recent_mean": float(recent.mean()), "shift_in_std": float(shift)}
    return (len(shifted) > 0), {"shifted_features": shifted}


def detect_prediction_drift(reference_predictions: np.ndarray, recent_predictions: np.ndarray, threshold: float = 0.15) -> tuple[bool, dict[str, Any]]:
    ref = np.asarray(reference_predictions, dtype=float)
    ref = ref[~np.isnan(ref)]
    recent = np.asarray(recent_predictions, dtype=float)
    recent = recent[~np.isnan(recent)]
    if len(ref) < 10 or len(recent) < 5:
        return False, {"reason": "insufficient samples to assess prediction drift."}
    shift = abs(float(recent.mean()) - float(ref.mean()))
    return shift >= threshold, {"reference_mean": float(ref.mean()), "recent_mean": float(recent.mean()), "shift": shift}


def detect_calibration_drift(training_brier_score: float | None, recent_brier_score: float | None, threshold: float = 0.05) -> tuple[bool, dict[str, Any]]:
    """A materially WORSE (higher) Brier score on recent outcomes than what
    was measured at training/validation time - probabilities that used to be
    trustworthy no longer are."""
    if training_brier_score is None or recent_brier_score is None:
        return False, {"reason": "training or recent Brier score unavailable."}
    degradation = recent_brier_score - training_brier_score
    return degradation >= threshold, {"training_brier_score": training_brier_score, "recent_brier_score": recent_brier_score, "degradation": degradation}


def detect_recent_hit_rate_drop(training_hit_rate: float | None, recent_hit_rate: float | None, threshold: float = 0.15) -> tuple[bool, dict[str, Any]]:
    if training_hit_rate is None or recent_hit_rate is None:
        return False, {"reason": "training or recent hit rate unavailable."}
    drop = training_hit_rate - recent_hit_rate
    return drop >= threshold, {"training_hit_rate": training_hit_rate, "recent_hit_rate": recent_hit_rate, "drop": drop}


def detect_missing_data_increase(
    reference_missingness_pct: dict[str, float], recent_missingness_pct: dict[str, float], threshold_pct: float = 20.0
) -> tuple[bool, dict[str, Any]]:
    increased = {}
    for col, recent_pct in recent_missingness_pct.items():
        ref_pct = reference_missingness_pct.get(col, 0.0)
        if recent_pct - ref_pct >= threshold_pct:
            increased[col] = {"reference_pct": ref_pct, "recent_pct": recent_pct}
    return (len(increased) > 0), {"increased_missingness": increased}


def run_drift_checks(
    reference_df: pd.DataFrame,
    recent_df: pd.DataFrame,
    feature_names: list[str],
    reference_predictions: np.ndarray | None = None,
    recent_predictions: np.ndarray | None = None,
    training_brier_score: float | None = None,
    recent_brier_score: float | None = None,
    training_hit_rate: float | None = None,
    recent_hit_rate: float | None = None,
) -> DriftReport:
    warnings: list[str] = []
    details: dict[str, Any] = {}

    feature_drifted, feature_details = detect_feature_drift(reference_df, recent_df, feature_names)
    details["feature_drift"] = feature_details
    if feature_drifted:
        warnings.append(WARNING_FEATURE_DRIFT)

    if reference_predictions is not None and recent_predictions is not None:
        pred_drifted, pred_details = detect_prediction_drift(reference_predictions, recent_predictions)
        details["prediction_drift"] = pred_details
        if pred_drifted:
            warnings.append(WARNING_PERFORMANCE_DRIFT)

    calib_drifted, calib_details = detect_calibration_drift(training_brier_score, recent_brier_score)
    details["calibration_drift"] = calib_details
    if calib_drifted:
        warnings.append(WARNING_CALIBRATION_DRIFT)

    hit_rate_dropped, hit_rate_details = detect_recent_hit_rate_drop(training_hit_rate, recent_hit_rate)
    details["hit_rate_drift"] = hit_rate_details
    if hit_rate_dropped:
        warnings.append(WARNING_PERFORMANCE_DRIFT)

    reference_missingness = {c: float(reference_df[c].isna().mean() * 100.0) for c in feature_names if c in reference_df.columns}
    recent_missingness = {c: float(recent_df[c].isna().mean() * 100.0) for c in feature_names if c in recent_df.columns}
    missing_increased, missing_details = detect_missing_data_increase(reference_missingness, recent_missingness)
    details["missing_data_drift"] = missing_details
    if missing_increased:
        warnings.append(WARNING_DATA_QUALITY_DRIFT)

    return DriftReport(warnings=sorted(set(warnings)), details=details)
