"""AI research manager (TradingAgents-inspired) - turns the bull/bear
debate into one structured recommendation. `confidence` here is a
SELF-REPORTED heuristic strength (an average of the winning side's
analyst confidences), not a calibrated probability - unlike
`ml/predictor.py`'s `calibrated_probability`, which is only ever
produced by an actual fitted calibration model. Never represent this
confidence as calibrated without the same empirical validation
`ml/calibration.py` requires."""

from __future__ import annotations

from .schemas import ACTION_HOLD, AgentOpinion, DebateResult


def synthesize_recommendation(debate: DebateResult, opinions: dict[str, AgentOpinion]) -> tuple[str, float, str]:
    """Returns `(action, confidence, thesis)`. The action is exactly the
    debate's verdict - the research manager does not get a second,
    unexplained vote; it only decides how to EXPLAIN the verdict and how
    confident to call it, from the same opinions the debate already saw."""
    action = debate.verdict
    if action == ACTION_HOLD:
        available = [o for o in opinions.values() if o.data_available]
        if not available:
            return ACTION_HOLD, 0.0, "No analyst had sufficient data to form an opinion - defaulting to HOLD."
        return ACTION_HOLD, 0.0, "Bull and bear cases are balanced (or every available opinion was neutral) - no directional edge to act on."

    winning_points = debate.bull_points if action == "BUY" else debate.bear_points
    winning_score = debate.bull_score if action == "BUY" else debate.bear_score
    n_winning = max(1, len(winning_points))
    confidence = round(min(1.0, winning_score / n_winning), 3)

    thesis = (
        f"{len(winning_points)} analyst(s) support {action} "
        f"(bull_score={debate.bull_score}, bear_score={debate.bear_score}): "
        + " | ".join(winning_points)
    )
    return action, confidence, thesis
