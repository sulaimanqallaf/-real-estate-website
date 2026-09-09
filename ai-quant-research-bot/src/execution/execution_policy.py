"""Autonomous execution policy (Phase 7 Part D). Classifies an
already-fully-gated candidate (already survived label/individual risk/
regime/portfolio risk/Quant Agent) into exactly one of:

- `AUTO_EXECUTE`: eligible to skip Telegram approval and go straight to the
  broker, IF `autonomous_paper.enabled` AND `auto_execute.enabled` are BOTH
  explicitly true (both default `false` - see module-level constants and
  `config/settings.yaml`'s comments). No candidate auto-executes merely
  because this module exists.
- `REQUIRE_APPROVAL`: routes to the existing Telegram approval flow, same
  as every prior phase.
- `WATCH_ONLY`: informational, matches the existing High Risk Dip
  Watchlist behavior - never a trade of any kind.
- `REJECT`: already rejected by an earlier stage; this module never
  reverses that, it only reports it.

**Hard invariant**: this module only ever READS `entry["label"]`/
`entry["best_risk_result"]`/`entry["regime_evaluation"]`/
`entry["portfolio_evaluation"]`/`entry["quant_assessment"]` - exactly like
`quant_agent.py` - and can only classify a candidate as MORE cautious
(REQUIRE_APPROVAL or REJECT) than what those upstream gates already decided,
never less. An Avoid/risk-rejected/regime-blocked/portfolio-rejected
candidate can only ever come out as `REJECT` here, regardless of how good
its ML/Big Money context looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DECISION_AUTO_EXECUTE = "AUTO_EXECUTE"
DECISION_REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
DECISION_WATCH_ONLY = "WATCH_ONLY"
DECISION_REJECT = "REJECT"

_CONFIDENCE_RANK = {"UNAVAILABLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}

REASON_POSITION_TOO_SMALL = "POSITION_TOO_SMALL"


def compute_broker_constrained_quantity(
    candidate_quantity: int, entry_price: float, broker_available_funds: float | None, config: dict[str, Any]
) -> tuple[int, str | None]:
    """Part W: the final order quantity is `min(candidate_quantity,
    broker_account_risk_quantity)` - NEVER `max()`. Broker-reported
    available funds can only ever SHRINK what the regime/portfolio-adjusted
    candidate already proposed, never grow it. `broker_available_funds=None`
    (e.g. DRY_RUN, or a broker call that hasn't happened yet) passes the
    candidate quantity through unconstrained - it never fabricates a
    tighter number just because the broker hasn't answered yet.

    Part X: if the broker-constrained size rounds down to zero shares,
    returns `POSITION_TOO_SMALL` rather than forcing a trade or reaching
    for leverage to make one share affordable."""
    if broker_available_funds is None:
        final_quantity = candidate_quantity
    else:
        max_affordable = int(broker_available_funds // entry_price) if entry_price > 0 else 0
        final_quantity = min(candidate_quantity, max_affordable)

    if final_quantity < 1:
        return 0, REASON_POSITION_TOO_SMALL
    return final_quantity, None


@dataclass(frozen=True)
class ExecutionDecision:
    ticker: str
    decision: str
    reasons: list[str] = field(default_factory=list)


def _is_rejected_upstream(entry: dict[str, Any]) -> bool:
    risk = entry.get("best_risk_result")
    regime_eval = entry.get("regime_evaluation")
    portfolio_eval = entry.get("portfolio_evaluation")
    return (
        entry.get("label") == "Avoid"
        or not risk
        or not risk.get("tradeable")
        or (regime_eval is not None and regime_eval.get("blocked"))
        or (portfolio_eval is not None and portfolio_eval.get("decision") == "REJECT")
    )


def classify_candidate(entry: dict[str, Any], config: dict[str, Any]) -> ExecutionDecision:
    ticker = entry.get("symbol", "")
    autonomous_cfg = config.get("autonomous_paper", {})

    if _is_rejected_upstream(entry):
        return ExecutionDecision(ticker, DECISION_REJECT, ["Already rejected by an upstream deterministic gate (label/individual risk/regime/portfolio risk)."])

    portfolio_eval = entry.get("portfolio_evaluation")
    if portfolio_eval is None:
        # Never ran through the regime/portfolio pipeline at all - can't be
        # auto-executed or even approval-routed without that context.
        return ExecutionDecision(ticker, DECISION_REJECT, ["No portfolio_evaluation attached - candidate never completed the required pipeline."])

    autonomous_enabled = autonomous_cfg.get("enabled", False)
    auto_execute_cfg = autonomous_cfg.get("auto_execute", {})
    auto_execute_enabled = auto_execute_cfg.get("enabled", False)

    if not autonomous_enabled or not auto_execute_enabled:
        return ExecutionDecision(ticker, DECISION_REQUIRE_APPROVAL, ["Autonomous execution is disabled by config - routing to Telegram approval (the safe default)."])

    reasons = []
    meets_auto_bar = True

    allowed_confidence = set(auto_execute_cfg.get("allowed_confidence", ["VERY_HIGH"]))
    assessment = entry.get("quant_assessment")
    ml_confidence = assessment.ml_confidence if assessment else "UNAVAILABLE"
    if ml_confidence not in allowed_confidence:
        meets_auto_bar = False
        reasons.append(f"ML confidence '{ml_confidence}' is not in allowed_confidence {sorted(allowed_confidence)}.")

    min_score = auto_execute_cfg.get("minimum_signal_score", 85)
    if entry.get("score", 0) < min_score:
        meets_auto_bar = False
        reasons.append(f"signal score {entry.get('score', 0)} is below minimum_signal_score {min_score}.")

    min_rr = auto_execute_cfg.get("minimum_risk_reward", 2.0)
    position = portfolio_eval.get("position") or {}
    risk_reward = position.get("risk_reward")
    if risk_reward is None or risk_reward < min_rr:
        meets_auto_bar = False
        reasons.append(f"risk/reward {risk_reward} is below minimum_risk_reward {min_rr}.")

    if auto_execute_cfg.get("require_good_data_quality", True):
        # QuantAssessment has no standalone data_quality field - ML confidence
        # UNAVAILABLE is the practical proxy (predictor.py already folds data
        # quality into the confidence band itself).
        if ml_confidence == "UNAVAILABLE":
            meets_auto_bar = False
            reasons.append("ML data quality requirement not met (ML confidence is UNAVAILABLE).")

    if auto_execute_cfg.get("require_strategy_edge", False):
        strategy_edge = assessment.strategy_edge if assessment else "UNKNOWN"
        if strategy_edge != "POSITIVE":
            meets_auto_bar = False
            reasons.append(f"require_strategy_edge is true but strategy_edge is '{strategy_edge}', not POSITIVE.")

    if portfolio_eval.get("decision") == "ACCEPT_WITH_REDUCED_SIZE" and not auto_execute_cfg.get("allow_reduced_size", True):
        meets_auto_bar = False
        reasons.append("candidate was size-reduced by portfolio risk and allow_reduced_size is false.")

    if meets_auto_bar:
        return ExecutionDecision(ticker, DECISION_AUTO_EXECUTE, ["Meets every configured AUTO_EXECUTE criterion."])

    return ExecutionDecision(ticker, DECISION_REQUIRE_APPROVAL, reasons or ["Did not meet the AUTO_EXECUTE bar - routing to Telegram approval."])
