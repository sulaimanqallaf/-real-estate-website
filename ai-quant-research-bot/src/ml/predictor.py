"""Prediction schema, confidence bands, and the conservative ensemble
(Phase 6 Parts M/N/O).

`MLPrediction` never claims a probability the underlying model/calibration
quality doesn't support - see `predict_for_ticker()`'s `data_quality`/
`warnings` handling and `UNAVAILABLE` confidence band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import calibration as ml_calibration
from . import features as ml_features
from . import models as ml_models

BAND_VERY_HIGH = "VERY_HIGH"
BAND_HIGH = "HIGH"
BAND_MEDIUM = "MEDIUM"
BAND_LOW = "LOW"
BAND_UNAVAILABLE = "UNAVAILABLE"

DATA_QUALITY_GOOD = "GOOD"
DATA_QUALITY_PARTIAL = "PARTIAL"
DATA_QUALITY_UNAVAILABLE = "UNAVAILABLE"

# Below this, a model's own historical validation/test quality is close
# enough to a coin flip that no PREDICTION from it should ever be labeled
# above LOW, no matter how "confident" that one prediction looks - Part N:
# bands must reflect more than an arbitrary probability cutoff.
MIN_HISTORICAL_ROC_AUC_FOR_HIGH_BANDS = 0.55


@dataclass(frozen=True)
class MLPrediction:
    ticker: str
    horizon: str
    expected_return: float | None
    raw_probability: float | None
    calibrated_probability: float | None
    confidence_band: str
    model_id: str | None
    data_quality: str
    warnings: list[str] = field(default_factory=list)
    model_agreement: float | None = None
    contributions: dict[str, float] = field(default_factory=dict)
    top_factors: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.confidence_band != BAND_UNAVAILABLE


def unavailable_prediction(ticker: str, horizon: str, reason: str) -> MLPrediction:
    return MLPrediction(
        ticker=ticker, horizon=horizon, expected_return=None, raw_probability=None,
        calibrated_probability=None, confidence_band=BAND_UNAVAILABLE, model_id=None,
        data_quality=DATA_QUALITY_UNAVAILABLE, warnings=[reason],
    )


def determine_confidence_band(
    calibrated_probability: float | None,
    historical_roc_auc: float | None,
    data_quality: str,
    model_agreement: float | None,
) -> str:
    """Combines the calibrated probability's distance from 0.5, the
    champion model's OWN historical validation/test quality, current data
    quality, and cross-model agreement - never a bare probability cutoff
    alone (Part N)."""
    if calibrated_probability is None or data_quality == DATA_QUALITY_UNAVAILABLE:
        return BAND_UNAVAILABLE

    if historical_roc_auc is not None and historical_roc_auc < MIN_HISTORICAL_ROC_AUC_FOR_HIGH_BANDS:
        return BAND_LOW

    strength = abs(calibrated_probability - 0.5) * 2.0  # 0..1
    if data_quality == DATA_QUALITY_PARTIAL:
        strength *= 0.7
    if model_agreement is not None and model_agreement < 0.6:
        strength *= 0.6

    if strength >= 0.5:
        return BAND_VERY_HIGH
    if strength >= 0.3:
        return BAND_HIGH
    if strength >= 0.15:
        return BAND_MEDIUM
    return BAND_LOW


def ensemble_predict(model_probabilities: dict[str, float | None], model_quality_weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Conservative ensemble (Part O): requires each model to be
    individually valid (drops `None`s rather than treating a missing model
    as 0.0), weights by validation quality when supplied, and reports
    dispersion so strong disagreement can lower confidence downstream -
    never blindly averages everything."""
    valid = {k: v for k, v in model_probabilities.items() if v is not None}
    if not valid:
        return {"ensemble_probability": None, "dispersion": None, "contributions": {}, "available": False, "num_models": 0}

    weights = {k: (model_quality_weights or {}).get(k, 1.0) for k in valid}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        weights = {k: 1.0 for k in valid}
        total_weight = float(len(valid))

    ensemble_probability = sum(valid[k] * weights[k] for k in valid) / total_weight
    dispersion = float(np.std(list(valid.values()))) if len(valid) > 1 else 0.0

    return {
        "ensemble_probability": float(ensemble_probability),
        "dispersion": dispersion,
        "contributions": dict(valid),
        "available": True,
        "num_models": len(valid),
    }


def agreement_from_dispersion(dispersion: float | None) -> float | None:
    """A simple, bounded [0, 1] "agreement" score from ensemble dispersion -
    1.0 means identical predictions, 0.0 means maximal (>=0.5 std)
    disagreement. Used by `determine_confidence_band`."""
    if dispersion is None:
        return None
    return max(0.0, 1.0 - min(dispersion, 0.5) / 0.5)


def top_factors_from_importances(
    importances: dict[str, float] | None, feature_row: pd.Series, top_n: int = 5
) -> list[str]:
    """A short, human-readable explanation: the top-N features by absolute
    importance/coefficient, signed by whether this specific row's value for
    that feature is above or below zero/its typical direction - a
    lightweight stand-in for a per-prediction explanation without SHAP (Part
    V explicitly allows skipping SHAP when it doesn't add clear value)."""
    if not importances:
        return []
    ranked = sorted(importances.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_n]
    factors = []
    for name, weight in ranked:
        value = feature_row.get(name)
        direction = "positive" if weight >= 0 else "negative"
        group = ml_features.feature_group_for(name) or "feature"
        factors.append(f"{direction} {name} ({group}, value={value if value is not None else 'N/A'})")
    return factors


