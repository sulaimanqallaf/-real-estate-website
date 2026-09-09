"""Quant Agent (Phase 6 Part P) - NOT an LLM personality. A plain,
deterministic orchestration layer that combines the rule-based signal score,
market regime fit, Big Money context, an ML prediction (when available), and
strategy performance memory into one structured `QuantAssessment` - report/
ranking context, same as `big_money.py`'s role in Phase 5.

**Hard invariant, enforced structurally, not just documented:**
`assess_candidate()` never writes to `entry["label"]`,
`entry["best_risk_result"]`, `entry["regime_evaluation"]`, or
`entry["portfolio_evaluation"]` - it only READS them to decide what to
report. If any of those already show a rejection (Avoid label, individual
risk not tradeable, regime block, or portfolio REJECT), the assessment's
`decision` is `NOT_ELIGIBLE` and says so - it has no code path back into
those fields to change the outcome. See `apply_quant_agent_filtering()` for
the one OPTIONAL, explicitly-configured exception: adding a NEW rejection on
top of an already-otherwise-eligible candidate when the ML signal is both
negative and highly confident - never removing an existing one, never
touching size, never touching label/regime/individual-risk state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ml import predictor as ml_predictor
from .strategy_memory import StrategyEdge

DECISION_ELIGIBLE = "ELIGIBLE"
DECISION_WEAK = "WEAK"
DECISION_NOT_ELIGIBLE = "NOT_ELIGIBLE"

REGIME_FIT_STRONG = "STRONG"
REGIME_FIT_NEUTRAL = "NEUTRAL"
REGIME_FIT_WEAK = "WEAK"
REGIME_FIT_BLOCKED = "BLOCKED"
REGIME_FIT_UNKNOWN = "UNKNOWN"

_CONFIDENCE_RANK = {
    ml_predictor.BAND_UNAVAILABLE: 0,
    ml_predictor.BAND_LOW: 1,
    ml_predictor.BAND_MEDIUM: 2,
    ml_predictor.BAND_HIGH: 3,
    ml_predictor.BAND_VERY_HIGH: 4,
}


@dataclass(frozen=True)
class QuantAssessment:
    ticker: str
    quant_score: float | None
    rule_score: int
    ml_confidence: str
    expected_return: float | None
    big_money_score: float | None
    regime_fit: str
    strategy_edge: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    calibrated_probability: float | None = None
    model_agreement: float | None = None
    horizon: str | None = None


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


def _regime_fit(entry: dict[str, Any]) -> str:
    regime_eval = entry.get("regime_evaluation")
    if regime_eval is None:
        return REGIME_FIT_UNKNOWN
    if regime_eval.get("blocked"):
        return REGIME_FIT_BLOCKED
    multiplier = regime_eval.get("combined_multiplier", 1.0)
    if multiplier >= 0.99:
        return REGIME_FIT_STRONG
    if multiplier < 0.5:
        return REGIME_FIT_WEAK
    return REGIME_FIT_NEUTRAL


def _rescale_big_money(big_money_score: Any) -> float | None:
    """-1..+1 -> 0..100, for display parity with rule_score/quant_score -
    None (never fabricated) if there's no composite to rescale."""
    if big_money_score is None or big_money_score.composite_score is None:
        return None
    return round((big_money_score.composite_score + 1.0) / 2.0 * 100.0, 1)


