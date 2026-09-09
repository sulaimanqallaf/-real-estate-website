"""Risk management: universal trade-rejection rules and position sizing.

Version 1 hard constraints (never overridden by config, checked once here so no
strategy or report path can silently bypass them): no margin, no options trading,
no shorting, no real execution. This module only evaluates whether a *proposed*
long candidate (entry/stop/target) from a strategy module should be presented as a
trade idea, and if so, how many shares that implies at the configured risk-per-trade.
"""

from __future__ import annotations

from typing import Any


def evaluate_candidate(
    candidate: dict[str, Any], snapshot: dict[str, float], config: dict[str, Any]
) -> dict[str, Any]:
    """Apply the universal risk rules to one raw strategy candidate.

    Returns a dict with tradeable (bool), blocked_reasons (list[str]), the
    entry/stop/target as proposed, computed upside/downside/risk-reward, and
    (when tradeable) suggested position size.
    """
    risk_cfg = config["risk"]

    entry = candidate["entry"]
    stop = candidate["stop_loss"]
    target = candidate["target"]

    price = snapshot["close"]
    rsi = snapshot["rsi_14"]
    sma_200 = snapshot["sma_200"]

    blocked_reasons: list[str] = []

    if risk_cfg["require_above_sma_200"]:
        if sma_200 != sma_200:  # NaN
            blocked_reasons.append("200D moving average not yet available.")
        elif price < sma_200:
            blocked_reasons.append("Price is below the 200D moving average.")

    if rsi == rsi and rsi > risk_cfg["max_rsi_for_trade"]:  # NaN-safe
        blocked_reasons.append(f"RSI {rsi:.1f} is above the {risk_cfg['max_rsi_for_trade']} overbought cutoff.")

    risk_per_share = entry - stop
    reward_per_share = target - entry

    if risk_per_share <= 0:
        blocked_reasons.append("Stop loss is not below entry - invalid trade geometry.")
        risk_reward = None
    else:
        risk_reward = reward_per_share / risk_per_share
        if risk_reward < risk_cfg["min_risk_reward_ratio"]:
            blocked_reasons.append(
                f"Risk/reward {risk_reward:.2f} is below the minimum {risk_cfg['min_risk_reward_ratio']} required."
            )

    expected_upside_pct = (target - price) / price * 100.0 if price else None
    expected_downside_pct = (price - stop) / price * 100.0 if price else None

    if (
        risk_cfg["reject_if_downside_exceeds_upside"]
        and expected_upside_pct is not None
        and expected_downside_pct is not None
        and expected_downside_pct > expected_upside_pct
    ):
        blocked_reasons.append("Expected downside exceeds expected upside.")

    tradeable = len(blocked_reasons) == 0

    position = (
        _size_position(entry, stop, config) if tradeable else {"shares": 0, "dollar_risk": 0.0, "position_value": 0.0}
    )

    return {
        "strategy": candidate["strategy"],
        "tradeable": tradeable,
        "blocked_reasons": blocked_reasons,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "expected_upside_pct": round(expected_upside_pct, 2) if expected_upside_pct is not None else None,
        "expected_downside_pct": round(expected_downside_pct, 2) if expected_downside_pct is not None else None,
        "risk_reward": round(risk_reward, 2) if risk_reward is not None else None,
        "shares": position["shares"],
        "dollar_risk": round(position["dollar_risk"], 2),
        "position_value": round(position["position_value"], 2),
        "note": candidate.get("note", ""),
    }


def _size_position(entry: float, stop: float, config: dict[str, Any]) -> dict[str, float]:
    risk_cfg = config["risk"]
    account_equity = risk_cfg["account_equity"]
    risk_pct = risk_cfg["risk_pct_per_trade"] / 100.0

    dollar_risk_budget = account_equity * risk_pct
    risk_per_share = entry - stop

    if risk_per_share <= 0:
        return {"shares": 0, "dollar_risk": 0.0, "position_value": 0.0}

    shares = int(dollar_risk_budget // risk_per_share)
    dollar_risk = shares * risk_per_share
    position_value = shares * entry

    return {"shares": shares, "dollar_risk": dollar_risk, "position_value": position_value}


def evaluate_best_candidate(
    raw_candidates: list[dict[str, Any]], snapshot: dict[str, float], config: dict[str, Any]
) -> dict[str, Any] | None:
    """Evaluate every raw candidate for a ticker and return the best result.

    "Best" = tradeable candidates ranked by risk/reward first; if none are
    tradeable, the highest-computed-risk/reward evaluation is still returned so
    callers can see why it was blocked.
    """
    if not raw_candidates:
        return None

    evaluated = [evaluate_candidate(c, snapshot, config) for c in raw_candidates]

    tradeable = [e for e in evaluated if e["tradeable"]]
    if tradeable:
        return max(tradeable, key=lambda e: e["risk_reward"] or 0.0)

    with_rr = [e for e in evaluated if e["risk_reward"] is not None]
    if with_rr:
        return max(with_rr, key=lambda e: e["risk_reward"])

    return evaluated[0]
