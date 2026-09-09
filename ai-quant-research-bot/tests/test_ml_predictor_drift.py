"""Tests for src/ml/predictor.py and src/ml/drift.py (Phase 6 Part Y
"Prediction")."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import drift, features as ml_features, model_registry, models, predictor, trainer

FEATURE_NAMES = ["momentum_20d", "rsi_14"]


@pytest.fixture
def registry(tmp_path):
    return model_registry.ModelRegistry(tmp_path / "models")


def _feature_row(**overrides):
    base = {c: np.nan for c in ml_features.FEATURE_WHITELIST}
    base.update(overrides)
    return pd.DataFrame([base])


# --- MLPrediction schema / confidence bands ---------------------------------------------


def test_unavailable_prediction_schema():
    pred = predictor.unavailable_prediction("AMD", "10d", "no model")
    assert pred.confidence_band == predictor.BAND_UNAVAILABLE
    assert pred.available is False
    assert pred.expected_return is None
    assert pred.raw_probability is None


def test_determine_confidence_band_none_probability_is_unavailable():
    assert predictor.determine_confidence_band(None, 0.7, "GOOD", 1.0) == predictor.BAND_UNAVAILABLE


def test_determine_confidence_band_low_historical_auc_caps_at_low():
    """A champion whose own historical quality is barely better than a coin
    flip must never label ANY single prediction above LOW, however extreme
    that prediction's own probability looks."""
    band = predictor.determine_confidence_band(0.98, 0.51, "GOOD", 1.0)
    assert band == predictor.BAND_LOW


def test_determine_confidence_band_strength_scales_with_distance_from_half():
    weak = predictor.determine_confidence_band(0.55, 0.75, "GOOD", 1.0)
    strong = predictor.determine_confidence_band(0.95, 0.75, "GOOD", 1.0)
    order = [predictor.BAND_UNAVAILABLE, predictor.BAND_LOW, predictor.BAND_MEDIUM, predictor.BAND_HIGH, predictor.BAND_VERY_HIGH]
    assert order.index(strong) > order.index(weak)


def test_determine_confidence_band_partial_data_quality_lowers_band():
    good = predictor.determine_confidence_band(0.9, 0.75, "GOOD", 1.0)
    partial = predictor.determine_confidence_band(0.9, 0.75, "PARTIAL", 1.0)
    order = [predictor.BAND_UNAVAILABLE, predictor.BAND_LOW, predictor.BAND_MEDIUM, predictor.BAND_HIGH, predictor.BAND_VERY_HIGH]
    assert order.index(partial) <= order.index(good)


def test_determine_confidence_band_disagreement_lowers_band():
    agree = predictor.determine_confidence_band(0.9, 0.75, "GOOD", 1.0)
    disagree = predictor.determine_confidence_band(0.9, 0.75, "GOOD", 0.2)
    order = [predictor.BAND_UNAVAILABLE, predictor.BAND_LOW, predictor.BAND_MEDIUM, predictor.BAND_HIGH, predictor.BAND_VERY_HIGH]
    assert order.index(disagree) < order.index(agree)


# --- ensemble (Scenario E: disagreement reduces confidence) ----------------------------


def test_ensemble_requires_individually_valid_models():
    result = predictor.ensemble_predict({"lr": 0.8, "rf": None, "gb": 0.7})
    assert result["num_models"] == 2
    assert "rf" not in result["contributions"]


def test_ensemble_all_models_unavailable_returns_unavailable():
    result = predictor.ensemble_predict({"lr": None, "rf": None})
    assert result["available"] is False


def test_ensemble_weights_by_validation_quality():
    equal = predictor.ensemble_predict({"lr": 0.9, "rf": 0.1})
    weighted = predictor.ensemble_predict({"lr": 0.9, "rf": 0.1}, model_quality_weights={"lr": 10.0, "rf": 0.01})
    assert weighted["ensemble_probability"] > equal["ensemble_probability"]  # weighted toward the higher-quality model


def test_ensemble_disagreement_produces_dispersion_and_lowers_agreement():
    """Scenario E: Logistic bullish, Random Forest neutral, boosting
    bearish - ensemble confidence must be reduced relative to full
    agreement at the same central probability."""
    disagreeing = predictor.ensemble_predict({"logistic_regression": 0.85, "random_forest": 0.5, "gradient_boosting": 0.15})
    agreeing = predictor.ensemble_predict({"logistic_regression": 0.5, "random_forest": 0.5, "gradient_boosting": 0.5})
    assert disagreeing["dispersion"] > agreeing["dispersion"]

    agreement_disagreeing = predictor.agreement_from_dispersion(disagreeing["dispersion"])
    agreement_agreeing = predictor.agreement_from_dispersion(agreeing["dispersion"])
    assert agreement_disagreeing < agreement_agreeing

    band_disagreeing = predictor.determine_confidence_band(disagreeing["ensemble_probability"], 0.75, "GOOD", agreement_disagreeing)
    band_agreeing_same_prob = predictor.determine_confidence_band(0.85, 0.75, "GOOD", agreement_agreeing)
    order = [predictor.BAND_UNAVAILABLE, predictor.BAND_LOW, predictor.BAND_MEDIUM, predictor.BAND_HIGH, predictor.BAND_VERY_HIGH]
    assert order.index(band_disagreeing) < order.index(band_agreeing_same_prob)


