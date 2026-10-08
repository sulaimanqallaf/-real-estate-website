"""Scheduled challenger retraining + automatic rollback (GitHub Issue #1
P1). `python -m src.ml.retrain_scheduler` is meant to run on its own
schedule (weekly, per the issue) - a SEPARATE process from `src.main`'s
daily research run, exactly like `position_monitor.py` is separate from
it; see README "Running it fully autonomously" for the three-service
pattern this extends.

**This module can only ever do two things to a model's status: promote a
CHALLENGER to CHAMPION (via the EXISTING, unchanged `model_registry.
decide_promotion()` gate - never bypassed, never loosened here), or
demote/roll back an underperforming CHAMPION.** It has no import of, and
no path to, `circuit_breaker.py`, `execution_policy.py`, or any other
safety/risk-limit code - there is structurally nothing here that could
"adjust a safety limit or trading permission" even by accident. Rollback
criteria are a SIMPLE, deliberately conservative real-world canary (a
promoted model's own actual PAPER outcomes falling below a minimum win
rate over a real sample) - not a sophisticated statistical test; see
`check_for_champion_deterioration()`'s docstring for the exact limitation.
"""

from __future__ import annotations

import logging
from typing import Any

from . import decision_ledger
from . import models as ml_models
from . import model_registry, trainer

# A promoted model's real-world win rate (over its own resolved PAPER
# trades, attributed by model_id) must stay at or above this floor, once
# there is a large enough sample to judge it at all - below that is
# treated as visible, real harm, not noise.
MIN_TRADES_FOR_DETERIORATION_CHECK = 10
MIN_ACCEPTABLE_WIN_RATE = 0.30


def rollback_champion(registry: model_registry.ModelRegistry, model_id: str, reason: str, logger: logging.Logger) -> bool:
    """Demotes `model_id` (must currently be CHAMPION) to RETIRED, and -
    if an earlier RETIRED model exists for the SAME slot (task, target,
    horizon, model_type) - restores the most recently trained one as the
    new CHAMPION. If none exists, the slot is simply left with no
    CHAMPION at all (safe: `predictor.py` degrades to "Data Unavailable"
    for that model family, exactly like a fresh, never-trained slot).
    Returns False only if `model_id` was not actually a CHAMPION (nothing
    to roll back - never raises)."""
    meta = registry.read_metadata(model_id)
    if meta.status != model_registry.STATUS_CHAMPION:
        return False

    registry.set_status(model_id, model_registry.STATUS_RETIRED)
    logger.warning("Rolled back CHAMPION %s (%s): %s", model_id, meta.model_type, reason)

    previous_candidates = [
        m for m in registry.list_metadata(task=meta.task, target=meta.target, horizon=meta.horizon, status=model_registry.STATUS_RETIRED)
        if m.model_type == meta.model_type and m.model_id != model_id
    ]
    if previous_candidates:
        restored = max(previous_candidates, key=lambda m: m.trained_at)
        registry.set_status(restored.model_id, model_registry.STATUS_CHAMPION)
        logger.warning("Restored previous CHAMPION %s for the same slot.", restored.model_id)
    return True


