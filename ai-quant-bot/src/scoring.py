"""0-100 scoring system built from the latest indicator snapshot."""

from __future__ import annotations

import math
from typing import Any


def _is_nan(value: float) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def score_asset(snapshot: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    """Score one asset from its indicator snapshot.

    Returns a dict with the total score (0-100) and a breakdown of which
    conditions were satisfied, so the report can explain the score.
    """
    points_cfg = config["scoring"]["points"]
    rsi_min = config["scoring"]["rsi_healthy_min"]
    rsi_max = config["scoring"]["rsi_healthy_max"]
    atr_extreme_pct = config["scoring"]["atr_extreme_pct_of_price"]
    max_extension_pct = config["scoring"]["max_extension_above_50d_pct"]

    price = snapshot["close"]
    sma_50 = snapshot["sma_50"]
    sma_200 = snapshot["sma_200"]
    momentum_20d = snapshot["momentum_20d"]
    momentum_60d = snapshot["momentum_60d"]
    rsi = snapshot["rsi_14"]
    atr = snapshot["atr_14"]
    volume_ratio = snapshot["volume_ratio_20d"]

    breakdown: dict[str, bool] = {}
    score = 0

    def award(key: str, condition: bool, points: int) -> None:
        nonlocal score
        breakdown[key] = bool(condition)
        if condition:
            score += points

    above_200d = (not _is_nan(sma_200)) and (not _is_nan(price)) and price > sma_200
    award("above_200d_ma", above_200d, points_cfg["above_200d_ma"])

    above_50d = (not _is_nan(sma_50)) and (not _is_nan(price)) and price > sma_50
    award("above_50d_ma", above_50d, points_cfg["above_50d_ma"])

    momentum_20_positive = (not _is_nan(momentum_20d)) and momentum_20d > 0
    award("momentum_20d_positive", momentum_20_positive, points_cfg["momentum_20d_positive"])

    momentum_60_positive = (not _is_nan(momentum_60d)) and momentum_60d > 0
    award("momentum_60d_positive", momentum_60_positive, points_cfg["momentum_60d_positive"])

    rsi_healthy = (not _is_nan(rsi)) and rsi_min <= rsi <= rsi_max
    award("rsi_in_healthy_range", rsi_healthy, points_cfg["rsi_in_healthy_range"])

    volume_above_avg = (not _is_nan(volume_ratio)) and volume_ratio > 1.0
    award("volume_above_avg", volume_above_avg, points_cfg["volume_above_avg"])

    atr_pct_of_price = (atr / price * 100.0) if (not _is_nan(atr) and not _is_nan(price) and price > 0) else float("nan")
    atr_not_extreme = (not _is_nan(atr_pct_of_price)) and atr_pct_of_price <= atr_extreme_pct
    award("atr_not_extreme", atr_not_extreme, points_cfg["atr_not_extreme"])

    extension_above_50d_pct = (
        ((price - sma_50) / sma_50 * 100.0)
        if (not _is_nan(sma_50) and sma_50 > 0 and not _is_nan(price))
        else float("nan")
    )
    not_extended = (not _is_nan(extension_above_50d_pct)) and extension_above_50d_pct <= max_extension_pct
    award("not_extended_above_50d", not_extended, points_cfg["not_extended_above_50d"])

    return {
        "score": score,
        "breakdown": breakdown,
        "atr_pct_of_price": atr_pct_of_price,
        "extension_above_50d_pct": extension_above_50d_pct,
    }
