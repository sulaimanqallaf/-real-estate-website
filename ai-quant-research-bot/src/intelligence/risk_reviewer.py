"""Portfolio risk reviewer (TradingAgents-inspired "risk team" stage).

**Hard invariant, mirroring `quant_agent.py`'s:** this can only move a
recommendation TOWARD HOLD (veto a BUY/SELL), never the other way. It has
no code path that can turn a HOLD into a BUY/SELL, upgrade confidence, or
touch `best_risk_result`/`regime_evaluation`/`portfolio_evaluation`/
position sizing - those stay the sole, unmodified authority of the
existing deterministic risk stack. This is read-only portfolio CONTEXT,
never a control capability (GitHub Issue #1 comment, integration plan
point 2 and point 6: "read-only... never credentials or control
capabilities" / "veto/reduce-only until validated")."""

from __future__ import annotations

from typing import Any


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


def review(entry: dict[str, Any], action: str, confidence: float, thesis: str) -> tuple[str, float, list[str]]:
    """Returns `(action, confidence, risk_notes)` - `action`/`confidence`
    are either unchanged or vetoed down to `("HOLD", 0.0)`; `thesis` is
    read but never modified (kept in the caller's AgentResearchAssessment
    unchanged, exactly like quant_agent's reasons list)."""
    notes: list[str] = []

    if action == "HOLD":
        return action, confidence, notes

    if _is_rejected_upstream(entry):
        notes.append(
            "Vetoed to HOLD: the existing deterministic risk stack (label/individual risk/regime/portfolio risk) "
            "already rejected this candidate - the research layer cannot override this."
        )
        return "HOLD", 0.0, notes

    portfolio_eval = entry.get("portfolio_evaluation") or {}
    if portfolio_eval.get("decision") not in (None, "ACCEPT"):
        notes.append(f"Vetoed to HOLD: portfolio risk decision is '{portfolio_eval.get('decision')}', not ACCEPT.")
        return "HOLD", 0.0, notes

    notes.append(f"No portfolio-risk veto triggered; recommendation stands at {action} (shadow mode only - not executed).")
    return action, confidence, notes