def check_for_champion_deterioration(config: dict[str, Any], registry: model_registry.ModelRegistry, logger: logging.Logger) -> list[dict[str, Any]]:
    """Checks every current CHAMPION's OWN real-world PAPER outcomes
    (decision_ledger rows whose `model_id` includes this model - see
    `quant_agent.QuantAssessment.model_id`/`main._record_decision_
    snapshot`) and rolls one back if its realized win rate is below
    `MIN_ACCEPTABLE_WIN_RATE` over at least `MIN_TRADES_FOR_DETERIORATION_
    CHECK` resolved trades. Returns one entry per champion actually
    flagged (empty if none were).

    **Known limitation, stated plainly rather than overclaimed:** this is
    a simple realized-win-rate canary, not a rigorous statistical
    significance test against the model's own training-time metrics (that
    would need a materially larger resolved-trade sample than this system
    will have accumulated for a long while, and pretending otherwise would
    be worse than just saying so) - it exists to catch a model that is
    doing clear, sustained, real harm, not to fine-tune promotion
    decisions. A model with too few resolved trades to judge is left
    alone, never flagged on insufficient evidence."""
    db_path = decision_ledger.resolve_db_path(config)
    resolved_rows = decision_ledger.query_decisions(db_path, only_with_outcome=True)
    flagged: list[dict[str, Any]] = []

    for meta in registry.list_metadata(status=model_registry.STATUS_CHAMPION):
        matching = [r for r in resolved_rows if r.get("model_id") and meta.model_id in r["model_id"].split(",")]
        if len(matching) < MIN_TRADES_FOR_DETERIORATION_CHECK:
            continue
        wins = sum(1 for r in matching if (r.get("pnl_dollars") or 0) > 0)
        win_rate = wins / len(matching)
        if win_rate < MIN_ACCEPTABLE_WIN_RATE:
            reason = f"Real-world win rate {win_rate:.1%} over {len(matching)} resolved PAPER trades is below the {MIN_ACCEPTABLE_WIN_RATE:.0%} floor."
            rolled_back = rollback_champion(registry, meta.model_id, reason, logger)
            flagged.append({"model_id": meta.model_id, "model_type": meta.model_type, "reason": reason, "rolled_back": rolled_back, "win_rate": win_rate, "sample_size": len(matching)})

    return flagged


def run_scheduled_retraining(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    """The weekly retrain job: trains a fresh CHALLENGER for every
    (ticker's own dataset x model family), lets the EXISTING, unchanged
    `decide_promotion()` gate decide whether each one is promoted, then
    runs `check_for_champion_deterioration()`. Never raises - one
    ticker's data failure or one model's training failure is recorded in
    the summary and never stops the rest (same `safe_run` isolation
    discipline as every other batch-processing loop in this codebase)."""
    from .. import data_collector, dataset_builder
    from ..utils import resolve_path, safe_run

    registry = model_registry.ModelRegistry(resolve_path(config.get("ml", {}).get("registry_dir", "data/models")))
    universe = config.get("ml", {}).get("training_universe") or config.get("tickers", [])
    horizon = config.get("ml", {}).get("primary_horizon", 10)
    min_history_bars = config.get("dataset", {}).get("min_history_bars", 200)

    trained: list[dict[str, Any]] = []
    promoted: list[str] = []
    errors: list[dict[str, Any]] = []

    for ticker in universe:
        dataset = safe_run(
            logger, f"{ticker} dataset build",
            lambda t=ticker: dataset_builder.build_dataset_rows(t, data_collector.fetch_symbol_history(t, config, logger), config, min_history_bars=min_history_bars),
        )
        if dataset is None:
            errors.append({"ticker": ticker, "stage": "dataset_build"})
            continue

        for model_type in ml_models.ALL_MODEL_TYPES:
            result = safe_run(
                logger, f"{ticker} {model_type} retrain",
                lambda d=dataset, mt=model_type: trainer.train_challenger_and_maybe_promote(d, horizon, ml_models.TASK_CLASSIFICATION, mt, registry, config),
            )
            if result is None or not result.get("success", True):
                errors.append({"ticker": ticker, "model_type": model_type, "stage": "train"})
                continue
            trained.append({"ticker": ticker, "model_type": model_type, "model_id": result["metadata"].model_id, "promoted": result.get("promoted", False)})
            if result.get("promoted"):
                promoted.append(result["metadata"].model_id)

    rollbacks = safe_run(logger, "champion deterioration check", lambda: check_for_champion_deterioration(config, registry, logger)) or []

    logger.info(
        "Scheduled retraining complete: %d trained, %d promoted, %d errors, %d rolled back.",
        len(trained), len(promoted), len(errors), len(rollbacks),
    )
    return {"trained": trained, "promoted": promoted, "errors": errors, "rollbacks": rollbacks}


def main() -> int:
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="ml_retrain_scheduler.log")
    run_scheduled_retraining(config, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
