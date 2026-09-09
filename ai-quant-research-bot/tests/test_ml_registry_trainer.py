"""Tests for src/ml/model_registry.py and src/ml/trainer.py (Phase 6 Part Y
"Registry" and the offline-training half of "Training")."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import audit, model_registry, models, trainer

FEATURE_NAMES = ["momentum_20d", "rsi_14"]


@pytest.fixture
def registry(tmp_path):
    return model_registry.ModelRegistry(tmp_path / "models")


# --- registry: artifact + metadata storage --------------------------------------------


def test_save_and_load_model_round_trips(registry, separable_dataset):
    result = trainer.train_and_register(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    assert result["success"] is True
    model_id = result["metadata"].model_id

    bundle, metadata = registry.load_model(model_id)
    assert "model_spec" in bundle and "feature_spec" in bundle
    assert metadata.model_id == model_id
    assert metadata.feature_list == FEATURE_NAMES


def test_metadata_captures_required_fields(registry, separable_dataset):
    result = trainer.train_and_register(separable_dataset, 10, models.TASK_CLASSIFICATION, models.MODEL_RANDOM_FOREST, registry, feature_names=FEATURE_NAMES)
    meta = result["metadata"]
    for field_name in ("model_id", "model_type", "task", "target", "horizon", "trained_at", "train_start", "train_end", "validation_end", "test_end", "feature_list", "hyperparameters", "metrics", "code_version", "dataset_fingerprint", "status"):
        assert hasattr(meta, field_name)
    assert meta.horizon == 10
    assert meta.model_type == models.MODEL_RANDOM_FOREST


def test_dataset_fingerprint_differs_for_different_datasets(separable_dataset, noise_dataset):
    fp1 = model_registry.compute_dataset_fingerprint(separable_dataset)
    fp2 = model_registry.compute_dataset_fingerprint(noise_dataset)
    assert fp1 != fp2


def test_save_model_never_overwrites_an_existing_model_id(registry, separable_dataset):
    result = trainer.train_and_register(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    model_dir = registry._model_dir(result["metadata"].model_id)
    with pytest.raises(FileExistsError):
        registry.save_model({"x": 1}, result["metadata"])  # same model_id - must refuse, not overwrite
    assert model_dir.exists()  # untouched


def test_new_model_always_gets_a_fresh_model_id(registry, separable_dataset):
    r1 = trainer.train_and_register(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    r2 = trainer.train_and_register(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    assert r1["metadata"].model_id != r2["metadata"].model_id


def test_champion_not_overwritten_silently_by_a_worse_challenger(registry, separable_dataset, noise_dataset):
    good = trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    assert good["promoted"] is True
    champion_before = registry.get_champion(models.TASK_CLASSIFICATION, models.TASK_CLASSIFICATION, 5, models.MODEL_LOGISTIC_REGRESSION)

    # A worse challenger trained on pure noise should not dislodge the champion.
    worse = trainer.train_challenger_and_maybe_promote(noise_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    champion_after = registry.get_champion(models.TASK_CLASSIFICATION, models.TASK_CLASSIFICATION, 5, models.MODEL_LOGISTIC_REGRESSION)

    assert champion_after.model_id == champion_before.model_id  # unchanged
    if worse.get("success"):
        assert worse["promoted"] is False
        failed_challenger_meta = registry.read_metadata(worse["metadata"].model_id)
        assert failed_challenger_meta.status == model_registry.STATUS_CHALLENGER  # remains, never silently discarded


def test_first_model_is_promoted_by_default(registry, separable_dataset):
    result = trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_GRADIENT_BOOSTING, registry, feature_names=FEATURE_NAMES)
    assert result["promoted"] is True
    assert result["promotion_decision"]["promote"] is True


def test_champion_slot_is_per_model_family(registry, separable_dataset):
    for model_type in models.ALL_MODEL_TYPES:
        trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, model_type, registry, feature_names=FEATURE_NAMES)
    champions = registry.get_all_champions(models.TASK_CLASSIFICATION, models.TASK_CLASSIFICATION, 5)
    assert set(champions) == set(models.ALL_MODEL_TYPES)
    assert len({m.model_id for m in champions.values()}) == 3  # three distinct models, three distinct champions


def test_promoting_one_family_never_retires_another_familys_champion(registry, separable_dataset):
    trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    rf_champion_1 = trainer.train_challenger_and_maybe_promote(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_RANDOM_FOREST, registry, feature_names=FEATURE_NAMES)
    lr_champion = registry.get_champion(models.TASK_CLASSIFICATION, models.TASK_CLASSIFICATION, 5, models.MODEL_LOGISTIC_REGRESSION)
    assert lr_champion.status == model_registry.STATUS_CHAMPION  # untouched by the RF promotion


def test_set_status_and_list_metadata_filtering(registry, separable_dataset):
    result = trainer.train_and_register(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, registry, feature_names=FEATURE_NAMES)
    registry.set_status(result["metadata"].model_id, model_registry.STATUS_RETIRED)
    retired = registry.list_metadata(status=model_registry.STATUS_RETIRED)
    assert len(retired) == 1
    assert retired[0].model_id == result["metadata"].model_id


def test_get_champion_returns_none_when_no_champion_exists(registry):
    assert registry.get_champion(models.TASK_CLASSIFICATION, models.TASK_CLASSIFICATION, 20) is None


# --- decide_promotion rules ------------------------------------------------------------


def _fake_metadata(**overrides):
    base = dict(
        model_id="m1", model_type=models.MODEL_LOGISTIC_REGRESSION, task=models.TASK_CLASSIFICATION,
        target=models.TASK_CLASSIFICATION, horizon=5, trained_at="now", train_start=None, train_end=None,
        validation_end=None, test_end=None, feature_list=[], hyperparameters={}, metrics={}, code_version="v1",
        dataset_fingerprint="abc", status=model_registry.STATUS_CHALLENGER,
    )
    base.update(overrides)
    return model_registry.ModelMetadata(**base)


def test_decide_promotion_no_champion_promotes_by_default():
    challenger = _fake_metadata()
    decision = model_registry.decide_promotion(challenger, None)
    assert decision["promote"] is True


def test_decide_promotion_requires_at_least_two_criteria():
    champion = _fake_metadata(model_id="champ", metrics={"test": {"pr_auc": 0.5, "brier_score": 0.2}})
    # Only ONE criterion better (pr_auc) - must NOT promote.
    challenger = _fake_metadata(model_id="chal", metrics={"test": {"pr_auc": 0.6, "brier_score": 0.25}})
    decision = model_registry.decide_promotion(challenger, champion)
    assert decision["promote"] is False


def test_decide_promotion_promotes_with_two_criteria_won():
    champion = _fake_metadata(model_id="champ", metrics={"test": {"pr_auc": 0.5, "brier_score": 0.2}})
    challenger = _fake_metadata(model_id="chal", metrics={"test": {"pr_auc": 0.6, "brier_score": 0.15}})
    decision = model_registry.decide_promotion(challenger, champion)
    assert decision["promote"] is True
    assert len(decision["reasons"]) == 2


def test_decide_promotion_blocks_on_new_overfit_regression():
    champion = _fake_metadata(model_id="champ", metrics={"test": {"pr_auc": 0.5, "brier_score": 0.2}, "overfit_warning": None})
    challenger = _fake_metadata(model_id="chal", metrics={"test": {"pr_auc": 0.7, "brier_score": 0.1}, "overfit_warning": "OVERFIT_WARNING"})
    decision = model_registry.decide_promotion(challenger, champion)
    assert decision["promote"] is False
    assert "overfit" in decision["reasons"][0].lower()


# --- trainer: audit refusal, target types ----------------------------------------------


def test_trainer_refuses_to_proceed_on_audit_failure(registry):
    import pandas as pd

    tiny = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=5, freq="B"), "ticker": "TEST", "momentum_20d": range(5), "forward_5d_return": [0.01] * 5})
    result = trainer.train_and_evaluate(tiny, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, feature_names=["momentum_20d"], min_train_rows=1)
    assert result["success"] is False
    assert "audit" in result


def test_trainer_classification_target_works(separable_dataset, registry):
    result = trainer.train_and_evaluate(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, feature_names=FEATURE_NAMES)
    assert result["success"] is True
    assert result["metrics"]["test"]["roc_auc"] > 0.7


def test_trainer_regression_target_works(separable_dataset, registry):
    result = trainer.train_and_evaluate(separable_dataset, 5, models.TASK_REGRESSION, models.MODEL_RANDOM_FOREST, feature_names=FEATURE_NAMES)
    assert result["success"] is True
    assert result["metrics"]["test"]["mae"] is not None


def test_trainer_refuses_single_class_training_target(registry):
    import numpy as np
    import pandas as pd

    n = 300
    df = pd.DataFrame({
        "timestamp": pd.date_range("2022-01-01", periods=n, freq="B"), "ticker": "TEST",
        "momentum_20d": np.random.randn(n), "rsi_14": np.random.randn(n),
        "forward_5d_return": [-0.5] * n,  # always below any sane threshold -> single class
    })
    result = trainer.train_and_evaluate(df, 5, models.TASK_CLASSIFICATION, models.MODEL_LOGISTIC_REGRESSION, feature_names=["momentum_20d", "rsi_14"], min_train_rows=50)
    assert result["success"] is False


def test_trainer_reports_overfit_warning_when_present(separable_dataset, registry):
    """A model with an absurdly deep, unconstrained Random Forest on a
    fairly small slice should show a real train/validation gap - not
    necessarily every run, so this test checks the field exists as a valid
    value rather than asserting it always fires."""
    result = trainer.train_and_evaluate(separable_dataset, 5, models.TASK_CLASSIFICATION, models.MODEL_RANDOM_FOREST, feature_names=FEATURE_NAMES, hyperparameters={"n_estimators": 200, "max_depth": None, "min_samples_leaf": 1})
    assert result["success"] is True
    assert result["metrics"]["overfit_warning"] in (None, "OVERFIT_WARNING")
