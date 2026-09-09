"""Strategy 3 - Trend Following (long only).

Trigger: EMA 50 above EMA 200 and price above SMA 200 => trend positive.
EMA 50 below EMA 200 => avoid (no candidate, regardless of price).
"""

from __future__ import annotations

from typing import Any

STRATEGY_NAME = "Trend Following"

UPTREND = "Uptrend"
DOWNTREND = "Downtrend"
MIXED = "Mixed / Sideways"


def describe_trend(snapshot: dict[str, float]) -> str:
    """Reporting-level trend descriptor for ANY ticker (e.g. SPY/QQQ regime lines).

    This is independent of evaluate()'s strategy-trigger logic, which only applies
    to the tickers in config.strategy_universe.trend_following.
    """
    price = snapshot["close"]
    ema_50 = snapshot["ema_50"]
    ema_200 = snapshot["ema_200"]
    sma_200 = snapshot["sma_200"]

    if any(v != v for v in (price, ema_50, ema_200, sma_200)):  # NaN check
        return MIXED

    if ema_50 > ema_200 and price > sma_200:
        return UPTREND
    if price < sma_200:
        return DOWNTREND
    return MIXED


def evaluate(snapshot: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    cfg = config["strategies"]["trend_following"]

    price = snapshot["close"]
    ema_50 = snapshot["ema_50"]
    ema_200 = snapshot["ema_200"]
    sma_200 = snapshot["sma_200"]
    atr = snapshot["atr_14"]

    result = {"strategy": STRATEGY_NAME, "trend_positive": False, "triggered": False, "candidate": None}

    required = (price, ema_50, ema_200, sma_200, atr)
    if any(v != v for v in required):  # NaN check
        return result

    trend_positive = ema_50 > ema_200 and price > sma_200
    result["trend_positive"] = bool(trend_positive)
    result["triggered"] = bool(trend_positive)

    if not trend_positive:
        return result

    entry = price
    risk_per_share = cfg["atr_stop_multiplier"] * atr
    stop = entry - risk_per_share
    target = entry + cfg["reward_risk_multiple"] * risk_per_share

    result["candidate"] = {
        "strategy": STRATEGY_NAME,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "note": "EMA 50 above EMA 200 and price above SMA 200: trend positive.",
    }
    return result
