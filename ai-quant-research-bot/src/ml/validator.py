"""Metrics, overfit detection, baseline comparison, and walk-forward
orchestration (Phase 6 Parts F/H/I).

"The model is useful only if stronger predictions correspond to better
realized outcomes" (Part F) - `decile_analysis()` and `top_bucket_stats()`
exist specifically to check that, not just abstract classification/regression
accuracy.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)

from . import labels as ml_labels
from . import models as ml_models

OVERFIT_WARNING = "OVERFIT_WARNING"

CLASSIFICATION_METRIC_NAMES = ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "brier_score"]
REGRESSION_METRIC_NAMES = ["mae", "rmse", "r2", "directional_accuracy", "rank_correlation"]


def _valid_pairs(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    mask = ~np.isnan(a) & ~np.isnan(b)
    return a[mask], b[mask]


def compute_classification_metrics(y_true: np.ndarray, y_pred_proba: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    y_true_v, proba_v = _valid_pairs(y_true, y_pred_proba)
    metrics: dict[str, Any] = {"sample_size": int(len(y_true_v))}
    if len(y_true_v) == 0:
        metrics.update({name: None for name in CLASSIFICATION_METRIC_NAMES})
        return metrics

    y_pred = (proba_v >= threshold).astype(float)
    metrics["accuracy"] = float(accuracy_score(y_true_v, y_pred))
    metrics["precision"] = float(precision_score(y_true_v, y_pred, zero_division=0))
    metrics["recall"] = float(recall_score(y_true_v, y_pred, zero_division=0))
    metrics["f1"] = float(f1_score(y_true_v, y_pred, zero_division=0))

    if len(np.unique(y_true_v)) >= 2:
        metrics["roc_auc"] = float(roc_auc_score(y_true_v, proba_v))
        metrics["pr_auc"] = float(average_precision_score(y_true_v, proba_v))
        metrics["brier_score"] = float(brier_score_loss(y_true_v, proba_v))
    else:
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
        metrics["brier_score"] = None
    return metrics


def compute_regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true_v, y_pred_v = _valid_pairs(y_true, y_pred)
    metrics: dict[str, Any] = {"sample_size": int(len(y_true_v))}
    if len(y_true_v) < 2:
        metrics.update({name: None for name in REGRESSION_METRIC_NAMES})
        return metrics

    metrics["mae"] = float(mean_absolute_error(y_true_v, y_pred_v))
    metrics["rmse"] = float(mean_squared_error(y_true_v, y_pred_v) ** 0.5)
    metrics["r2"] = float(r2_score(y_true_v, y_pred_v)) if len(y_true_v) >= 2 else None
    metrics["directional_accuracy"] = float(np.mean(np.sign(y_true_v) == np.sign(y_pred_v)))

    try:
        from scipy.stats import spearmanr

        result = spearmanr(y_true_v, y_pred_v)
        corr = result.correlation if hasattr(result, "correlation") else result[0]
        metrics["rank_correlation"] = float(corr) if corr == corr else None
    except Exception:  # noqa: BLE001 - rank correlation is a nice-to-have, never fatal
        metrics["rank_correlation"] = None
    return metrics


def decile_analysis(realized_returns: np.ndarray, prediction_scores: np.ndarray, n_deciles: int = 10) -> list[dict[str, Any]]:
    """Average realized forward return grouped by prediction-score decile
    (decile 9 = the model's most bullish predictions). A useful model shows
    a roughly monotonic increase in average return from decile 0 to decile
    `n_deciles - 1`; a useless one shows no pattern - this function just
    reports the numbers, `trainer.py`/README explain how to read them."""
    returns_v, scores_v = _valid_pairs(realized_returns, prediction_scores)
    if len(returns_v) < n_deciles * 2:
        return []
    frame = pd.DataFrame({"score": scores_v, "return": returns_v})
    try:
        frame["decile"] = pd.qcut(frame["score"], n_deciles, labels=False, duplicates="drop")
    except ValueError:
        return []
    grouped = frame.groupby("decile")["return"].agg(["mean", "count"]).reset_index()
    return [{"decile": int(r["decile"]), "avg_return": float(r["mean"]), "count": int(r["count"])} for _, r in grouped.iterrows()]


def top_bucket_stats(realized_returns: np.ndarray, prediction_scores: np.ndarray, top_frac: float = 0.1, success_threshold: float = 0.0) -> dict[str, Any]:
    """Hit rate and expected forward return for the top-`top_frac` most
    confident predictions - the trading-relevant question "if I only ever
    acted on the model's best ideas, how often would they have worked."""
    returns_v, scores_v = _valid_pairs(realized_returns, prediction_scores)
    n = len(returns_v)
    if n < 10:
        return {"available": False, "reason": f"only {n} samples - need >= 10."}

    top_n = max(1, int(np.ceil(n * top_frac)))
    order = np.argsort(scores_v)[::-1][:top_n]
    top_returns = returns_v[order]
    hit_rate = float(np.mean(top_returns > success_threshold))
    return {
        "available": True,
        "bucket_size": int(top_n),
        "hit_rate": hit_rate,
        "expected_return": float(np.mean(top_returns)),
    }


def detect_overfit(train_metrics: dict[str, Any], validation_metrics: dict[str, Any], metric_name: str, max_relative_degradation: float = 0.15) -> str | None:
    """`OVERFIT_WARNING` when the training metric materially exceeds the
    validation metric (Part H: "training performance materially exceeds
    validation/test"). Only fires when both values exist and are on a
    "higher is better" scale (roc_auc, pr_auc, accuracy, r2, ...) - callers
    pick `metric_name` accordingly."""
    train_val = train_metrics.get(metric_name)
    val_val = validation_metrics.get(metric_name)
    if train_val is None or val_val is None or train_val <= 0:
        return None
    degradation = (train_val - val_val) / abs(train_val)
    return OVERFIT_WARNING if degradation > max_relative_degradation else None


# --- baselines (Part I) -------------------------------------------------------------


def majority_class_baseline_metrics(y_true: np.ndarray) -> dict[str, Any]:
    y_true_v = np.asarray(y_true, dtype=float)
    y_true_v = y_true_v[~np.isnan(y_true_v)]
    if len(y_true_v) == 0:
        return {"sample_size": 0, "accuracy": None}
    majority = 1.0 if y_true_v.mean() >= 0.5 else 0.0
    predicted = np.full_like(y_true_v, majority)
    return {"sample_size": int(len(y_true_v)), "accuracy": float(np.mean(predicted == y_true_v)), "majority_class": majority}


def momentum_direction_baseline(df: pd.DataFrame, horizon: int, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """A simple, non-ML baseline: predict "success" whenever 20D momentum is
    already positive. Classification ML must beat this to be worth using."""
    target = ml_labels.build_classification_target(df, horizon, config)
    momentum_positive = (pd.to_numeric(df["momentum_20d"], errors="coerce") > 0).astype(float)
    return compute_classification_metrics(target.to_numpy(), momentum_positive.to_numpy())


def rule_score_baseline(df: pd.DataFrame, horizon: int, score_column: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The EXISTING rule-based signal score (0-100), rescaled to [0, 1] and
    used directly as a "probability" - the baseline ML has to beat to add
    value over what this codebase already computes without any ML at all."""
    if score_column not in df.columns:
        return None
    target = ml_labels.build_classification_target(df, horizon, config)
    score = pd.to_numeric(df[score_column], errors="coerce") / 100.0
    return compute_classification_metrics(target.to_numpy(), score.to_numpy())


def zero_return_baseline_metrics(y_true_return: np.ndarray) -> dict[str, Any]:
    return compute_regression_metrics(y_true_return, np.zeros_like(np.asarray(y_true_return, dtype=float)))


def historical_average_return_baseline_metrics(train_returns: np.ndarray, test_returns: np.ndarray) -> dict[str, Any]:
    train_v = np.asarray(train_returns, dtype=float)
    train_v = train_v[~np.isnan(train_v)]
    avg = float(train_v.mean()) if len(train_v) else 0.0
    predicted = np.full_like(np.asarray(test_returns, dtype=float), avg)
    return compute_regression_metrics(test_returns, predicted)


def compare_to_baselines(ml_metric_value: float | None, baseline_metric_values: dict[str, float | None]) -> dict[str, Any]:
    """Transparent comparison - never claims incremental value the numbers
    don't support (Part I: "If it does not [add value]: say so explicitly")."""
    if ml_metric_value is None:
        return {"beats_all_baselines": False, "adds_incremental_value": False, "reason": "ML metric unavailable."}
    beats = {name: (val is None or ml_metric_value > val) for name, val in baseline_metric_values.items()}
    adds_value = all(beats.values()) if beats else False
    return {"beats_all_baselines": adds_value, "per_baseline": beats, "adds_incremental_value": adds_value}


# --- walk-forward orchestration (Part F) --------------------------------------------


def run_walk_forward_evaluation(
    df: pd.DataFrame,
    horizon: int,
    task: str,
    model_type: str,
    config: dict[str, Any] | None = None,
    feature_names: list[str] | None = None,
    min_train_rows: int = 200,
    validation_rows: int = 40,
    expanding: bool = True,
) -> dict[str, Any]:
    """Train a FRESH model on each fold's own training slice (never reusing
    a model fit on a later fold's data) and evaluate on that fold's
    validation slice, aggregating metrics across folds. This is the direct
    implementation of Part F's rolling/expanding walk-forward evaluation."""
    from . import features as ml_features
    from . import splits as ml_splits

    names = feature_names or ml_features.FEATURE_WHITELIST
    folds = ml_splits.walk_forward_folds(df, min_train_rows, validation_rows, expanding=expanding)
    if not folds:
        return {"folds": [], "aggregated": None, "reason": "not enough rows for even one walk-forward fold."}

    fold_results = []
    for fold in folds:
        train_known = ml_labels.rows_with_known_label(fold.train, horizon)
        val_known = ml_labels.rows_with_known_label(fold.validation, horizon)
        if len(train_known) < 20 or val_known.empty:
            continue

        feature_spec = ml_features.fit_feature_spec(train_known, names)
        spec = ml_models.build_model(model_type, task, names)
        X_train = spec.prepare_X(train_known, feature_spec)
        X_val = spec.prepare_X(val_known, feature_spec)

        if task == ml_models.TASK_CLASSIFICATION:
            y_train = ml_labels.build_classification_target(train_known, horizon, config)
            y_val = ml_labels.build_classification_target(val_known, horizon, config)
            if y_train.dropna().nunique() < 2:
                continue
            spec.fit(X_train, y_train.fillna(0.0))
            proba = spec.predict_proba(X_val)
            metrics = compute_classification_metrics(y_val.to_numpy(), proba)
        else:
            y_train = ml_labels.build_regression_target(train_known, horizon)
            y_val = ml_labels.build_regression_target(val_known, horizon)
            spec.fit(X_train, y_train)
            preds = spec.predict(X_val)
            metrics = compute_regression_metrics(y_val.to_numpy(), preds)

        fold_results.append({"fold_index": fold.fold_index, "validation_start": fold.validation_start, "validation_end": fold.validation_end, "metrics": metrics})

    if not fold_results:
        return {"folds": [], "aggregated": None, "reason": "no fold had enough labeled data to evaluate."}

    metric_names = CLASSIFICATION_METRIC_NAMES if task == ml_models.TASK_CLASSIFICATION else REGRESSION_METRIC_NAMES
    aggregated = {}
    for name in metric_names:
        values = [f["metrics"].get(name) for f in fold_results if f["metrics"].get(name) is not None]
        aggregated[name] = float(np.mean(values)) if values else None

    return {"folds": fold_results, "aggregated": aggregated}
