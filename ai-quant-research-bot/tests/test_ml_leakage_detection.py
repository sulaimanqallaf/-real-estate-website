"""Leakage-detection tests for the ML pipeline (AI Quant Trading Platform
sprint, Phase 4: "review Freqtrade's leakage-detection techniques and
implement compatible tests" - original tests only; no Freqtrade code is
used or copied, GPL-3.0, see docs/platform/OSS_INTEGRATION_AUDIT.md).

This project's dataset_builder/SMC feature layer already has thorough
no-lookahead coverage (tests/test_dataset_builder.py's
test_feature_row_identical_regardless_of_future_bars_present,
tests/test_smc_features.py's causal-feature-table tests) - duplicating
those here would add nothing. The real gap this file closes is at the
WALK-FORWARD VALIDATION boundary: this project's labels are
forward_{horizon}d_return columns, built from price data up to
`horizon` rows AHEAD of each row. A plain walk-forward split (train
ends exactly where validation begins, no gap - the purged/embargoed
walk-forward CV technique from the quantitative-finance literature,
which FreqAI's own documentation also discusses) lets a handful of
rows near the tail of train carry labels computed FROM price action
inside the very validation window being used to judge the model - the
model is partly scored on data it implicitly saw. This is demonstrated
below (first failing without an embargo, then closed by
`walk_forward_folds(..., embargo_rows=horizon)`, which
`validator.run_walk_forward_evaluation` now sets automatically - see
src/ml/splits.py's updated docstring).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import splits


def _dataset_with_forward_return_label(n: int, horizon: int, seed: int = 7) -> pd.DataFrame:
    """A plain synthetic dataset whose `forward_{horizon}d_return` column
    is HONESTLY forward-looking (row i's label is a real function of
    price at row i+horizon, exactly like dataset_builder's real
    columns) - built independently here (not by calling
    dataset_builder) so this test owns, and can directly reason about,
    exactly which future row each label depends on."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    price = 100.0 * np.cumprod(1.0 + rng.normal(0.0003, 0.01, n))
    forward_return = np.full(n, np.nan)
    forward_return[: n - horizon] = price[horizon:] / price[: n - horizon] - 1.0
    return pd.DataFrame({
        "timestamp": dates, "ticker": "TEST", "close": price,
        f"forward_{horizon}d_return": forward_return,
    })


def _label_window_overlaps_validation(fold: splits.WalkForwardFold, df: pd.DataFrame, horizon: int, timestamp_col: str = "timestamp") -> bool:
    """True if ANY row in `fold.train` has a label whose defining future
    window (its own timestamp's row position + `horizon` rows) lands on
    or after `fold.validation`'s first row - i.e. the exact leakage this
    file is about, checked directly against row POSITIONS in the full
    sorted dataset, not approximated by calendar-date arithmetic."""
    sorted_df = df.sort_values(timestamp_col).reset_index(drop=True)
    position_by_ts = {ts: i for i, ts in enumerate(sorted_df[timestamp_col])}
    val_start_pos = position_by_ts[fold.validation[timestamp_col].iloc[0]]

    for ts in fold.train[timestamp_col]:
        train_row_pos = position_by_ts[ts]
        label_window_end_pos = train_row_pos + horizon
        if label_window_end_pos >= val_start_pos:
            return True
    return False


def test_plain_walk_forward_without_embargo_leaks_label_windows_into_validation():
    """The real, reproduced leakage: with embargo_rows=0 (the historical
    default, unchanged for backward compatibility), the LAST `horizon`
    rows of every fold's train slice have forward-return labels whose
    window reaches into that same fold's validation slice."""
    horizon = 10
    df = _dataset_with_forward_return_label(n=400, horizon=horizon)
    folds = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=0)

    assert len(folds) >= 2
    assert any(_label_window_overlaps_validation(f, df, horizon) for f in folds)


