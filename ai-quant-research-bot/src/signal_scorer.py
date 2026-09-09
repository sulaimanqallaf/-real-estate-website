"""0-100 signal scoring and label classification.

Scoring is computed for every ticker, whether or not any strategy actually
triggered a candidate for it - the first six criteria are pure indicator checks
that apply universally; the last four only score points when the relevant
strategy/skew/risk state applies to that ticker.
"""

from __future__ import annotations

from typing import Any

STRONG_CANDIDATE = "Strong candidate"
WATCHLIST = "Watchlist"
WEAK_WATCHLIST = "Weak watchlist"
AVOID = "Avoid"


def _is_nan(value: float) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def score_ticker(
    snapshot: dict[str, float],
    trend_following_result: dict[str, Any] | None,
    momentum_breakout_result: dict[str, Any] | None,
    skew_classification: str,
    best_risk_result: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    points_cfg = config["scoring"]["points"]
    rsi_min = config["scoring"]["rsi_healthy_min"]
    rsi_max = config["scoring"]["rsi_healthy_max"]

    price = snapshot["close"]
    sma_50 = snapshot["sma_50"]
    sma_200 = snapshot["sma_200"]
    momentum_20d = snapshot["momentum_20d"]
    momentum_60d = snapshot["momentum_60d"]
    rsi = snapshot["rsi_14"]
    relative_volume = snapshot["relative_volume"]

    breakdown: dict[str, bool] = {}
    score = 0

    def award(key: str, condition: bool) -> None:
        nonlocal score
        breakdown[key] = bool(condition)
        if condition:
            score += points_cfg[key]

    award("above_sma_200", (not _is_nan(sma_200)) and (not _is_nan(price)) and price > sma_200)
    award("above_sma_50", (not _is_nan(sma_50)) and (not _is_nan(price)) and price > sma_50)
    award("momentum_20d_positive", (not _is_nan(momentum_20d)) and momentum_20d > 0)
    award("momentum_60d_positive", (not _is_nan(momentum_60d)) and momentum_60d > 0)
    award("rsi_in_healthy_range", (not _is_nan(rsi)) and rsi_min <= rsi <= rsi_max)
    award("volume_above_avg", (not _is_nan(relative_volume)) and relative_volume > 1.0)

    trend_positive = bool(trend_following_result and trend_following_result.get("trend_positive"))
    award("trend_following_positive", trend_positive)

    breakout_active = bool(momentum_breakout_result and momentum_breakout_result.get("triggered"))
    award("momentum_breakout_active", breakout_active)

    from .strategies import skew_map

    award("favorable_options_skew", skew_map.is_favorable(skew_classification, config))

    risk_reward = best_risk_result.get("risk_reward") if best_risk_result else None
    award(
        "risk_reward_above_threshold",
        risk_reward is not None and risk_reward >= config["risk"]["min_risk_reward_ratio"],
    )

    label = classify_label(score, config)

    return {"score": score, "label": label, "breakdown": breakdown}


def classify_label(score: int, config: dict[str, Any]) -> str:
    labels_cfg = config["scoring"]["labels"]
    if score >= labels_cfg["strong_candidate_min"]:
        return STRONG_CANDIDATE
    if score >= labels_cfg["watchlist_min"]:
        return WATCHLIST
    if score >= labels_cfg["weak_watchlist_min"]:
        return WEAK_WATCHLIST
    return AVOID