def assess_candidate(
    entry: dict[str, Any],
    ml_prediction: ml_predictor.MLPrediction | None,
    strategy_edge: StrategyEdge | None,
    config: dict[str, Any] | None = None,
) -> QuantAssessment:
    """Build one candidate's `QuantAssessment`. Purely additive/read-only
    with respect to `entry` - the caller decides separately (see
    `apply_quant_agent_filtering`) whether/how to attach this to `entry`."""
    reasons: list[str] = []
    warnings: list[str] = []

    rule_score = entry.get("score", 0)
    regime_fit = _regime_fit(entry)
    big_money_display = _rescale_big_money(entry.get("big_money_score"))

    ml_confidence = ml_prediction.confidence_band if ml_prediction is not None else ml_predictor.BAND_UNAVAILABLE
    expected_return = ml_prediction.expected_return if ml_prediction is not None else None
    if ml_prediction is None or not ml_prediction.available:
        warnings.append("ML: Data Unavailable.")

    if strategy_edge is None:
        strategy_edge_label = "UNKNOWN"
    elif not strategy_edge.has_sufficient_sample:
        strategy_edge_label = "INSUFFICIENT_SAMPLE"
        warnings.append(f"Strategy performance memory: insufficient sample size (n={strategy_edge.sample_size}) to claim an edge.")
    else:
        strategy_edge_label = strategy_edge.edge_direction
        reasons.append(
            f"Strategy historical edge for '{strategy_edge.group_key}': {strategy_edge_label.lower()} "
            f"(n={strategy_edge.sample_size}, win rate {strategy_edge.win_rate_pct:.1f}%)."
        )

    calibrated_probability = ml_prediction.calibrated_probability if ml_prediction is not None else None
    model_agreement = ml_prediction.model_agreement if ml_prediction is not None else None
    horizon = ml_prediction.horizon if ml_prediction is not None else None

    if _is_rejected_upstream(entry):
        return QuantAssessment(
            ticker=entry.get("symbol", ""), quant_score=None, rule_score=rule_score, ml_confidence=ml_confidence,
            expected_return=expected_return, big_money_score=big_money_display, regime_fit=regime_fit,
            strategy_edge=strategy_edge_label, decision=DECISION_NOT_ELIGIBLE,
            reasons=["Upstream deterministic gate already rejected this candidate (label/individual risk/regime/portfolio risk) - the Quant Agent cannot override this."] + reasons,
            warnings=warnings, calibrated_probability=calibrated_probability, model_agreement=model_agreement, horizon=horizon,
        )

    reasons.append(f"Rule-based signal score: {rule_score}/100.")
    if ml_prediction is not None and ml_prediction.available:
        if expected_return is not None:
            reasons.append(f"ML confidence: {ml_confidence}, expected return {expected_return:+.2%}.")
        else:
            reasons.append(f"ML confidence: {ml_confidence}.")

    components = [rule_score]
    weights = [1.0]
    if ml_prediction is not None and ml_prediction.available and expected_return is not None:
        ml_component = 50.0 + max(-50.0, min(50.0, expected_return * 1000.0))
        components.append(ml_component)
        weights.append(0.5)
    if big_money_display is not None:
        components.append(big_money_display)
        weights.append(0.3)

    quant_score = round(min(100.0, sum(c * w for c, w in zip(components, weights)) / sum(weights)), 1)

    min_eligible = (config or {}).get("quant_agent", {}).get("eligible_score_threshold", 0.0)
    decision = DECISION_ELIGIBLE if quant_score >= min_eligible else DECISION_WEAK

    return QuantAssessment(
        ticker=entry.get("symbol", ""), quant_score=quant_score, rule_score=rule_score, ml_confidence=ml_confidence,
        expected_return=expected_return, big_money_score=big_money_display, regime_fit=regime_fit,
        strategy_edge=strategy_edge_label, decision=decision, reasons=reasons, warnings=warnings,
        calibrated_probability=calibrated_probability, model_agreement=model_agreement, horizon=horizon,
    )


def apply_quant_agent_filtering(
    ticker_results: list[dict[str, Any]],
    assessments: dict[str, QuantAssessment],
    config: dict[str, Any] | None = None,
) -> None:
    """Attaches `entry["quant_assessment"]` for reporting, always. With
    `config.ml.use_for_filtering` true (default false), MAY additionally
    reject an entry that already passed every upstream gate, but ONLY when
    the ML signal is both negative AND at least `minimum_confidence_for_
    filtering` confident - this can only ADD a rejection on top of an
    existing ACCEPT, never remove one, never touch `label`/
    `best_risk_result`/`regime_evaluation`, and never change a position's
    size. See module docstring."""
    cfg = (config or {}).get("ml", {})
    for entry in ticker_results:
        entry["quant_assessment"] = assessments.get(entry.get("symbol"))

    if not cfg.get("enabled", True) or not cfg.get("use_for_filtering", False):
        return

    min_confidence = cfg.get("minimum_confidence_for_filtering", ml_predictor.BAND_HIGH)
    min_rank = _CONFIDENCE_RANK.get(min_confidence, _CONFIDENCE_RANK[ml_predictor.BAND_HIGH])

    for entry in ticker_results:
        assessment = entry.get("quant_assessment")
        portfolio_eval = entry.get("portfolio_evaluation")
        if assessment is None or portfolio_eval is None or portfolio_eval.get("decision") == "REJECT":
            continue
        if entry.get("label") == "Avoid":
            continue

        if (
            assessment.expected_return is not None
            and assessment.expected_return < 0
            and _CONFIDENCE_RANK.get(assessment.ml_confidence, 0) >= min_rank
        ):
            portfolio_eval["decision"] = "REJECT"
            portfolio_eval.setdefault("rejection_reasons", []).append(
                f"Blocked by ML filtering: {assessment.ml_confidence} confidence in a negative expected "
                f"return ({assessment.expected_return:+.2%})."
            )
