"""Market Regime Engine - simple, explainable SPY/QQQ-based classification.

No machine learning. Every decision here is a plain rule over indicators already
computed elsewhere (EMA/SMA trend, momentum, rolling volatility) plus a trailing
drawdown computed directly from the raw price history main.py already fetched -
no new data source, no new fetch.

Runs BEFORE the Portfolio Risk Engine in the pipeline:
  Individual Risk Manager -> Market Regime Filter -> Portfolio Risk Manager -> Top Candidate
It can only shrink a candidate's size (via `position_multipliers` and
`status_size_multipliers`, both <= 1.0) or block a strategy outright for the
current regime - it never increases anything risk_manager.py already sized, and
it never loosens the existing score/label gates (`score_thresholds` only adds a
*stricter* bar on top of them for certain regimes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .strategies import trend_following
from .strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from .strategies.momentum_breakout import STRATEGY_NAME as MOMENTUM_BREAKOUT_STRATEGY_NAME
from .strategies.trend_following import STRATEGY_NAME as TREND_FOLLOWING_STRATEGY_NAME

BULL_TREND = "BULL_TREND"
BULL_VOLATILE = "BULL_VOLATILE"
SIDEWAYS = "SIDEWAYS"
BEAR_TREND = "BEAR_TREND"
HIGH_VOLATILITY = "HIGH_VOLATILITY"
RISK_OFF = "RISK_OFF"

_STRATEGY_KEY_MAP = {
    STRATEGY_NAME_SAFE: "mean_reversion_safe",
    STRATEGY_NAME_AGGRESSIVE: "mean_reversion_aggressive",
    MOMENTUM_BREAKOUT_STRATEGY_NAME: "momentum_breakout",
    TREND_FOLLOWING_STRATEGY_NAME: "trend_following",
}


def strategy_key_for(strategy_name: str) -> str | None:
    return _STRATEGY_KEY_MAP.get(strategy_name)


@dataclass(frozen=True)
class MarketRegime:
    primary: str
    trend: str          # "bullish" | "bearish" | "mixed"
    volatility: str      # "elevated" | "normal"
    risk_state: str        # "risk_on" | "risk_off"
    confidence: float        # 0.0-1.0, a normalized count of agreeing rule inputs - never a fabricated ML probability
    explanation: str


_RISK_OFF_PRIMARIES = {RISK_OFF, BEAR_TREND, HIGH_VOLATILITY}


def _is_nan(value: float) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def compute_trailing_drawdown_pct(price_df: pd.DataFrame, lookback_days: int) -> float | None:
    """(rolling high - latest close) / rolling high * 100, over the trailing
    window. None (never fabricated) if there isn't enough history."""
    window = price_df["close"].tail(lookback_days)
    if len(window) < 2:
        return None
    rolling_high = window.max()
    if rolling_high <= 0:
        return None
    latest_close = window.iloc[-1]
    return float((rolling_high - latest_close) / rolling_high * 100.0)


