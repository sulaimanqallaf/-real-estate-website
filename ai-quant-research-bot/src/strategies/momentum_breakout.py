"""Strategy 2 - Momentum Breakout (long only).

Trigger: price closes above the prior N-day high on volume above M times its
20-day average. Stop is ATR-based; target is set to a configured reward multiple
of the resulting risk.
"""

from __future__ import annotations

from typing import Any

STRATEGY_NAME = "Momentum Breakout"


def evaluate(snapshot: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    cfg = config["strategies"]["momentum_breakout"]

    price = snapshot["close"]
    rolling_high = snapshot["rolling_high_20"]
    relative_volume = snapshot["relative_volume"]
    atr = snapshot["atr_14"]

    result = {"strategy": STRATEGY_NAME, "triggered": False, "candidate": None}

    required = (price, rolling_high, relative_volume, atr)
    if any(v != v for v in required):  # NaN check
        return result

    breakout = price > rolling_high
    volume_confirmed = relative_volume > cfg["volume_multiplier"]
    triggered = breakout and volume_confirmed
    result["triggered"] = bool(triggered)

    if not triggered:
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
        "note": (
            f"Broke above the prior {cfg['breakout_window']}D high on "
            f"{relative_volume:.2f}x average volume."
        ),
    }
    return result