def predict_for_ticker(
    ticker: str,
    feature_row: pd.DataFrame,
    registry: Any,
    horizon: int,
    config: dict[str, Any] | None = None,
) -> MLPrediction:
    """Top-level orchestration (Parts M/N/O): load every model family's
    current CHAMPION for `horizon` (classification for probability,
    regression for expected return), ensemble the classification
    probabilities across families, and package the result as one
    `MLPrediction`. Degrades gracefully to `unavailable_prediction()` at
    every step - a missing registry, no champion, or a missing feature never
    raises, it just narrows what the prediction can honestly claim."""
    from . import model_registry as ml_registry

    horizon_label = f"{horizon}d"
    missing = ml_features.missing_feature_columns(feature_row)
    if missing:
        return unavailable_prediction(ticker, horizon_label, f"Feature row is missing required columns: {', '.join(missing[:5])}.")

    classification_champions = registry.get_all_champions(ml_models.TASK_CLASSIFICATION, ml_models.TASK_CLASSIFICATION, horizon)
    if not classification_champions:
        return unavailable_prediction(ticker, horizon_label, f"No registered classification champion for the {horizon_label} horizon.")

    raw_probs: dict[str, float] = {}
    quality_weights: dict[str, float] = {}
    best_calibrated: dict[str, float] = {}
    best_historical_auc: float | None = None
    warnings: list[str] = []

    for model_type, meta in classification_champions.items():
        try:
            bundle, _ = registry.load_model(meta.model_id)
        except Exception as exc:  # noqa: BLE001 - a corrupt/missing artifact degrades, never crashes the run
            warnings.append(f"Could not load {model_type} champion: {exc}")
            continue

        prediction = predict_with_one_model(feature_row, bundle["model_spec"], bundle["feature_spec"], bundle.get("calibration"))
        raw_probs[model_type] = prediction["raw_probability"]
        if prediction.get("calibrated_probability") is not None:
            best_calibrated[model_type] = prediction["calibrated_probability"]

        test_metrics = meta.metrics.get("test") or {}
        auc = test_metrics.get("roc_auc")
        if auc is not None:
            quality_weights[model_type] = max(0.0, auc - 0.5)
            best_historical_auc = auc if best_historical_auc is None else max(best_historical_auc, auc)

    ensemble = ensemble_predict(raw_probs, quality_weights if any(quality_weights.values()) else None)
    if not ensemble["available"]:
        return unavailable_prediction(ticker, horizon_label, "No classification champion produced a usable probability.")

    calibrated_probability = float(np.mean(list(best_calibrated.values()))) if best_calibrated else None
    if calibrated_probability is None:
        warnings.append("Calibration unavailable - reporting raw (uncalibrated) probability only.")

    expected_return = None
    regression_champions = registry.get_all_champions(ml_models.TASK_REGRESSION, ml_models.TASK_REGRESSION, horizon)
    if regression_champions:
        # Prefer gradient boosting if available (handles NaN natively, and
        # tends to have the most stable point estimates), else any other
        # available family, deterministically first-by-model-type-name.
        model_type = ml_models.MODEL_GRADIENT_BOOSTING if ml_models.MODEL_GRADIENT_BOOSTING in regression_champions else sorted(regression_champions)[0]
        meta = regression_champions[model_type]
        try:
            bundle, _ = registry.load_model(meta.model_id)
            prediction = predict_with_one_model(feature_row, bundle["model_spec"], bundle["feature_spec"], None)
            expected_return = prediction.get("expected_return")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not load {model_type} regression champion: {exc}")

    agreement = agreement_from_dispersion(ensemble["dispersion"])
    data_quality = DATA_QUALITY_GOOD if len(raw_probs) == len(ml_models.ALL_MODEL_TYPES) else DATA_QUALITY_PARTIAL
    confidence_band = determine_confidence_band(
        calibrated_probability if calibrated_probability is not None else ensemble["ensemble_probability"],
        best_historical_auc, data_quality, agreement,
    )

    return MLPrediction(
        ticker=ticker, horizon=horizon_label, expected_return=expected_return,
        raw_probability=ensemble["ensemble_probability"], calibrated_probability=calibrated_probability,
        confidence_band=confidence_band, model_id=",".join(m.model_id for m in classification_champions.values()),
        data_quality=data_quality, warnings=warnings, model_agreement=agreement,
        contributions=ensemble["contributions"],
    )


def predict_with_one_model(
    feature_row: pd.DataFrame,
    model_spec: ml_models.ModelSpec,
    feature_spec: ml_features.FittedFeatureSpec,
    calibration: ml_calibration.CalibrationResult | None,
) -> dict[str, Any]:
    """One model's raw + calibrated probability (classification) or point
    estimate (regression) for a single-row feature DataFrame."""
    X = model_spec.prepare_X(feature_row, feature_spec)
    if model_spec.task == ml_models.TASK_CLASSIFICATION:
        raw = float(model_spec.predict_proba(X)[0])
        calibrated = None
        if calibration is not None and calibration.available:
            applied = ml_calibration.apply_calibration(calibration, np.array([raw]))
            calibrated = float(applied[0]) if applied is not None else None
        return {"raw_probability": raw, "calibrated_probability": calibrated}
    point_estimate = float(model_spec.predict(X)[0])
    return {"expected_return": point_estimate}
