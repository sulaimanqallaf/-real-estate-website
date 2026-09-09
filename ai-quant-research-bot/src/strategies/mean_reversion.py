"""Strategy 1 - Mean Reversion (long only).

Trigger: price is below the N-period moving average by more than K standard
deviations, and the underlying trend is not broken (price still above SMA 200).
Exit target is the moving average itself (reversion target).
"""

from __future__ import annotations

from typing import Any

STRATEGY_NAME = "Mean Reversion"


def evaluate(snapshot: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    cfg = config["strategies"]["mean_reversion"]

    price = snapshot["close"]
    ma = snapshot["sma_20"]
    std = snapshot["bb_std"]
    atr = snapshot["atr_14"]
    sma_200 = snapshot["sma_200"]

    result = {"strategy": STRATEGY_NAME, "triggered": False, "candidate": None}

    required = (price, ma, std, atr)
    if any(v != v for v in required):  # NaN check
        return result

    trend_intact = True
    if cfg["require_trend_intact"]:
        trend_intact = (sma_200 == sma_200) and price > sma_200  # NaN-safe check

    if std <= 0 or not trend_intact:
        return result

    deviation_in_std = (ma - price) / std
    triggered = deviation_in_std > cfg["std_dev_threshold"]
    result["triggered"] = bool(triggered)

    if not triggered:
        return result

    entry = price
    stop = entry - cfg["atr_stop_multiplier"] * atr
    target = ma  # reversion target: back to the moving average

    result["candidate"] = {
        "strategy": STRATEGY_NAME,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "note": f"Price is {deviation_in_std:.2f} std devs below the 20D MA with trend intact.",
    }
    return result
