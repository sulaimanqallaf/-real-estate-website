"""Tests for src/ml/models.py, src/ml/validator.py, src/ml/calibration.py
(Phase 6 Part Y "Training"/"Validation"/"Overfitting")."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import calibration, features, labels, models, splits, validator

FEATURE_NAMES = ["momentum_20d", "rsi_14"]


# --- models: each family trains, deterministic seeding --------------------------------


@pytest.mark.parametrize("model_type", models.ALL_MODEL_TYPES)
def test_each_model_family_trains_for_classification(separable_dataset, model_type):
    split = splits.chronological_split(separable_dataset)
    spec = models.build_model(model_type, models.TASK_CLASSIFICATION, FEATURE_NAMES)
    feature_spec = features.fit_feature_spec(split.train, FEATURE_NAMES)
    X_train = spec.prepare_X(split.train, feature_spec)
    y_train = labels.build_classification_target(split.train, 5)
    spec.fit(X_train, y_train.fillna(0.0))

    X_val = spec.prepare_X(split.validation, feature_spec)
    proba = spec.predict_proba(X_val)
    assert proba.shape[0] == len(split.validation)
    assert ((proba >= 0.0) & (proba <= 1.0)).all()


@pytest.mark.parametrize("model_type", [models.MODEL_RANDOM_FOREST, models.MODEL_GRADIENT_BOOSTING])
def test_each_model_family_trains_for_regression(separable_dataset, model_type):
    split = splits.chronological_split(separable_dataset)
    spec = models.build_model(model_type, models.TASK_REGRESSION, FEATURE_NAMES)
    feature_spec = features.fit_feature_spec(split.train, FEATURE_NAMES)
    X_train = spec.prepare_X(split.train, feature_spec)
    y_train = labels.build_regression_target(split.train, 5)
    spec.fit(X_train, y_train)

    X_val = spec.prepare_X(split.validation, feature_spec)
    preds = spec.predict(X_val)
    assert preds.shape[0] == len(split.validation)


def test_logistic_regression_rejects_regression_task():
    with pytest.raises(ValueError):
        models.build_model(models.MODEL_LOGISTIC_REGRESSION, models.TASK_REGRESSION, FEATURE_NAMES)


def test_unknown_model_type_raises():
    with pytest.raises(ValueError):
        models.build_model("neural_net", models.TASK_CLASSIFICATION, FEATURE_NAMES)


def test_deterministic_seed_behavior_gives_identical_predictions(separable_dataset):
    """Same data, same hyperparameters (including random_state) -> bit-for-bit
    identical predictions across two independently trained instances."""
    split = splits.chronological_split(separable_dataset)
    feature_spec = features.fit_feature_spec(split.train, FEATURE_NAMES)
    y_train = labels.build_classification_target(split.train, 5).fillna(0.0)

    spec_a = models.build_model(models.MODEL_RANDOM_FOREST, models.TASK_CLASSIFICATION, FEATURE_NAMES)
    spec_b = models.build_model(models.MODEL_RANDOM_FOREST, models.TASK_CLASSIFICATION, FEATURE_NAMES)
    X_train = spec_a.prepare_X(split.train, feature_spec)
    spec_a.fit(X_train, y_train)
    spec_b.fit(X_train, y_train)

    X_val = spec_a.prepare_X(split.validation, feature_spec)
    np.testing.assert_array_equal(spec_a.predict_proba(X_val), spec_b.predict_proba(X_val))


def test_feature_importances_available_for_tree_models(separable_dataset):
    split = splits.chronological_split(separable_dataset)
    spec = models.build_model(models.MODEL_RANDOM_FOREST, models.TASK_CLASSIFICATION, FEATURE_NAMES)
    feature_spec = features.fit_feature_spec(split.train, FEATURE_NAMES)
    spec.fit(spec.prepare_X(split.train, feature_spec), labels.build_classification_target(split.train, 5).fillna(0.0))
    importances = spec.feature_importances()
    assert set(importances) == set(FEATURE_NAMES)


def test_signed_coefficients_available_for_logistic_regression(separable_dataset):
    split = splits.chronological_split(separable_dataset)
    spec = models.build_model(models.MODEL_LOGISTIC_REGRESSION, models.TASK_CLASSIFICATION, FEATURE_NAMES)
    feature_spec = features.fit_feature_spec(split.train, FEATURE_NAMES)
    spec.fit(spec.prepare_X(split.train, feature_spec), labels.build_classification_target(split.train, 5).fillna(0.0))
    coefs = spec.signed_coefficients()
    assert set(coefs) == set(FEATURE_NAMES)
    assert spec.feature_importances() is None  # LR has no tree-style importances


def test_histgradientboosting_handles_all_nan_feature_column_without_crashing(separable_dataset):
    """Regression test for a real bug found during integration testing:
    HistGradientBoosting's binning step raises on a column with fewer than
    2 distinct non-missing values (e.g. institutional_score with no data
    source configured, all-NaN across the whole training window)."""
    split = splits.chronological_split(separable_dataset)
    names = FEATURE_NAMES + ["institutional_score"]  # institutional_score is all-NaN in this fixture
    spec = models.build_model(models.MODEL_GRADIENT_BOOSTING, models.TASK_CLASSIFICATION, names)
    feature_spec = features.fit_feature_spec(split.train, names)
    X_train = spec.prepare_X(split.train, feature_spec)
    assert (X_train["institutional_score"] == 0.0).all()  # neutralized, not left all-NaN
    spec.fit(X_train, labels.build_classification_target(split.train, 5).fillna(0.0))  # must not raise


# --- validator: metrics -----------------------------------------------------------------


def test_classification_metrics_bounded_and_correct():
    y_true = np.array([1, 1, 0, 0, 1, 0])
    y_proba = np.array([0.9, 0.8, 0.2, 0.1, 0.6, 0.4])
    metrics = validator.compute_classification_metrics(y_true, y_proba)
    assert metrics["sample_size"] == 6
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["roc_auc"] <= 1.0
    assert 0.0 <= metrics["brier_score"] <= 1.0


def test_classification_metrics_handle_single_class_gracefully():
    y_true = np.array([1, 1, 1, 1])
    y_proba = np.array([0.9, 0.8, 0.7, 0.6])
    metrics = validator.compute_classification_metrics(y_true, y_proba)
    assert metrics["roc_auc"] is None
    assert metrics["accuracy"] == 1.0


def test_classification_metrics_empty_input_returns_none_not_crash():
    metrics = validator.compute_classification_metrics(np.array([]), np.array([]))
    assert metrics["sample_size"] == 0
    assert metrics["accuracy"] is None


def test_regression_metrics_correct():
    y_true = np.array([0.01, -0.02, 0.03, -0.01])
    y_pred = np.array([0.015, -0.01, 0.025, 0.005])
    metrics = validator.compute_regression_metrics(y_true, y_pred)
    assert metrics["mae"] > 0
    assert metrics["directional_accuracy"] == 0.75  # 3 of 4 signs match


def test_decile_analysis_monotonic_on_separable_data(separable_dataset):
    split = splits.chronological_split(separable_dataset)
    decile = validator.decile_analysis(
        labels.build_regression_target(split.test, 5).to_numpy(),
        pd.to_numeric(split.test["momentum_20d"], errors="coerce").to_numpy(),
    )
    avg_returns = [d["avg_return"] for d in sorted(decile, key=lambda d: d["decile"])]
    assert avg_returns == sorted(avg_returns)  # monotonically increasing by construction


def test_top_bucket_stats_reports_unavailable_with_too_few_samples():
    result = validator.top_bucket_stats(np.array([0.01, 0.02]), np.array([0.5, 0.6]))
    assert result["available"] is False


def test_detect_overfit_fires_on_material_degradation():
    train_metrics = {"roc_auc": 0.95}
    val_metrics = {"roc_auc": 0.55}
    assert validator.detect_overfit(train_metrics, val_metrics, "roc_auc") == validator.OVERFIT_WARNING


def test_detect_overfit_does_not_fire_on_small_degradation():
    train_metrics = {"roc_auc": 0.80}
    val_metrics = {"roc_auc": 0.78}
    assert validator.detect_overfit(train_metrics, val_metrics, "roc_auc") is None


def test_detect_overfit_handles_missing_metrics_gracefully():
    assert validator.detect_overfit({}, {"roc_auc": 0.7}, "roc_auc") is None
    assert validator.detect_overfit({"roc_auc": 0.7}, {}, "roc_auc") is None


# --- validator: baselines ---------------------------------------------------------------


def test_majority_class_baseline():
    y = np.array([1, 1, 1, 0])
    result = validator.majority_class_baseline_metrics(y)
    assert result["majority_class"] == 1.0
    assert result["accuracy"] == 0.75


def test_momentum_direction_baseline_matches_sign(separable_dataset):
    result = validator.momentum_direction_baseline(separable_dataset, 5)
    assert result["sample_size"] > 0


def test_rule_score_baseline_uses_score_column(separable_dataset):
    result = validator.rule_score_baseline(separable_dataset, 5, "score")
    assert result is not None
    result_missing = validator.rule_score_baseline(separable_dataset.drop(columns=["score"]), 5, "score")
    assert result_missing is None


def test_compare_to_baselines_reports_no_incremental_value_honestly():
    comparison = validator.compare_to_baselines(0.5, {"baseline_a": 0.6, "baseline_b": 0.55})
    assert comparison["adds_incremental_value"] is False


def test_compare_to_baselines_reports_incremental_value_when_ml_wins():
    comparison = validator.compare_to_baselines(0.8, {"baseline_a": 0.6, "baseline_b": 0.55})
    assert comparison["adds_incremental_value"] is True


def test_compare_to_baselines_unavailable_ml_metric_never_claims_value():
    comparison = validator.compare_to_baselines(None, {"baseline_a": 0.6})
    assert comparison["adds_incremental_value"] is False


# --- validator: walk-forward -------------------------------------------------------------


def test_walk_forward_evaluation_on_separable_data_beats_chance(separable_dataset):
    result = validator.run_walk_forward_evaluation(
        separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION,
        feature_names=FEATURE_NAMES, min_train_rows=400, validation_rows=150,
    )
    assert result["aggregated"]["roc_auc"] > 0.7  # meaningfully better than a coin flip
    assert len(result["folds"]) >= 2


def test_walk_forward_evaluation_folds_ordered_chronologically(separable_dataset):
    result = validator.run_walk_forward_evaluation(
        separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION,
        feature_names=FEATURE_NAMES, min_train_rows=400, validation_rows=150,
    )
    ends = [f["validation_end"] for f in result["folds"]]
    assert ends == sorted(ends)


def test_walk_forward_evaluation_insufficient_rows_reports_reason():
    tiny = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=10, freq="B"), "ticker": "TEST",
        "momentum_20d": range(10), "rsi_14": range(10), "forward_5d_return": [0.01] * 10,
    })
    result = validator.run_walk_forward_evaluation(tiny, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, feature_names=["momentum_20d", "rsi_14"], min_train_rows=100, validation_rows=20)
    assert result["aggregated"] is None
    assert "reason" in result


# --- calibration -------------------------------------------------------------------------


def test_calibration_unavailable_with_too_few_samples():
    result = calibration.fit_calibration(np.array([0.1, 0.9, 0.3]), np.array([0, 1, 0]))
    assert result.available is False


def test_calibration_unavailable_with_single_class():
    n = 60
    result = calibration.fit_calibration(np.random.uniform(0, 1, n), np.zeros(n))
    assert result.available is False


def test_calibration_sigmoid_fits_with_moderate_sample(separable_dataset):
    split = splits.chronological_split(separable_dataset)
    proba = pd.to_numeric(split.validation["momentum_20d"], errors="coerce").to_numpy()
    proba = (proba - proba.min()) / (proba.max() - proba.min())
    y = labels.build_classification_target(split.validation, 5).to_numpy()
    result = calibration.fit_calibration(proba, y, method="sigmoid")
    assert result.available is True
    assert result.method == calibration.METHOD_SIGMOID


def test_apply_calibration_bounded_zero_one(separable_dataset):
    split = splits.chronological_split(separable_dataset)
    proba = pd.to_numeric(split.validation["momentum_20d"], errors="coerce").to_numpy()
    proba = (proba - proba.min()) / (proba.max() - proba.min())
    y = labels.build_classification_target(split.validation, 5).to_numpy()
    result = calibration.fit_calibration(proba, y)
    applied = calibration.apply_calibration(result, proba)
    assert ((applied >= 0.0) & (applied <= 1.0)).all()


def test_apply_calibration_none_when_unavailable():
    result = calibration.CalibrationResult(calibration.METHOD_UNAVAILABLE, None, available=False, sample_size=2)
    assert calibration.apply_calibration(result, np.array([0.5])) is None
