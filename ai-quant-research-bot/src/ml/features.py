"""Explicit ML feature whitelist and feature-matrix preparation (Phase 6 Part C).

`FEATURE_WHITELIST` is deliberately just `dataset_builder.FEATURE_COLUMNS` -
every column in that list was already designed, built, and tested (see
`tests/test_dataset_builder.py` and `tests/test_smc_features.py`) to be
computable using ONLY information knowable at each row's own timestamp. This
module does not duplicate that feature-generation logic (Part A) - it only
selects from it, documents WHY each group is safe to use, and handles the
mechanical parts of turning a dataset into a model-ready matrix: which
columns are numeric-model-safe, and how missing values are imputed WITHOUT
leaking test/validation statistics into training (imputation statistics are
always fit on the training split only - see `fit_imputation_stats` /
`FittedFeatureSpec`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import dataset_builder

# The whitelist IS dataset_builder.FEATURE_COLUMNS - kept as a separate name
# in this module (rather than importing that list directly everywhere) so a
# future deliberate narrowing of "what ML may use" doesn't require touching
# dataset_builder.py, which stays the single source of truth for "what's
# point-in-time safe to compute at all."
FEATURE_WHITELIST: list[str] = list(dataset_builder.FEATURE_COLUMNS)

# Documented feature groups (Part C) - purely descriptive, used by
# explainability (predictor.py) to label which family a feature belongs to.
FEATURE_GROUPS: dict[str, list[str]] = {
    "technical": [
        "close", "sma_20", "sma_50", "sma_200", "ema_50", "ema_200", "rsi_14", "atr_14",
        "momentum_20d", "momentum_60d", "relative_volume", "daily_volatility_pct",
    ],
    "strategy": ["mean_reversion_safe_active", "momentum_breakout_active", "trend_following_active"],
    "regime": ["regime_primary_code", "regime_volatility_elevated", "regime_risk_off", "regime_confidence"],
    "big_money": [
        "institutional_score", "insider_score", "options_flow_score",
        "big_money_composite_score", "big_money_data_quality_score",
    ],
    "smc": [
        "smc_fvg_bullish_count", "smc_fvg_bearish_count", "smc_swing_high_count", "smc_swing_low_count",
        "smc_bos_bullish_count", "smc_bos_bearish_count", "smc_choch_bullish_count", "smc_choch_bearish_count",
        "smc_liquidity_sweep_high_count", "smc_liquidity_sweep_low_count",
    ],
    "context": ["return_vs_spy", "macro_fed_funds_rate", "macro_10y_2y_spread"],
}


def feature_group_for(feature_name: str) -> str | None:
    for group, members in FEATURE_GROUPS.items():
        if feature_name in members:
            return group
    return None


@dataclass(frozen=True)
class FittedFeatureSpec:
    """Imputation statistics fit on a TRAINING split only - reused as-is at
    validation/test/predict time. Fitting a median on the validation or test
    split (or on predict-time data) would leak that split's own distribution
    into the "features," a subtle form of the same lookahead problem this
    whole phase is built to avoid."""

    feature_names: list[str]
    medians: dict[str, float]

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.reindex(columns=self.feature_names).astype(float)
        for col in self.feature_names:
            out[col] = out[col].fillna(self.medians.get(col, 0.0))
        return out

    def transform_allow_nan(self, df: pd.DataFrame) -> pd.DataFrame:
        """For model families that natively handle missing values
        (HistGradientBoosting) - skip imputation entirely rather than
        discard information a NaN-aware model could have used."""
        return df.reindex(columns=self.feature_names).astype(float)


def fit_feature_spec(train_df: pd.DataFrame, feature_names: list[str] | None = None) -> FittedFeatureSpec:
    """Fit median-imputation statistics on `train_df` ONLY."""
    names = feature_names or FEATURE_WHITELIST
    medians = {}
    for col in names:
        if col in train_df.columns:
            series = pd.to_numeric(train_df[col], errors="coerce")
            median = series.median()
            medians[col] = float(median) if median == median else 0.0
        else:
            medians[col] = 0.0
    return FittedFeatureSpec(feature_names=names, medians=medians)


def missing_feature_columns(df: pd.DataFrame, feature_names: list[str] | None = None) -> list[str]:
    """Which whitelisted features are entirely absent from `df` - used by
    the audit layer and by predictor.py to fail safely rather than silently
    treat a whole missing column as all-zero."""
    names = feature_names or FEATURE_WHITELIST
    return [c for c in names if c not in df.columns]
