"""Unit tests for risk_manager.py: universal gating rules and position sizing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import risk_manager
from src.strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from src.utils import load_config

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")


def make_snapshot(**overrides) -> dict:
    base = {
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "volume": 1_000_000.0,
        "rsi_14": 55.0,
        "sma_200": 90.0,
    }
    base.update(overrides)
    return base


def make_candidate(entry=100.0, stop_loss=95.0, target=115.0, strategy="Test Strategy"):
    # risk = 5, reward = 15 -> R:R = 3.0 (comfortably above the 1.5 minimum)
    return {"strategy": strategy, "entry": entry, "stop_loss": stop_loss, "target": target, "note": "test"}


def test_valid_candidate_is_tradeable_with_correct_risk_reward():
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(), CONFIG)
    assert result["tradeable"] is True
    assert result["blocked_reasons"] == []
    assert result["risk_reward"] == 3.0
    assert result["expected_upside_pct"] == 15.0
    assert result["expected_downside_pct"] == 5.0


def test_blocked_when_price_below_sma_200():
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(sma_200=110.0), CONFIG)
    assert result["tradeable"] is False
    assert any("200d" in r.lower() for r in result["blocked_reasons"])


def test_blocked_when_rsi_overbought():
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(rsi_14=80.0), CONFIG)
    assert result["tradeable"] is False
    assert any("overbought" in r.lower() for r in result["blocked_reasons"])


def test_rsi_exactly_at_cutoff_is_blocked():
    # RSI must be *strictly below* 75 to pass - 75.0 itself does not qualify.
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(rsi_14=75.0), CONFIG)
    assert result["tradeable"] is False
    assert any("overbought" in r.lower() for r in result["blocked_reasons"])


def test_rsi_just_below_cutoff_passes():
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(rsi_14=74.9), CONFIG)
    assert result["tradeable"] is True


def test_mean_reversion_safe_candidate_is_exempt_from_sma_200_rule():
    # Price (100) is below SMA200 (110) - blocked for any other strategy, but Mean
    # Reversion (either mode) is explicitly exempt from this one rule.
    candidate = make_candidate(strategy=STRATEGY_NAME_SAFE)
    result = risk_manager.evaluate_candidate(candidate, make_snapshot(sma_200=110.0), CONFIG)
    assert result["tradeable"] is True
    assert not any("200d" in r.lower() for r in result["blocked_reasons"])


def test_mean_reversion_aggressive_candidate_is_exempt_from_sma_200_rule():
    candidate = make_candidate(strategy=STRATEGY_NAME_AGGRESSIVE)
    result = risk_manager.evaluate_candidate(candidate, make_snapshot(sma_200=110.0), CONFIG)
    assert result["tradeable"] is True
    assert not any("200d" in r.lower() for r in result["blocked_reasons"])


def test_non_mean_reversion_candidate_is_still_blocked_below_sma_200():
    candidate = make_candidate(strategy="Trend Following")
    result = risk_manager.evaluate_candidate(candidate, make_snapshot(sma_200=110.0), CONFIG)
    assert result["tradeable"] is False
    assert any("200d" in r.lower() for r in result["blocked_reasons"])


def test_blocked_when_risk_reward_too_low():
    # risk = 5, reward = 5 -> R:R = 1.0, below the 1.5 minimum.
    candidate = make_candidate(entry=100.0, stop_loss=95.0, target=105.0)
    result = risk_manager.evaluate_candidate(candidate, make_snapshot(), CONFIG)
    assert result["tradeable"] is False
    assert any("risk/reward" in r.lower() for r in result["blocked_reasons"])


def test_blocked_when_downside_exceeds_upside():
    # entry 100, stop 80 (20% downside), target 108 (8% upside) - even though the
    # raw R:R (8/20 = 0.4) would already fail, this exercises the dedicated rule.
    candidate = make_candidate(entry=100.0, stop_loss=80.0, target=108.0)
    result = risk_manager.evaluate_candidate(candidate, make_snapshot(), CONFIG)
    assert result["tradeable"] is False
    assert any("downside exceeds" in r.lower() for r in result["blocked_reasons"])


def test_position_sizing_respects_risk_budget():
    cfg = CONFIG.copy()
    cfg["risk"] = {**CONFIG["risk"], "account_equity": 10000, "risk_pct_per_trade": 1.0}
    # risk per share = entry(100) - stop(95) = 5. Budget = 10000 * 1% = 100 -> 20 shares.
    result = risk_manager.evaluate_candidate(make_candidate(), make_snapshot(), cfg)
    assert result["tradeable"] is True
    assert result["shares"] == 20
    assert result["dollar_risk"] == 100.0


def test_evaluate_best_candidate_prefers_tradeable_over_blocked():
    blocked = make_candidate(entry=100.0, stop_loss=95.0, target=105.0, strategy="Blocked One")  # R:R too low
    good = make_candidate(entry=100.0, stop_loss=95.0, target=115.0, strategy="Good One")
    best = risk_manager.evaluate_best_candidate([blocked, good], make_snapshot(), CONFIG)
    assert best["tradeable"] is True
    assert best["strategy"] == "Good One"


def test_evaluate_best_candidate_returns_none_for_empty_list():
    assert risk_manager.evaluate_best_candidate([], make_snapshot(), CONFIG) is None
