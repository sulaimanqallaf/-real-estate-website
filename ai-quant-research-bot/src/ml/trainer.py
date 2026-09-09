"""Offline training entry point (Phase 6 Part X).

**Training is deliberately separate from daily research/trading execution.**
`src/main.py` never imports this module and never calls anything in it - the
daily run only ever LOADS an explicitly registered CHAMPION model (see
`predictor.py`/`quant_agent.py`); no model is trained or changes mid-run.
Run this module directly, offline, whenever you want to (re)train:

    python -m src.ml.trainer --ticker AMD --horizon 10 --task classification --model-type gradient_boosting

Every training call runs the full pipeline: audit (refusing to proceed on
any CRITICAL issue - Part D) -> chronological train/validation/test split
(Part E) -> fit -> train/validation/test metrics + calibration (Part G) ->
overfit check (Part H) -> baseline comparison (Part I) -> walk-forward
evaluation (Part F) -> registry save as CHALLENGER -> champion/challenger
promotion decision (Part K). A failed or non-promoted challenger is never
discarded silently - it stays in the registry with its own status so its
result is inspectable later.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pandas as pd

from . import audit, calibration as ml_calibration
from . import features as ml_features
from . import labels as ml_labels
from . import model_registry
from . import models as ml_models
from . import splits as ml_splits
from . import validator


def train_and_evaluate(
    df: pd.DataFrame,
    horizon: int,
    task: str,
    model_type: str,
    config: dict[str, Any] | None = None,
    feature_names: list[str] | None = None,
    split_pcts: tuple[float, float, float] = (0.6, 0.2, 0.2),
    hyperparameters: dict[str, Any] | None = None,
    min_train_rows: int = 50,
) -> dict[str, Any]:
    """Runs the audit -> split -> fit -> evaluate -> baseline -> walk-forward
    pipeline and returns a full result bundle (NOT yet saved to the
    registry - see `train_and_register`). `result["success"] is False`
    whenever the audit found a critical issue or there isn't enough labeled
    data - the caller must not treat a failed result as a usable model."""
    names = feature_names or ml_features.FEATURE_WHITELIST

    report = audit.run_audit(df, horizon, config, names)
    if not report.passed:
        return {"success": False, "audit": report, "reason": "Audit found critical issue(s) - refusing to train.", "audit_summary": report.summary()}

    split = ml_splits.chronological_split(df, *split_pcts)
    train_known = ml_labels.rows_with_known_label(split.train, horizon)
    val_known = ml_labels.rows_with_known_label(split.validation, horizon)
    test_known = ml_labels.rows_with_known_label(split.test, horizon)

    if len(train_known) < min_train_rows:
        return {"success": False, "audit": report, "reason": f"Only {len(train_known)} labeled training rows - need >= {min_train_rows}."}

    feature_spec = ml_features.fit_feature_spec(train_known, names)
    spec = ml_models.build_model(model_type, task, names, hyperparameters)
    X_train = spec.prepare_X(train_known, feature_spec)

    calib = None
    top_bucket: dict[str, Any] = {}
    baselines: dict[str, Any] = {}
    comparison: dict[str, Any] = {}

    if task == ml_models.TASK_CLASSIFICATION:
        y_train = ml_labels.build_classification_target(train_known, horizon, config)
        if y_train.dropna().nunique() < 2:
            return {"success": False, "audit": report, "reason": "Training target has only one class - cannot train a classifier."}
        spec.fit(X_train, y_train.fillna(0.0))
        train_metrics = validator.compute_classification_metrics(y_train.to_numpy(), spec.predict_proba(X_train))

        val_metrics: dict[str, Any] = {}
        if len(val_known):
            X_val = spec.prepare_X(val_known, feature_spec)
            y_val = ml_labels.build_classification_target(val_known, horizon, config)
            val_proba = spec.predict_proba(X_val)
            val_metrics = validator.compute_classification_metrics(y_val.to_numpy(), val_proba)
            calib = ml_calibration.fit_calibration(val_proba, y_val.to_numpy())

        test_metrics: dict[str, Any] = {}
        if len(test_known):
            X_test = spec.prepare_X(test_known, feature_spec)
            y_test = ml_labels.build_classification_target(test_known, horizon, config)
            test_proba = spec.predict_proba(X_test)
            test_metrics = validator.compute_classification_metrics(y_test.to_numpy(), test_proba)
            test_returns = ml_labels.build_regression_target(test_known, horizon).to_numpy()
            top_bucket = validator.top_bucket_stats(test_returns, test_proba)

            majority = validator.majority_class_baseline_metrics(y_test.to_numpy())
            momentum_baseline = validator.momentum_direction_baseline(test_known, horizon, config)
            rule_baseline = validator.rule_score_baseline(test_known, horizon, "score", config) if "score" in test_known.columns else None
            baselines = {"majority_class": majority, "momentum_direction": momentum_baseline, "rule_score": rule_baseline}
            comparison = validator.compare_to_baselines(
                test_metrics.get("pr_auc"),
                {"momentum_direction": (momentum_baseline or {}).get("pr_auc"), "rule_score": (rule_baseline or {}).get("pr_auc")},
            )

        overfit_warning = validator.detect_overfit(train_metrics, val_metrics or test_metrics, "roc_auc") if (val_metrics or test_metrics) else None

    else:
        y_train = ml_labels.build_regression_target(train_known, horizon)
        spec.fit(X_train, y_train)
        train_metrics = validator.compute_regression_metrics(y_train.to_numpy(), spec.predict(X_train))

        val_metrics = {}
        if len(val_known):
            X_val = spec.prepare_X(val_known, feature_spec)
            y_val = ml_labels.build_regression_target(val_known, horizon)
            val_metrics = validator.compute_regression_metrics(y_val.to_numpy(), spec.predict(X_val))

        test_metrics = {}
        if len(test_known):
            X_test = spec.prepare_X(test_known, feature_spec)
            y_test = ml_labels.build_regression_target(test_known, horizon)
            test_preds = spec.predict(X_test)
            test_metrics = validator.compute_regression_metrics(y_test.to_numpy(), test_preds)
            top_bucket = validator.top_bucket_stats(y_test.to_numpy(), test_preds)

            zero_baseline = validator.zero_return_baseline_metrics(y_test.to_numpy())
            hist_avg_baseline = validator.historical_average_return_baseline_metrics(y_train.to_numpy(), y_test.to_numpy())
            baselines = {"zero_return": zero_baseline, "historical_average": hist_avg_baseline}
            ml_mae = test_metrics.get("mae")
            comparison = validator.compare_to_baselines(
                -ml_mae if ml_mae is not None else None,
                {"zero_return": -(zero_baseline.get("mae")) if zero_baseline.get("mae") is not None else None,
                 "historical_average": -(hist_avg_baseline.get("mae")) if hist_avg_baseline.get("mae") is not None else None},
            )

        overfit_warning = validator.detect_overfit(train_metrics, val_metrics or test_metrics, "r2") if (val_metrics or test_metrics) else None

    walk_forward = validator.run_walk_forward_evaluation(df, horizon, task, model_type, config, names)

    metrics_bundle = {
        "train": train_metrics,
        "validation": val_metrics,
        "test": test_metrics,
        "walk_forward": walk_forward.get("aggregated"),
        "top_bucket": top_bucket,
        "baselines": baselines,
        "baseline_comparison": comparison,
        "overfit_warning": overfit_warning,
    }

    return {
        "success": True,
        "model_spec": spec,
        "feature_spec": feature_spec,
        "calibration": calib,
        "metrics": metrics_bundle,
        "audit": report,
        "split": split,
        "feature_names": names,
    }


def train_and_register(
    df: pd.DataFrame,
    horizon: int,
    task: str,
    model_type: str,
    registry: model_registry.ModelRegistry,
    config: dict[str, Any] | None = None,
    feature_names: list[str] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    initial_status: str = model_registry.STATUS_EXPERIMENT,
) -> dict[str, Any]:
    result = train_and_evaluate(df, horizon, task, model_type, config, feature_names, hyperparameters=hyperparameters)
    if not result.get("success"):
        return result

    split = result["split"]
    hp = getattr(result["model_spec"].estimator, "get_params", lambda: {})()
    metadata = model_registry.build_metadata(
        model_type=model_type,
        task=task,
        target=task,
        horizon=horizon,
        train_start=split.train["timestamp"].min() if len(split.train) else None,
        train_end=split.train_end,
        validation_end=split.validation_end,
        test_end=split.test_end,
        feature_list=result["feature_names"],
        hyperparameters=hp,
        metrics=result["metrics"],
        dataset_fingerprint=model_registry.compute_dataset_fingerprint(df),
        status=initial_status,
    )
    bundle = {"model_spec": result["model_spec"], "feature_spec": result["feature_spec"], "calibration": result["calibration"]}
    registry.save_model(bundle, metadata)
    return {**result, "metadata": metadata}


def train_challenger_and_maybe_promote(
    df: pd.DataFrame,
    horizon: int,
    task: str,
    model_type: str,
    registry: model_registry.ModelRegistry,
    config: dict[str, Any] | None = None,
    feature_names: list[str] | None = None,
    hyperparameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full champion/challenger workflow (Part K): train as CHALLENGER,
    compare against the current CHAMPION (if any) using
    `model_registry.decide_promotion`, and only promote when that rule says
    to. A failed challenger, or one that loses the comparison, remains in
    the registry with its own status - never silently discarded, never
    force-promoted."""
    result = train_and_register(df, horizon, task, model_type, registry, config, feature_names, hyperparameters, initial_status=model_registry.STATUS_CHALLENGER)
    if not result.get("success"):
        return result

    champion = registry.get_champion(task, task, horizon, model_type)
    decision = model_registry.decide_promotion(result["metadata"], champion)
    result["promotion_decision"] = decision
    if decision["promote"]:
        registry.promote_to_champion(result["metadata"].model_id)
        result["promoted"] = True
    else:
        result["promoted"] = False
    return result