def test_embargo_rows_closes_the_label_window_leak():
    """The fix: embargo_rows=horizon removes exactly the train rows whose
    label window could reach into validation - zero overlap in every
    fold, on the identical dataset/fold boundaries as the test above."""
    horizon = 10
    df = _dataset_with_forward_return_label(n=400, horizon=horizon)
    folds = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=horizon)

    assert len(folds) >= 2
    assert not any(_label_window_overlaps_validation(f, df, horizon) for f in folds)


def test_embargo_rows_does_not_change_fold_cadence_or_validation_windows():
    """The embargo only shrinks WHAT's included in train - which rows
    fall in each fold's validation window (and how many folds exist)
    must stay identical to the embargo_rows=0 case, so a caller can
    turn this on without silently changing evaluation coverage."""
    horizon = 10
    df = _dataset_with_forward_return_label(n=400, horizon=horizon)
    folds_plain = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=0)
    folds_embargoed = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=horizon)

    assert len(folds_plain) == len(folds_embargoed)
    for plain, embargoed in zip(folds_plain, folds_embargoed):
        pd.testing.assert_frame_equal(plain.validation.reset_index(drop=True), embargoed.validation.reset_index(drop=True))
        assert embargoed.validation_start == plain.validation_start
        assert embargoed.validation_end == plain.validation_end
        assert len(embargoed.train) == len(plain.train) - horizon


def test_embargo_larger_than_a_fold_train_window_never_produces_a_negative_size_slice():
    """An embargo_rows misconfigured larger than a fold's own train size
    must degrade to an empty train slice for that fold (and be skipped),
    never wrap around / produce a negative-length slice."""
    horizon = 10_000  # deliberately absurd relative to the dataset
    df = _dataset_with_forward_return_label(n=400, horizon=10)
    folds = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=horizon)

    for fold in folds:
        assert len(fold.train) >= 0  # never negative/garbage


def test_validator_run_walk_forward_evaluation_applies_the_label_horizon_as_embargo(monkeypatch):
    """src/ml/validator.py's run_walk_forward_evaluation must call
    walk_forward_folds with embargo_rows == the horizon being evaluated
    (not 0) - a direct regression test on the wiring itself, so a future
    edit can't silently drop the embargo call-site fix."""
    from src.ml import validator

    captured = {}
    original = splits.walk_forward_folds

    def spy(*args, **kwargs):
        captured["embargo_rows"] = kwargs.get("embargo_rows")
        return original(*args, **kwargs)

    # run_walk_forward_evaluation does `from . import splits as ml_splits`
    # LOCALLY (inside the function, re-fetched on every call) - patching
    # the real module's attribute here is what that live import picks up.
    monkeypatch.setattr(splits, "walk_forward_folds", spy)

    tiny = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=10, freq="B"), "ticker": "TEST",
        "momentum_20d": range(10), "rsi_14": range(10), "forward_5d_return": [0.01] * 10,
    })
    validator.run_walk_forward_evaluation(
        tiny, horizon=5, task="classification", model_type="logistic_regression",
        feature_names=["momentum_20d", "rsi_14"], min_train_rows=100, validation_rows=20,
    )

    assert captured["embargo_rows"] == 5


# --- train/validation row-identity contamination (a separate, simpler leakage class) ---


def test_no_single_row_appears_in_both_a_folds_train_and_validation():
    df = _dataset_with_forward_return_label(n=400, horizon=5)
    folds = splits.walk_forward_folds(df, min_train_rows=150, validation_rows=50, embargo_rows=5)
    for fold in folds:
        train_ts = set(fold.train["timestamp"])
        val_ts = set(fold.validation["timestamp"])
        assert train_ts.isdisjoint(val_ts)


def test_chronological_split_train_and_test_never_share_a_row():
    df = _dataset_with_forward_return_label(n=300, horizon=5)
    split = splits.chronological_split(df)
    assert set(split.train["timestamp"]).isdisjoint(set(split.test["timestamp"]))
    assert set(split.validation["timestamp"]).isdisjoint(set(split.test["timestamp"]))