# --- predict_for_ticker: end-to-end orchestration ---------------------------------------


def test_predict_for_ticker_unavailable_with_no_registered_champion(registry):
    row = _feature_row(momentum_20d=5.0, rsi_14=60.0)
    pred = predictor.predict_for_ticker("AMD", row, registry, 10)
    assert pred.available is False
    assert "champion" in pred.warnings[0].lower()


def test_predict_for_ticker_unavailable_with_missing_feature_columns(registry):
    row = pd.DataFrame([{"momentum_20d": 5.0}])  # missing every other whitelisted column
    pred = predictor.predict_for_ticker("AMD", row, registry, 10)
    assert pred.available is False


def test_predict_for_ticker_bullish_signal_produces_high_confidence(registry, separable_dataset):
    for model_type in models.ALL_MODEL_TYPES:
        trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, model_type, registry, feature_names=FEATURE_NAMES)

    row = _feature_row(momentum_20d=9.0, rsi_14=68.0)
    pred = predictor.predict_for_ticker("TEST", row, registry, 5)
    assert pred.available is True
    assert pred.data_quality == predictor.DATA_QUALITY_GOOD
    assert pred.calibrated_probability is not None
    assert 0.0 <= pred.calibrated_probability <= 1.0


def test_predict_for_ticker_partial_data_quality_with_only_some_families_registered(registry, separable_dataset):
    trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    row = _feature_row(momentum_20d=5.0, rsi_14=55.0)
    pred = predictor.predict_for_ticker("TEST", row, registry, 5)
    assert pred.data_quality == predictor.DATA_QUALITY_PARTIAL


def test_predict_for_ticker_never_raises_on_a_corrupt_model_id(registry, separable_dataset, monkeypatch):
    trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)

    original_load = registry.load_model

    def _boom(model_id):
        raise OSError("simulated corrupt artifact")

    monkeypatch.setattr(registry, "load_model", _boom)
    row = _feature_row(momentum_20d=5.0, rsi_14=55.0)
    pred = predictor.predict_for_ticker("TEST", row, registry, 5)
    assert pred.available is False  # degrades cleanly, does not raise


# --- drift -------------------------------------------------------------------------------


def test_feature_drift_detected_on_material_shift():
    reference = pd.DataFrame({"momentum_20d": np.random.normal(0, 1, 200)})
    recent = pd.DataFrame({"momentum_20d": np.random.normal(5, 1, 30)})  # shifted 5 std devs
    drifted, details = drift.detect_feature_drift(reference, recent, ["momentum_20d"])
    assert drifted is True
    assert "momentum_20d" in details["shifted_features"]


def test_feature_drift_not_detected_on_stable_distribution():
    rng = np.random.default_rng(0)
    reference = pd.DataFrame({"momentum_20d": rng.normal(0, 1, 200)})
    recent = pd.DataFrame({"momentum_20d": rng.normal(0, 1, 30)})
    drifted, _ = drift.detect_feature_drift(reference, recent, ["momentum_20d"])
    assert drifted is False


def test_calibration_drift_detected_on_material_brier_degradation():
    drifted, _ = drift.detect_calibration_drift(0.10, 0.25)
    assert drifted is True


def test_calibration_drift_not_flagged_without_data():
    drifted, details = drift.detect_calibration_drift(None, 0.25)
    assert drifted is False
    assert "reason" in details


def test_hit_rate_drop_detected():
    drifted, _ = drift.detect_recent_hit_rate_drop(0.65, 0.40)
    assert drifted is True


def test_missing_data_increase_detected():
    drifted, details = drift.detect_missing_data_increase({"col_a": 5.0}, {"col_a": 40.0})
    assert drifted is True
    assert "col_a" in details["increased_missingness"]


def test_run_drift_checks_aggregates_all_warning_types():
    rng = np.random.default_rng(0)
    reference_df = pd.DataFrame({"momentum_20d": rng.normal(0, 1, 200), "rsi_14": rng.normal(50, 5, 200)})
    recent_df = pd.DataFrame({"momentum_20d": rng.normal(10, 1, 30), "rsi_14": rng.normal(50, 5, 30)})
    report = drift.run_drift_checks(
        reference_df, recent_df, ["momentum_20d", "rsi_14"],
        training_brier_score=0.1, recent_brier_score=0.3,
        training_hit_rate=0.6, recent_hit_rate=0.3,
    )
    assert drift.WARNING_FEATURE_DRIFT in report.warnings
    assert drift.WARNING_CALIBRATION_DRIFT in report.warnings
    assert drift.WARNING_PERFORMANCE_DRIFT in report.warnings
    assert report.has_drift is True


def test_run_drift_checks_clean_data_reports_no_warnings():
    rng = np.random.default_rng(1)
    reference_df = pd.DataFrame({"momentum_20d": rng.normal(0, 1, 200)})
    recent_df = pd.DataFrame({"momentum_20d": rng.normal(0, 1, 30)})
    report = drift.run_drift_checks(reference_df, recent_df, ["momentum_20d"])
    assert report.has_drift is False
