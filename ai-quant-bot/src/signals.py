"""Signal classification and trade-plan estimation (entry/target/stop/R:R).

Version 1 never executes trades. This module only estimates parameters
for a report; a "trade setup" here is informational, not an order.
"""

from __future__ import annotations

import math
from typing import Any

STRONG_BUY = "Strong Buy Setup"
WATCHLIST = "Watchlist"
AVOID = "Avoid"


def _is_nan(value: float) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def classify_by_score(score: int, config: dict[str, Any]) -> str:
    signals_cfg = config["signals"]
    if score >= signals_cfg["strong_buy_min_score"]:
        return STRONG_BUY
    if score >= signals_cfg["watchlist_min_score"]:
        return WATCHLIST
    return AVOID


def build_trade_plan(snapshot: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    """Estimate entry zone / target / stop / R:R, or explain why no trade is proposed."""
    risk_cfg = config["risk"]

    price = snapshot["close"]
    atr = snapshot["atr_14"]
    rsi = snapshot["rsi_14"]
    sma_200 = snapshot["sma_200"]

    reasons_blocked: list[str] = []

    if _is_nan(price) or _is_nan(atr) or atr <= 0:
        reasons_blocked.append("Insufficient data to compute ATR-based trade plan.")

    if not _is_nan(rsi) and rsi > risk_cfg["max_rsi_for_trade"]:
        reasons_blocked.append(f"RSI {rsi:.1f} is above the {risk_cfg['max_rsi_for_trade']} overbought cutoff.")

    if risk_cfg["require_above_200d_ma"]:
        if _is_nan(sma_200):
            reasons_blocked.append("200D moving average not yet available.")
        elif price < sma_200:
            reasons_blocked.append("Price is below the 200D moving average.")

    if reasons_blocked:
        return {
            "tradeable": False,
            "entry_low": None,
            "entry_high": None,
            "target": None,
            "stop_loss": None,
            "expected_upside_pct": None,
            "expected_downside_pct": None,
            "risk_reward": None,
            "blocked_reasons": reasons_blocked,
        }

    entry_fraction = risk_cfg["entry_zone_atr_fraction"]
    entry_low = price - entry_fraction * atr
    entry_high = price + entry_fraction * atr

    stop_loss = entry_low - risk_cfg["atr_stop_multiplier"] * atr
    risk_per_share = entry_high - stop_loss
    target = entry_high + risk_per_share * risk_cfg["min_reward_risk_multiple"]

    reward_per_share = target - entry_high
    risk_reward = reward_per_share / risk_per_share if risk_per_share > 0 else float("nan")

    if _is_nan(risk_reward) or risk_reward < risk_cfg["min_risk_reward_ratio"]:
        return {
            "tradeable": False,
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "target": round(target, 2),
            "stop_loss": round(stop_loss, 2),
            "expected_upside_pct": None,
            "expected_downside_pct": None,
            "risk_reward": round(risk_reward, 2) if not _is_nan(risk_reward) else None,
            "blocked_reasons": [
                f"Risk/reward {risk_reward:.2f} is below the minimum "
                f"{risk_cfg['min_risk_reward_ratio']} required to recommend a trade."
            ],
        }

    expected_upside_pct = (target - price) / price * 100.0
    expected_downside_pct = (price - stop_loss) / price * 100.0

    return {
        "tradeable": True,
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "target": round(target, 2),
        "stop_loss": round(stop_loss, 2),
        "expected_upside_pct": round(expected_upside_pct, 2),
        "expected_downside_pct": round(expected_downside_pct, 2),
        "risk_reward": round(risk_reward, 2),
        "blocked_reasons": [],
    }


def trend_status(snapshot: dict[str, float]) -> str:
    price = snapshot["close"]
    sma_50 = snapshot["sma_50"]
    sma_200 = snapshot["sma_200"]

    above_50 = (not _is_nan(sma_50)) and price > sma_50
    above_200 = (not _is_nan(sma_200)) and price > sma_200

    if above_50 and above_200:
        return "Above 50D and 200D moving averages"
    if above_200 and not above_50:
        return "Above 200D but below 50D moving average"
    if above_50 and not above_200:
        return "Above 50D but below 200D moving average"
    return "Below 50D and 200D moving averages"


def momentum_status(snapshot: dict[str, float]) -> str:
    m20 = snapshot["momentum_20d"]
    m60 = snapshot["momentum_60d"]

    m20_pos = (not _is_nan(m20)) and m20 > 0
    m60_pos = (not _is_nan(m60)) and m60 > 0

    if m20_pos and m60_pos:
        return "Positive 20D and 60D"
    if m20_pos and not m60_pos:
        return "Positive 20D, negative 60D"
    if m60_pos and not m20_pos:
        return "Negative 20D, positive 60D"
    return "Negative 20D and 60D"


def build_explanation(
    signal: str,
    score_breakdown: dict[str, bool],
    trade_plan: dict[str, Any],
    snapshot: dict[str, float],
) -> str:
    if signal == AVOID and trade_plan["blocked_reasons"]:
        return " ".join(trade_plan["blocked_reasons"])

    positives = []
    if score_breakdown.get("above_200d_ma") and score_breakdown.get("above_50d_ma"):
        positives.append("strong trend")
    if score_breakdown.get("momentum_20d_positive") and score_breakdown.get("momentum_60d_positive"):
        positives.append("healthy momentum")
    if score_breakdown.get("rsi_in_healthy_range"):
        positives.append("RSI not overbought")
    if score_breakdown.get("volume_above_avg"):
        positives.append("above-average volume")

    negatives = []
    if not score_breakdown.get("above_200d_ma"):
        negatives.append("price below 200D MA")
    if not score_breakdown.get("momentum_20d_positive"):
        negatives.append("weak 20D momentum")
    if not score_breakdown.get("rsi_in_healthy_range"):
        rsi = snapshot["rsi_14"]
        if not _is_nan(rsi) and rsi > 68:
            negatives.append("RSI elevated")
        else:
            negatives.append("RSI outside healthy range")
    if not score_breakdown.get("not_extended_above_50d"):
        negatives.append("extended above 50D MA")

    if signal == STRONG_BUY:
        parts = positives or ["conditions broadly favorable"]
    elif signal == WATCHLIST:
        parts = (positives[:1] or []) + negatives[:2]
    else:
        parts = negatives or ["insufficient favorable conditions"]

    return (", ".join(parts) + ".").capitalize()
