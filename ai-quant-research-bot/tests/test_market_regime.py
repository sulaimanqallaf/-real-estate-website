"""Unit tests for market_regime.py: simple, explainable SPY/QQQ-based
classification (no ML), regime admission rules, and the position-size gates
that must only ever make things stricter, never looser.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import market_regime
from src.strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from src.strategies.momentum_breakout import STRATEGY_NAME as MOMENTUM_BREAKOUT
from src.strategies.trend_following import STRATEGY_NAME as TREND_FOLLOWING
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
CONFIG = load_config(CONFIG_PATH)


def make_snapshot(**overrides) -> dict:
    base = {
        "close": 100.0,
        "sma_200": 90.0,
        "ema_50": 95.0,
        "ema_200": 92.0,
        "momentum_20d": 2.0,
        "momentum_60d": 3.0,
        "daily_volatility_pct": 1.0,
    }
    base.update(overrides)
    return base


def make_price_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1_000_000.0] * len(closes)}, index=dates)
    df.index.name = "date"
    return df


# --- classify_regime -------------------------------------------------------------


def test_strong_bullish_snapshots_classify_as_bull_trend():
    snap = make_snapshot(close=100.0, sma_200=90.0, ema_50=95.0, ema_200=92.0, momentum_20d=3.0, daily_volatility_pct=1.0)
    price_df = make_price_df([90.0] * 50 + [100.0] * 10)  # no meaningful drawdown
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.BULL_TREND
    assert regime.trend == "bullish"
    assert regime.risk_state == "risk_on"
    assert regime.confidence == 1.0


def test_bearish_snapshots_with_deep_drawdown_classify_as_risk_off():
    snap = make_snapshot(close=80.0, sma_200=100.0, ema_50=85.0, ema_200=95.0, momentum_20d=-3.0, daily_volatility_pct=1.0)
    price_df = make_price_df([100.0] * 40 + [80.0] * 20)  # 20% drawdown from the rolling high
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.RISK_OFF
    assert regime.trend == "bearish"
    assert regime.risk_state == "risk_off"


def test_bearish_snapshots_without_deep_drawdown_or_elevated_vol_classify_as_bear_trend():
    snap = make_snapshot(close=95.0, sma_200=100.0, ema_50=95.0, ema_200=98.0, momentum_20d=-1.0, daily_volatility_pct=1.0)
    price_df = make_price_df([100.0] * 55 + [95.0] * 5)  # only a mild ~5% drawdown, below the RISK_OFF threshold
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.BEAR_TREND
    assert regime.risk_state == "risk_off"


def test_sideways_snapshot_classifies_as_sideways():
    # Neither an uptrend (price/EMA structure not fully aligned) nor a downtrend
    # (price still above SMA200), and volatility normal.
    snap = make_snapshot(close=100.0, sma_200=98.0, ema_50=97.0, ema_200=99.0, momentum_20d=0.2, daily_volatility_pct=1.0)
    price_df = make_price_df([98.0] * 40 + [100.0] * 20)
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.SIDEWAYS
    assert regime.trend == "mixed"


def test_volatility_spike_on_a_non_bullish_tape_classifies_as_high_volatility():
    threshold = CONFIG["market_regime"]["high_volatility_pct_threshold"]
    snap = make_snapshot(close=100.0, sma_200=98.0, ema_50=97.0, ema_200=99.0, momentum_20d=0.0, daily_volatility_pct=threshold + 1.0)
    price_df = make_price_df([98.0] * 40 + [100.0] * 20)
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.HIGH_VOLATILITY
    assert regime.volatility == "elevated"


def test_bull_trend_with_elevated_volatility_is_bull_volatile():
    threshold = CONFIG["market_regime"]["high_volatility_pct_threshold"]
    snap = make_snapshot(close=100.0, sma_200=90.0, ema_50=95.0, ema_200=92.0, momentum_20d=3.0, daily_volatility_pct=threshold + 1.0)
    price_df = make_price_df([90.0] * 50 + [100.0] * 10)
    regime = market_regime.classify_regime(price_df, price_df, snap, snap, CONFIG)
    assert regime.primary == market_regime.BULL_VOLATILE
    assert regime.risk_state == "risk_on"


def test_missing_snapshot_data_defaults_safely_to_sideways_without_crashing():
    nan_snap = make_snapshot(momentum_20d=float("nan"))
    good_snap = make_snapshot()
    price_df = make_price_df([100.0] * 30)
    regime = market_regime.classify_regime(price_df, price_df, nan_snap, good_snap, CONFIG)
    assert regime.primary == market_regime.SIDEWAYS
    assert regime.confidence == 0.0
    assert "insufficient data" in regime.explanation.lower()


def test_compute_trailing_drawdown_pct_is_none_with_too_little_history():
    price_df = make_price_df([100.0])
    assert market_regime.compute_trailing_drawdown_pct(price_df, 60) is None


def test_compute_trailing_drawdown_pct_is_correct():
    price_df = make_price_df([100.0] * 10 + [80.0])  # 20% drawdown from a 100 high
    dd = market_regime.compute_trailing_drawdown_pct(price_df, 60)
    assert dd == pytest.approx(20.0)


# --- strategy_key_for -------------------------------------------------------------


def test_strategy_key_for_maps_all_four_strategies():
    assert market_regime.strategy_key_for(STRATEGY_NAME_SAFE) == "mean_reversion_safe"
    assert market_regime.strategy_key_for(STRATEGY_NAME_AGGRESSIVE) == "mean_reversion_aggressive"
    assert market_regime.strategy_key_for(MOMENTUM_BREAKOUT) == "momentum_breakout"
    assert market_regime.strategy_key_for(TREND_FOLLOWING) == "trend_following"
    assert market_regime.strategy_key_for("Not A Real Strategy") is None


# --- evaluate_regime_admission -----------------------------------------------------


def test_preferred_and_allowed_get_full_status_multiplier():
    result = market_regime.evaluate_regime_admission("trend_following", market_regime.BULL_TREND, CONFIG, aggressive_enabled=False)
    assert result["status"] == "preferred"
    assert result["blocked"] is False
    assert result["size_multiplier"] == 1.0


def test_restricted_status_cuts_size():
    result = market_regime.evaluate_regime_admission("trend_following", market_regime.BEAR_TREND, CONFIG, aggressive_enabled=False)
    assert result["status"] == "restricted"
    assert result["blocked"] is False
    assert 0.0 < result["size_multiplier"] < 1.0


def test_aggressive_mean_reversion_blocked_in_bear_trend_when_aggressive_mode_disabled():
    result = market_regime.evaluate_regime_admission(
        "mean_reversion_aggressive", market_regime.BEAR_TREND, CONFIG, aggressive_enabled=False
    )
    assert result["status"] == "blocked"
    assert result["blocked"] is True


def test_aggressive_mean_reversion_only_restricted_not_blocked_in_bear_trend_when_enabled():
    result = market_regime.evaluate_regime_admission(
        "mean_reversion_aggressive", market_regime.BEAR_TREND, CONFIG, aggressive_enabled=True
    )
    assert result["blocked"] is False
    assert result["status"] == "restricted"
    assert result["size_multiplier"] < 1.0


def test_aggressive_mean_reversion_stays_blocked_in_high_volatility_and_risk_off_even_when_enabled():
    """The BEAR_TREND exception is scoped to BEAR_TREND only per spec - the spec
    states "blocked" plainly for HIGH_VOLATILITY/RISK_OFF with no stated
    exception, so aggressive_mode.enabled must not quietly widen it there too."""
    for regime in (market_regime.HIGH_VOLATILITY, market_regime.RISK_OFF):
        result = market_regime.evaluate_regime_admission(
            "mean_reversion_aggressive", regime, CONFIG, aggressive_enabled=True
        )
        assert result["blocked"] is True, f"expected still-blocked in {regime}"


def test_no_regime_admission_multiplier_ever_exceeds_one():
    for regime in [market_regime.BULL_TREND, market_regime.BULL_VOLATILE, market_regime.SIDEWAYS,
                   market_regime.BEAR_TREND, market_regime.HIGH_VOLATILITY, market_regime.RISK_OFF]:
        for strategy_key in ["mean_reversion_safe", "mean_reversion_aggressive", "momentum_breakout", "trend_following"]:
            for aggressive_enabled in (True, False):
                result = market_regime.evaluate_regime_admission(strategy_key, regime, CONFIG, aggressive_enabled)
                assert result["size_multiplier"] <= 1.0


# --- passes_regime_score_gate ------------------------------------------------------


def test_regime_without_a_configured_threshold_never_blocks_on_score():
    assert market_regime.passes_regime_score_gate(1, market_regime.BULL_TREND, CONFIG) is True


def test_risk_off_requires_configured_stricter_score():
    threshold = CONFIG["market_regime"]["score_thresholds"][market_regime.RISK_OFF]
    assert market_regime.passes_regime_score_gate(threshold, market_regime.RISK_OFF, CONFIG) is True
    assert market_regime.passes_regime_score_gate(threshold - 1, market_regime.RISK_OFF, CONFIG) is False


def test_regime_score_gate_never_admits_something_the_base_gate_would_reject():
    """Regime logic may only make admission stricter, never looser: a score of 0
    must never pass any regime's gate."""
    for regime in CONFIG["market_regime"]["score_thresholds"]:
        assert market_regime.passes_regime_score_gate(0, regime, CONFIG) is False
