"""Prediction targets built from dataset_builder's already-causal
forward_*d_return columns (Phase 6 Part B). Labels are the ONE place looking
forward is correct - see dataset_builder.py's module docstring; this module
never re-derives a label from anything but those already-computed columns,
so it can't accidentally introduce a new leakage path.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

HORIZONS = (5, 10, 20)

DEFAULT_CLASSIFICATION_THRESHOLDS = {5: 0.01, 10: 0.015, 20: 0.02}


def regression_target_column(horizon: int) -> str:
    return f"forward_{horizon}d_return"


def classification_threshold(horizon: int, config: dict[str, Any] | None = None) -> float:
    """Configurable hurdle a forward return must clear to count as a
    "success" for the classification target - see config.ml.
    classification_thresholds. Falls back to a documented default rather
    than crashing on a missing config key."""
    thresholds = (config or {}).get("ml", {}).get("classification_thresholds", {})
    # config keys are strings ("5d") when loaded from YAML - accept either.
    for key in (str(horizon), f"{horizon}d", horizon):
        if key in thresholds:
            return float(thresholds[key])
    return DEFAULT_CLASSIFICATION_THRESHOLDS.get(horizon, 0.01)


def build_regression_target(df: pd.DataFrame, horizon: int) -> pd.Series:
    """The regression target IS the forward return column itself - no
    transformation, no re-derivation. Rows with no label yet (the horizon
    hasn't happened) stay NaN, exactly as dataset_builder left them."""
    col = regression_target_column(horizon)
    if col not in df.columns:
        raise KeyError(f"dataset is missing label column '{col}' - was it built with this horizon?")
    return df[col]


def build_classification_target(df: pd.DataFrame, horizon: int, config: dict[str, Any] | None = None) -> pd.Series:
    """1.0 if forward_{horizon}d_return exceeds the configured hurdle, 0.0
    otherwise, NaN wherever the underlying forward return is itself NaN
    (label not yet knowable) - never silently coerced to 0."""
    threshold = classification_threshold(horizon, config)
    forward_return = build_regression_target(df, horizon)
    target = (forward_return > threshold).astype(float)
    target[forward_return.isna()] = float("nan")
    return target


def rows_with_known_label(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Drop rows whose label isn't knowable yet - never train/evaluate on a
    fabricated or forward-filled label."""
    col = regression_target_column(horizon)
    return df[df[col].notna()]
