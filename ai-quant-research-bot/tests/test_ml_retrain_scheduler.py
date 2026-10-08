"""GitHub Issue #1 P1 - scheduled retraining + automatic rollback.
Training itself is already covered in tests/test_ml_registry_trainer.py;
these tests focus on rollback_champion(), check_for_champion_
deterioration(), and run_scheduled_retraining()'s own orchestration
(mocking the actual per-ticker training so this stays fast).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import decision_ledger, model_registry, retrain_scheduler
from src.ml import models as ml_models

LOGGER = logging.getLogger("test")


def _fake_metadata(model_registry_module, **overrides):
    base = dict(
        model_id="m1", model_type=ml_models.MODEL_LOGISTIC_REGRESSION, task=ml_models.TASK_CLASSIFICATION,
        target=ml_models.TASK_CLASSIFICATION, horizon=5, trained_at="2026-01-01T00:00:00+00:00", train_start=None,
        train_end=None, validation_end=None, test_end=None, feature_list=[], hyperparameters={}, metrics={},
        code_version="v1", dataset_fingerprint="abc", status=model_registry_module.STATUS_CHAMPION,
    )
    base.update(overrides)
    return model_registry_module.ModelMetadata(**base)


def _registry(tmp_path):
    return model_registry.ModelRegistry(tmp_path / "models")


def _seed_champion(registry, **overrides) -> str:
    meta = _fake_metadata(model_registry, **overrides)
    registry.save_model({"dummy": "bundle"}, meta)
    return meta.model_id


# --- rollback_champion ----------------------------------------------------------------


def test_rollback_champion_demotes_and_restores_previous(tmp_path):
    registry = _registry(tmp_path)
    old_id = _seed_champion(registry, model_id="old", trained_at="2026-01-01T00:00:00+00:00", status=model_registry.STATUS_RETIRED)
    new_id = _seed_champion(registry, model_id="new", trained_at="2026-02-01T00:00:00+00:00", status=model_registry.STATUS_CHAMPION)

    rolled_back = retrain_scheduler.rollback_champion(registry, new_id, "simulated deterioration", LOGGER)

    assert rolled_back is True
    assert registry.read_metadata(new_id).status == model_registry.STATUS_RETIRED
    assert registry.read_metadata(old_id).status == model_registry.STATUS_CHAMPION


def test_rollback_champion_leaves_slot_empty_with_no_previous_champion(tmp_path):
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry)

    rolled_back = retrain_scheduler.rollback_champion(registry, model_id, "simulated deterioration", LOGGER)

    assert rolled_back is True
    assert registry.read_metadata(model_id).status == model_registry.STATUS_RETIRED
    assert registry.get_champion(ml_models.TASK_CLASSIFICATION, ml_models.TASK_CLASSIFICATION, 5) is None


def test_rollback_champion_returns_false_for_a_non_champion(tmp_path):
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry, status=model_registry.STATUS_CHALLENGER)

    rolled_back = retrain_scheduler.rollback_champion(registry, model_id, "simulated deterioration", LOGGER)

    assert rolled_back is False
    assert registry.read_metadata(model_id).status == model_registry.STATUS_CHALLENGER


def test_rollback_champion_only_restores_the_same_model_family(tmp_path):
    """A rolled-back Logistic Regression champion must never cause a
    Random Forest RETIRED model to be restored - the slot key is (task,
    target, horizon, model_type), never just (task, target, horizon)."""
    registry = _registry(tmp_path)
    _seed_champion(registry, model_id="old_rf", model_type=ml_models.MODEL_RANDOM_FOREST, status=model_registry.STATUS_RETIRED)
    lr_id = _seed_champion(registry, model_id="new_lr", model_type=ml_models.MODEL_LOGISTIC_REGRESSION, status=model_registry.STATUS_CHAMPION)

    retrain_scheduler.rollback_champion(registry, lr_id, "simulated deterioration", LOGGER)

    assert registry.read_metadata("old_rf").status == model_registry.STATUS_RETIRED  # untouched
    assert registry.get_champion(ml_models.TASK_CLASSIFICATION, ml_models.TASK_CLASSIFICATION, 5, ml_models.MODEL_LOGISTIC_REGRESSION) is None


# --- check_for_champion_deterioration ---------------------------------------------------


def _config_with_ledger(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def test_deterioration_check_ignores_a_champion_with_too_few_resolved_trades(tmp_path):
    config = _config_with_ledger(tmp_path)
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry)

    db_path = decision_ledger.resolve_db_path(config)
    for i in range(5):  # below MIN_TRADES_FOR_DETERIORATION_CHECK
        decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", model_id=model_id, trade_id=f"t{i}")
        decision_ledger.record_outcome(db_path, trade_id=f"t{i}", outcome_status="STOPPED", pnl_dollars=-10.0)

    flagged = retrain_scheduler.check_for_champion_deterioration(config, registry, LOGGER)

    assert flagged == []
    assert registry.read_metadata(model_id).status == model_registry.STATUS_CHAMPION  # untouched


def test_deterioration_check_rolls_back_a_champion_with_a_losing_record(tmp_path):
    config = _config_with_ledger(tmp_path)
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry)

    db_path = decision_ledger.resolve_db_path(config)
    for i in range(20):
        decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", model_id=model_id, trade_id=f"t{i}")
        # Only 2/20 wins = 10% win rate, well below the 30% floor.
        pnl = 10.0 if i < 2 else -10.0
        decision_ledger.record_outcome(db_path, trade_id=f"t{i}", outcome_status="STOPPED", pnl_dollars=pnl)

    flagged = retrain_scheduler.check_for_champion_deterioration(config, registry, LOGGER)

    assert len(flagged) == 1
    assert flagged[0]["model_id"] == model_id
    assert flagged[0]["rolled_back"] is True
    assert registry.read_metadata(model_id).status == model_registry.STATUS_RETIRED


def test_deterioration_check_leaves_a_healthy_champion_alone(tmp_path):
    config = _config_with_ledger(tmp_path)
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry)

    db_path = decision_ledger.resolve_db_path(config)
    for i in range(20):
        decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", model_id=model_id, trade_id=f"t{i}")
        pnl = 10.0 if i < 12 else -10.0  # 60% win rate
        decision_ledger.record_outcome(db_path, trade_id=f"t{i}", outcome_status="STOPPED", pnl_dollars=pnl)

    flagged = retrain_scheduler.check_for_champion_deterioration(config, registry, LOGGER)

    assert flagged == []
    assert registry.read_metadata(model_id).status == model_registry.STATUS_CHAMPION


def test_deterioration_check_only_attributes_outcomes_to_their_own_model_id(tmp_path):
    """A different model's real outcomes must never count against this
    champion - model_id attribution must be exact, not 'any row at all'."""
    config = _config_with_ledger(tmp_path)
    registry = _registry(tmp_path)
    model_id = _seed_champion(registry)

    db_path = decision_ledger.resolve_db_path(config)
    for i in range(20):
        # A DIFFERENT model's losing trades - must not be attributed to model_id.
        decision_ledger.record_decision(db_path, ticker="MSFT", decision="AUTO_EXECUTE", model_id="some_other_model", trade_id=f"other{i}")
        decision_ledger.record_outcome(db_path, trade_id=f"other{i}", outcome_status="STOPPED", pnl_dollars=-10.0)

    flagged = retrain_scheduler.check_for_champion_deterioration(config, registry, LOGGER)

    assert flagged == []  # no resolved trades attributed to `model_id` at all


# --- run_scheduled_retraining: orchestration only (training itself mocked) ------------


def test_run_scheduled_retraining_records_errors_without_stopping_other_tickers(monkeypatch, tmp_path):
    config = {
        "tickers": ["AMD", "MSFT"],
        "ml": {"registry_dir": str(tmp_path / "models"), "primary_horizon": 10},
        "data": {"journal_dir": str(tmp_path / "journal")},
        "dataset": {"min_history_bars": 5},
    }

    def fake_fetch(ticker, cfg, logger):
        if ticker == "AMD":
            raise RuntimeError("simulated: fetch failed for AMD")
        import pandas as pd

        return pd.DataFrame({"close": [1.0] * 10})

    monkeypatch.setattr("src.data_collector.fetch_symbol_history", fake_fetch)
    monkeypatch.setattr("src.dataset_builder.build_dataset_rows", lambda ticker, df, cfg, min_history_bars: df)

    def fake_train(dataset, horizon, task, model_type, registry, cfg):
        return {"success": True, "promoted": False, "metadata": _fake_metadata(model_registry, model_id=f"m_{model_type}", status=model_registry.STATUS_CHALLENGER)}

    monkeypatch.setattr("src.ml.trainer.train_challenger_and_maybe_promote", fake_train)

    summary = retrain_scheduler.run_scheduled_retraining(config, LOGGER)

    assert any(e["ticker"] == "AMD" for e in summary["errors"])
    assert any(t["ticker"] == "MSFT" for t in summary["trained"])
    assert len(summary["trained"]) == len(ml_models.ALL_MODEL_TYPES)  # one per model family, for MSFT only


def test_run_scheduled_retraining_tracks_promotions(monkeypatch, tmp_path):
    config = {
        "tickers": ["AMD"],
        "ml": {"registry_dir": str(tmp_path / "models"), "primary_horizon": 10},
        "data": {"journal_dir": str(tmp_path / "journal")},
        "dataset": {"min_history_bars": 5},
    }

    import pandas as pd

    monkeypatch.setattr("src.data_collector.fetch_symbol_history", lambda ticker, cfg, logger: pd.DataFrame({"close": [1.0] * 10}))
    monkeypatch.setattr("src.dataset_builder.build_dataset_rows", lambda ticker, df, cfg, min_history_bars: df)

    def fake_train(dataset, horizon, task, model_type, registry, cfg):
        return {"success": True, "promoted": True, "metadata": _fake_metadata(model_registry, model_id=f"m_{model_type}", status=model_registry.STATUS_CHAMPION)}

    monkeypatch.setattr("src.ml.trainer.train_challenger_and_maybe_promote", fake_train)

    summary = retrain_scheduler.run_scheduled_retraining(config, LOGGER)

    assert len(summary["promoted"]) == len(ml_models.ALL_MODEL_TYPES)
    assert summary["errors"] == []
