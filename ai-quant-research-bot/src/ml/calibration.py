"""Probability calibration (Phase 6 Part G): Platt/sigmoid or isotonic,
fit on a held-out (validation) split's raw predicted probabilities vs actual
outcomes - never on the training split itself, which would just calibrate a
model to agree with predictions it already memorized.

`fit_calibration()` refuses (returns `available=False`) when there aren't
enough samples to trust a calibration curve, rather than fitting one anyway
and reporting false precision - see Part G: "If sample size is too small:
say calibration unavailable."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

METHOD_SIGMOID = "sigmoid"
METHOD_ISOTONIC = "isotonic"
METHOD_UNAVAILABLE = "unavailable"

MIN_SAMPLES_FOR_SIGMOID = 50
MIN_SAMPLES_FOR_ISOTONIC = 200


@dataclass
class CalibrationResult:
    method: str
    calibrator: Any | None
    available: bool
    sample_size: int
    reason: str | None = None


def fit_calibration(raw_probabilities: np.ndarray, y_true: np.ndarray, method: str = "auto") -> CalibrationResult:
    raw_probabilities = np.asarray(raw_probabilities, dtype=float)
    y_true = np.asarray(y_true, dtype=float)
    valid = ~np.isnan(raw_probabilities) & ~np.isnan(y_true)
    raw = raw_probabilities[valid]
    y = y_true[valid]
    n = len(y)

    if n < MIN_SAMPLES_FOR_SIGMOID:
        return CalibrationResult(
            METHOD_UNAVAILABLE, None, available=False, sample_size=n,
            reason=f"only {n} labeled samples available for calibration (need >= {MIN_SAMPLES_FOR_SIGMOID}).",
        )
    if len(np.unique(y)) < 2:
        return CalibrationResult(
            METHOD_UNAVAILABLE, None, available=False, sample_size=n,
            reason="calibration set has only one class present - cannot fit a calibration curve.",
        )

    chosen = method
    if method == "auto":
        chosen = METHOD_ISOTONIC if n >= MIN_SAMPLES_FOR_ISOTONIC else METHOD_SIGMOID

    if chosen == METHOD_ISOTONIC:
        from sklearn.isotonic import IsotonicRegression

        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw, y)
    elif chosen == METHOD_SIGMOID:
        from sklearn.linear_model import LogisticRegression

        calibrator = LogisticRegression()
        calibrator.fit(raw.reshape(-1, 1), y)
    else:
        raise ValueError(f"Unknown calibration method '{chosen}'.")

    return CalibrationResult(chosen, calibrator, available=True, sample_size=n)


def apply_calibration(calibration: CalibrationResult, raw_probabilities: np.ndarray) -> np.ndarray | None:
    """Returns `None` (never a fabricated calibrated value) when calibration
    wasn't available. Output is always clipped to [0, 1]."""
    if not calibration.available or calibration.calibrator is None:
        return None
    raw = np.asarray(raw_probabilities, dtype=float)
    if calibration.method == METHOD_ISOTONIC:
        return np.clip(calibration.calibrator.predict(raw), 0.0, 1.0)
    return np.clip(calibration.calibrator.predict_proba(raw.reshape(-1, 1))[:, 1], 0.0, 1.0)
