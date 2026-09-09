"""Strategy 1 - Mean Reversion (long only), in two modes.

SAFE mode (default): price is below the N-period moving average by more than K
standard deviations, AND the underlying trend is not broken (price still above
SMA 200). This is the only mode allowed into Top Candidates / the trade journal.

AGGRESSIVE mode: the same dip logic but with a deeper required washout and,
critically, WITHOUT requiring price to be above SMA 200 - a genuine "buy the dip
during a downtrend" setup. It is always computed and reported (see
report_writer.py's "High Risk Dip Watchlist" section) but is never eligible for
Top Candidates or the trade journal unless config.strategies.mean_reversion.
aggressive_mode.enabled is explicitly set to true - see risk_manager.py and
report_writer.select_top_candidates for where that's enforced.

Exit target in both modes is the moving average itself (reversion target).
"""

from __future__ import annotations

from typing import Any

STRATEGY_NAME_SAFE = "Mean Reversion (Safe)"
STRATEGY_NAME_AGGRESSIVE = "Mean Reversion (Aggressive)"

_MODE_STRATEGY_NAMES = {"safe": STRATEGY_NAME_SAFE, "aggressive": STRATEGY_NAME_AGGRESSIVE}


def _mode_config(config: dict[str, Any], mode: str) -> dict[str, Any]:
    base_cfg = config["strategies"]["mean_reversion"]
    if mode == "safe":
        return base_cfg
    if mode == "aggressive":
        return base_cfg["aggressive_mode"]
    raise ValueError(f"Unknown mean reversion mode: {mode!r} (expected 'safe' or 'aggressive')")


def evaluate(snapshot: dict[str, float], config: dict[str, Any], mode: str = "safe") -> dict[str, Any]:
    cfg = _mode_config(config, mode)
    strategy_name = _MODE_STRATEGY_NAMES[mode]

    price = snapshot["close"]
    ma = snapshot["sma_20"]
    std = snapshot["bb_std"]
    atr = snapshot["atr_14"]
    sma_200 = snapshot["sma_200"]

    result = {"strategy": strategy_name, "mode": mode, "triggered": False, "candidate": None}

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

    trend_note = "with trend intact" if cfg["require_trend_intact"] else "without requiring trend intact (aggressive)"
    result["candidate"] = {
        "strategy": strategy_name,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "note": f"Price is {deviation_in_std:.2f} std devs below the 20D MA {trend_note}.",
    }
    return result