def classify_regime(
    spy_df: pd.DataFrame,
    qqq_df: pd.DataFrame,
    spy_snapshot: dict[str, float],
    qqq_snapshot: dict[str, float],
    config: dict[str, Any],
) -> MarketRegime:
    cfg = config["market_regime"]

    required_fields = ("close", "ema_50", "ema_200", "sma_200", "momentum_20d", "momentum_60d", "daily_volatility_pct")
    if any(_is_nan(spy_snapshot.get(f)) for f in required_fields) or any(
        _is_nan(qqq_snapshot.get(f)) for f in required_fields
    ):
        return MarketRegime(
            primary=SIDEWAYS,
            trend="mixed",
            volatility="normal",
            risk_state="risk_on",
            confidence=0.0,
            explanation="Insufficient data for regime classification - defaulting to SIDEWAYS.",
        )

    spy_trend = trend_following.describe_trend(spy_snapshot)
    qqq_trend = trend_following.describe_trend(qqq_snapshot)
    bullish = spy_trend == trend_following.UPTREND and qqq_trend == trend_following.UPTREND
    bearish = spy_trend == trend_following.DOWNTREND and qqq_trend == trend_following.DOWNTREND

    vol_threshold = cfg["high_volatility_pct_threshold"]
    elevated_volatility = (
        spy_snapshot["daily_volatility_pct"] >= vol_threshold or qqq_snapshot["daily_volatility_pct"] >= vol_threshold
    )

    drawdown_pct = compute_trailing_drawdown_pct(spy_df, cfg["drawdown_lookback_days"])
    risk_off_drawdown = drawdown_pct is not None and drawdown_pct >= cfg["risk_off_drawdown_pct_threshold"]

    if risk_off_drawdown or (bearish and elevated_volatility):
        primary = RISK_OFF
    elif elevated_volatility and not bullish:
        primary = HIGH_VOLATILITY
    elif bearish:
        primary = BEAR_TREND
    elif bullish and elevated_volatility:
        primary = BULL_VOLATILE
    elif bullish:
        primary = BULL_TREND
    else:
        primary = SIDEWAYS

    trend = "bullish" if bullish else ("bearish" if bearish else "mixed")
    volatility = "elevated" if elevated_volatility else "normal"
    risk_state = "risk_off" if primary in _RISK_OFF_PRIMARIES else "risk_on"

    # Confidence is a plain count of rule inputs that agree with the chosen
    # trend label, normalized to 0-1 - never a fabricated ML-style probability.
    spy_mom_positive = spy_snapshot["momentum_20d"] > 0
    qqq_mom_positive = qqq_snapshot["momentum_20d"] > 0

    if trend == "bullish":
        agreeing_signals = [spy_trend == trend_following.UPTREND, qqq_trend == trend_following.UPTREND, spy_mom_positive, qqq_mom_positive]
        explanation = (
            f"SPY > SMA200 and EMA50 > EMA200; QQQ momentum {'positive' if qqq_mom_positive else 'negative'}; "
            f"volatility {volatility}."
        )
    elif trend == "bearish":
        agreeing_signals = [spy_trend == trend_following.DOWNTREND, qqq_trend == trend_following.DOWNTREND, not spy_mom_positive, not qqq_mom_positive]
        explanation = (
            f"SPY < SMA200; QQQ trend: {qqq_trend}; momentum {'negative' if not qqq_mom_positive else 'positive'}; "
            f"volatility {volatility}."
        )
    else:
        agreeing_signals = [spy_trend != trend_following.UPTREND, qqq_trend != trend_following.UPTREND, not elevated_volatility]
        explanation = f"SPY trend: {spy_trend}; QQQ trend: {qqq_trend}; volatility {volatility}."

    confidence = round(sum(agreeing_signals) / len(agreeing_signals), 2)
    return MarketRegime(primary, trend, volatility, risk_state, confidence, explanation)


def evaluate_regime_admission(
    strategy_key: str, regime_primary: str, config: dict[str, Any], aggressive_enabled: bool
) -> dict[str, Any]:
    """Whether a strategy is admitted in the current regime, and by how much its
    size should be cut (on top of the regime-wide position_multipliers entry).

    Returns {"status", "blocked", "size_multiplier", "note"}. Never increases
    anything - size_multiplier is always <= 1.0, and "blocked" is a hard reject
    (size_multiplier is meaningless/0.0 in that case, never actually applied).
    """
    cfg = config["market_regime"]
    prefs = cfg["strategy_preferences"].get(regime_primary, {})
    status = prefs.get(strategy_key, "allowed")

    # Spec-specified exception, scoped to BEAR_TREND only (spec names it there
    # specifically; HIGH_VOLATILITY and RISK_OFF are plain "blocked" with no
    # stated exception): Aggressive Mean Reversion is only "restricted" (not
    # blocked) if aggressive_mode is explicitly enabled - the regime's own
    # score_thresholds entry (checked separately by passes_regime_score_gate)
    # then serves as the "exceptional conditions" bar this candidate still has
    # to clear.
    if status == "blocked" and strategy_key == "mean_reversion_aggressive" and aggressive_enabled and regime_primary == BEAR_TREND:
        status = "restricted"

    if status == "blocked":
        return {
            "status": "blocked",
            "blocked": True,
            "size_multiplier": 0.0,
            "note": f"{strategy_key} is blocked in {regime_primary}.",
        }

    size_multiplier = cfg["status_size_multipliers"].get(status, 1.0)
    note = f"{strategy_key} is '{status}' in {regime_primary}."
    return {"status": status, "blocked": False, "size_multiplier": size_multiplier, "note": note}


def passes_regime_score_gate(score: int, regime_primary: str, config: dict[str, Any]) -> bool:
    """The optional stricter, regime-wide score bar (item 15) - additive on top
    of the existing Avoid-label gate, never a substitute or a loosening of it."""
    threshold = config["market_regime"]["score_thresholds"].get(regime_primary)
    if threshold is None:
        return True
    return score >= threshold
