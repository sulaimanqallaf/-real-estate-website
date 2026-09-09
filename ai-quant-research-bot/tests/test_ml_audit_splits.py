"""Tests for src/ml/audit.py, src/ml/splits.py, src/ml/labels.py,
src/ml/features.py (Phase 6 Part Y "Dataset/audit")."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import dataset_builder as db
from src.ml import audit, features, labels, splits
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@pytest.fixture(scope="module")
def config():
    return load_config(CONFIG_PATH)


def _build_dataset(config, n=500, seed=5):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    price = 100 + np.cumsum(rng.normal(0, 1.0, n))
    df = pd.DataFrame(
        {"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": rng.integers(1_000_000, 5_000_000, n).astype(float)},
        index=dates,
    )
    df.index.name = "date"
    return db.build_dataset_rows("TEST", df, config, min_history_bars=200)


@pytest.fixture(scope="module")
def base_rows(config):
    return _build_dataset(config)


def make_dataset(config, n=500, seed=5):
    """Kept for tests that need a custom n/seed; the module-scoped
    `base_rows` fixture (default n=500, seed=5) is preferred where a test
    doesn't care about the exact dataset - avoids rebuilding it dozens of
    times per file (indicator/SMC recomputation per row is not free)."""
    return _build_dataset(config, n, seed)


# --- labels --------------------------------------------------------------------------


def test_classification_threshold_uses_config_when_present():
    config = {"ml": {"classification_thresholds": {"5d": 0.05}}}
    assert labels.classification_threshold(5, config) == 0.05


def test_classification_threshold_falls_back_to_default_when_missing():
    assert labels.classification_threshold(10, {"ml": {"classification_thresholds": {}}}) == labels.DEFAULT_CLASSIFICATION_THRESHOLDS[10]


def test_regression_target_is_the_raw_forward_return_column(config, base_rows):
    rows = base_rows
    target = labels.build_regression_target(rows, 5)
    pd.testing.assert_series_equal(target, rows["forward_5d_return"], check_names=False)


def test_classification_target_respects_threshold_and_preserves_nan(config, base_rows):
    rows = base_rows
    target = labels.build_classification_target(rows, 5, config)
    forward = rows["forward_5d_return"]
    threshold = labels.classification_threshold(5, config)
    assert (target[forward.notna()] == (forward[forward.notna()] > threshold).astype(float)).all()
    assert target[forward.isna()].isna().all()


def test_rows_with_known_label_drops_unlabeled_rows(config, base_rows):
    rows = base_rows
    known = labels.rows_with_known_label(rows, 5)
    assert known["forward_5d_return"].notna().all()
    assert len(known) < len(rows)


# --- features --------------------------------------------------------------------------


def test_feature_whitelist_matches_dataset_builder_columns():
    assert features.FEATURE_WHITELIST == list(db.FEATURE_COLUMNS)


def test_feature_groups_cover_every_whitelisted_feature():
    grouped = {f for members in features.FEATURE_GROUPS.values() for f in members}
    assert grouped == set(features.FEATURE_WHITELIST)


def test_fit_feature_spec_never_leaks_test_statistics(config, base_rows):
    rows = base_rows
    split = splits.chronological_split(rows)
    spec = features.fit_feature_spec(split.train, ["momentum_20d"])
    train_median = pd.to_numeric(split.train["momentum_20d"], errors="coerce").median()
    assert spec.medians["momentum_20d"] == pytest.approx(train_median)
    # Applying the SAME spec to test data must use the TRAIN median, not test's own.
    test_median = pd.to_numeric(split.test["momentum_20d"], errors="coerce").median()
    if test_median != train_median:
        transformed_test = spec.transform(split.test)
        assert not transformed_test["momentum_20d"].isna().any()


def test_transform_allow_nan_preserves_missing_values(config, base_rows):
    rows = base_rows
    spec = features.fit_feature_spec(rows, ["institutional_score"])
    out = spec.transform_allow_nan(rows)
    assert out["institutional_score"].isna().all()  # never fetched in this test - stays NaN, not imputed


def test_missing_feature_columns_detected():
    df = pd.DataFrame({"close": [1, 2, 3]})
    missing = features.missing_feature_columns(df, ["close", "sma_20"])
    assert missing == ["sma_20"]


# --- audit -----------------------------------------------------------------------------


def test_audit_clean_dataset_passes(config, base_rows):
    rows = base_rows
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert report.passed is True


def test_audit_detects_insufficient_history(config):
    rows = make_dataset(config, n=50)  # min_history_bars=200 means this yields 0 rows
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert report.passed is False
    assert any(i.check == "insufficient_history" for i in report.critical_issues)


def test_audit_detects_duplicate_rows(config, base_rows):
    rows = base_rows
    with_dupe = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    report = audit.run_audit(with_dupe, horizon=5, config=config, min_rows=100)
    assert report.passed is False
    assert any(i.check == "duplicate_rows" for i in report.critical_issues)


def test_audit_detects_timestamp_ordering_violation(config, base_rows):
    rows = base_rows
    shuffled = rows.copy()
    shuffled.iloc[[0, 1]] = shuffled.iloc[[1, 0]].values
    report = audit.run_audit(shuffled, horizon=5, config=config, min_rows=100)
    assert any(i.check == "timestamp_ordering" for i in report.critical_issues)


def test_audit_detects_missing_target_column_entirely(config, base_rows):
    rows = base_rows.drop(columns=["forward_5d_return"])
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert report.passed is False
    assert any(i.check == "target_availability" for i in report.critical_issues)


def test_audit_leakage_trap_catches_target_derived_feature(config, base_rows):
    """Scenario C (Part Z): intentionally add a future-derived feature and
    confirm the audit flags it as critical, not a warning."""
    rows = base_rows
    cheat = rows.copy()
    cheat["cheat_feature"] = cheat["forward_5d_return"] * 2.0
    report = audit.run_audit(cheat, horizon=5, config=config, feature_names=features.FEATURE_WHITELIST + ["cheat_feature"], min_rows=100)
    assert report.passed is False
    assert any(i.check == "feature_leakage" for i in report.critical_issues)


def test_audit_leakage_trap_catches_label_column_itself_in_feature_list(config, base_rows):
    rows = base_rows
    report = audit.run_audit(rows, horizon=5, config=config, feature_names=features.FEATURE_WHITELIST + ["forward_10d_return"], min_rows=100)
    assert report.passed is False


def test_audit_flags_constant_column_as_warning_not_critical(config, base_rows):
    rows = base_rows
    rows = rows.copy()
    rows["daily_volatility_pct"] = 1.0  # force a constant column
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert report.passed is True  # a constant column alone must not block training
    assert any(i.check == "constant_column" for i in report.warnings)


def test_audit_detects_class_imbalance(config, base_rows):
    rows = base_rows
    # Force overwhelming positive class by making returns huge.
    rows = rows.copy()
    rows["forward_5d_return"] = 0.5
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert any(i.check == "class_imbalance" for i in report.warnings)


def test_audit_summary_is_human_readable(config):
    rows = make_dataset(config, n=50)
    report = audit.run_audit(rows, horizon=5, config=config, min_rows=100)
    assert "CRITICAL" in report.summary()


# --- splits ------------------------------------------------------------------------------


def test_chronological_split_never_shuffles(config, base_rows):
    rows = base_rows
    split = splits.chronological_split(rows)
    combined = pd.concat([split.train, split.validation, split.test])
    assert combined["timestamp"].is_monotonic_increasing
    assert split.train["timestamp"].max() <= split.validation["timestamp"].min()
    assert split.validation["timestamp"].max() <= split.test["timestamp"].min()


def test_chronological_split_respects_custom_percentages(config, base_rows):
    rows = base_rows
    split = splits.chronological_split(rows, train_pct=0.5, validation_pct=0.3, test_pct=0.2)
    total = len(rows)
    assert abs(len(split.train) - int(total * 0.5)) <= 1
    assert abs(len(split.validation) - int(total * 0.3)) <= 1


def test_chronological_split_rejects_percentages_not_summing_to_one(config, base_rows):
    rows = base_rows
    with pytest.raises(ValueError):
        splits.chronological_split(rows, train_pct=0.5, validation_pct=0.3, test_pct=0.3)


def test_walk_forward_folds_are_chronologically_ordered_and_non_overlapping(config, base_rows):
    rows = base_rows
    folds = splits.walk_forward_folds(rows, min_train_rows=100, validation_rows=30, expanding=True)
    assert len(folds) > 1
    for fold in folds:
        assert fold.train["timestamp"].max() < fold.validation["timestamp"].min() or fold.train["timestamp"].max() <= fold.validation["timestamp"].min()
        assert fold.train_end <= fold.validation_start
    for a, b in zip(folds, folds[1:]):
        assert a.validation_end <= b.validation_start  # folds progress forward, never backward


def test_walk_forward_expanding_grows_train_window(config, base_rows):
    rows = base_rows
    folds = splits.walk_forward_folds(rows, min_train_rows=100, validation_rows=30, expanding=True)
    sizes = [len(f.train) for f in folds]
    assert sizes == sorted(sizes)  # strictly non-decreasing


def test_walk_forward_rolling_keeps_train_window_fixed_size(config, base_rows):
    rows = base_rows
    folds = splits.walk_forward_folds(rows, min_train_rows=100, validation_rows=30, expanding=False)
    sizes = {len(f.train) for f in folds}
    assert len(sizes) == 1  # every fold's train window is the same fixed size


def test_no_shuffle_regression_row_order_preserved_within_split(config, base_rows):
    """A direct 'no random shuffle' check: every row in the train split
    appears in the same relative order as in the original chronological
    dataset."""
    rows = base_rows
    split = splits.chronological_split(rows)
    original_order = rows.sort_values("timestamp")["timestamp"].reset_index(drop=True)
    train_positions = original_order[original_order.isin(split.train["timestamp"])].reset_index(drop=True)
    pd.testing.assert_series_equal(train_positions, split.train["timestamp"].reset_index(drop=True))