def _build_dataset_for_cli(ticker: str, config: dict[str, Any]) -> pd.DataFrame:
    from .. import data_collector, dataset_builder
    from ..utils import setup_logging

    logger = setup_logging(config, log_filename="ml_trainer.log")
    price_df = data_collector.fetch_symbol_history(ticker, config, logger)
    return dataset_builder.build_dataset_rows(ticker, price_df, config, min_history_bars=config.get("dataset", {}).get("min_history_bars", 200))


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Phase 6 ML training - never invoked from src.main.")
    parser.add_argument("--ticker", required=True, help="Ticker to build/fetch a dataset for, unless --dataset-path is given.")
    parser.add_argument("--horizon", type=int, default=10, choices=list(ml_labels.HORIZONS))
    parser.add_argument("--task", default=ml_models.TASK_CLASSIFICATION, choices=[ml_models.TASK_CLASSIFICATION, ml_models.TASK_REGRESSION])
    parser.add_argument("--model-type", default=ml_models.MODEL_GRADIENT_BOOSTING, choices=list(ml_models.ALL_MODEL_TYPES))
    parser.add_argument("--config", default=None)
    parser.add_argument("--dataset-path", default=None, help="Existing Parquet dataset; if omitted, fetches fresh history and builds one via dataset_builder.")
    parser.add_argument("--registry-dir", default=None)
    args = parser.parse_args()

    from ..utils import load_config, resolve_path

    config = load_config(args.config)

    if args.dataset_path:
        df = pd.read_parquet(args.dataset_path)
    else:
        df = _build_dataset_for_cli(args.ticker, config)

    registry_dir = args.registry_dir or config.get("ml", {}).get("registry_dir", "data/models")
    registry = model_registry.ModelRegistry(resolve_path(registry_dir))

    result = train_challenger_and_maybe_promote(df, args.horizon, args.task, args.model_type, registry, config)

    summary = {k: v for k, v in result.items() if k not in ("model_spec", "feature_spec", "calibration", "split", "audit")}
    if "metadata" in summary:
        summary["metadata"] = summary["metadata"].to_dict()
    if "audit" in result:
        summary["audit_summary"] = result["audit"].summary()
    print(json.dumps(summary, indent=2, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
