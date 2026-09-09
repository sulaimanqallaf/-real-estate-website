"""Unit tests for scoring.py and signals.py using synthetic indicator snapshots."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import scoring, signals
from src.utils import load_config

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")


def make_snapshot(**overrides) -> dict:
    base = {
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "volume": 1_000_000.0,
        "momentum_20d": 2.0,
        "momentum_60d": 3.0,
        "sma_50": 95.0,
        "sma_200": 90.0,
        "rsi_14": 55.0,
        "atr_14": 1.5,
        "volume_ratio_20d": 1.2,
        "daily_volatility_pct": 1.0,
    }
    base.update(overrides)
    return base


def test_perfect_score_is_100():
    snap = make_snapshot()
    result = scoring.score_asset(snap, CONFIG)
    assert result["score"] == 100
    assert all(result["breakdown"].values())


def test_price_below_all_mas_scores_low():
    # Deep downtrend: trend/momentum/RSI/volume conditions all fail. atr_not_extreme and
    # not_extended_above_50d still score (low volatility, and "not extended above the 50D MA"
    # is trivially true when price sits well below it) - that is correct, not a bug.
    snap = make_snapshot(
        close=80.0,
        sma_50=95.0,
        sma_200=90.0,
        momentum_20d=-1.0,
        momentum_60d=-2.0,
        rsi_14=30.0,
        volume_ratio_20d=0.8,
    )
    result = scoring.score_asset(snap, CONFIG)
    breakdown = result["breakdown"]
    assert result["score"] == 15
    assert breakdown["atr_not_extreme"] is True
    assert breakdown["not_extended_above_50d"] is True
    for key in ("above_200d_ma", "above_50d_ma", "momentum_20d_positive", "momentum_60d_positive",
                "rsi_in_healthy_range", "volume_above_avg"):
        assert breakdown[key] is False


def test_rsi_boundaries():
    cfg = CONFIG["scoring"]
    rsi_points = cfg["points"]["rsi_in_healthy_range"]

    in_range = scoring.score_asset(make_snapshot(rsi_14=cfg["rsi_healthy_min"]), CONFIG)
    assert in_range["breakdown"]["rsi_in_healthy_range"] is True

    just_above = scoring.score_asset(make_snapshot(rsi_14=cfg["rsi_healthy_max"] + 0.01), CONFIG)
    assert just_above["breakdown"]["rsi_in_healthy_range"] is False

    below_min = scoring.score_asset(make_snapshot(rsi_14=cfg["rsi_healthy_min"] - 0.01), CONFIG)
    assert below_min["breakdown"]["rsi_in_healthy_range"] is False
    assert (in_range["score"] - below_min["score"]) == rsi_points


def test_extension_above_50d_penalty():
    cfg = CONFIG["scoring"]
    max_ext = cfg["max_extension_above_50d_pct"]

    # Stay a hair under/over the threshold to avoid floating-point boundary flakiness.
    within = make_snapshot(close=95.0 * (1 + (max_ext - 0.1) / 100.0), sma_50=95.0)
    over = make_snapshot(close=95.0 * (1 + (max_ext + 0.1) / 100.0), sma_50=95.0)

    result_within = scoring.score_asset(within, CONFIG)
    result_over = scoring.score_asset(over, CONFIG)

    assert result_within["breakdown"]["not_extended_above_50d"] is True
    assert result_over["breakdown"]["not_extended_above_50d"] is False


def test_atr_extreme_penalty():
    cfg = CONFIG["scoring"]
    threshold_pct = cfg["atr_extreme_pct_of_price"]

    calm = make_snapshot(close=100.0, atr_14=(threshold_pct / 100.0) * 100.0)
    volatile = make_snapshot(close=100.0, atr_14=(threshold_pct / 100.0) * 100.0 + 1.0)

    assert scoring.score_asset(calm, CONFIG)["breakdown"]["atr_not_extreme"] is True
    assert scoring.score_asset(volatile, CONFIG)["breakdown"]["atr_not_extreme"] is False


def test_signal_classification_thresholds():
    cfg = CONFIG["signals"]
    assert signals.classify_by_score(cfg["strong_buy_min_score"], CONFIG) == signals.STRONG_BUY
    assert signals.classify_by_score(cfg["strong_buy_min_score"] - 1, CONFIG) == signals.WATCHLIST
    assert signals.classify_by_score(cfg["watchlist_min_score"], CONFIG) == signals.WATCHLIST
    assert signals.classify_by_score(cfg["watchlist_min_score"] - 1, CONFIG) == signals.AVOID


def test_trade_plan_blocked_when_rsi_overbought():
    snap = make_snapshot(rsi_14=80.0)
    plan = signals.build_trade_plan(snap, CONFIG)
    assert plan["tradeable"] is False
    assert any("overbought" in reason.lower() for reason in plan["blocked_reasons"])


def test_trade_plan_blocked_when_price_below_200d_ma():
    snap = make_snapshot(close=85.0, sma_200=90.0, sma_50=95.0)
    plan = signals.build_trade_plan(snap, CONFIG)
    assert plan["tradeable"] is False
    assert any("200d" in reason.lower() for reason in plan["blocked_reasons"])


def test_trade_plan_valid_case_has_positive_risk_reward():
    snap = make_snapshot()
    plan = signals.build_trade_plan(snap, CONFIG)
    assert plan["tradeable"] is True
    assert plan["risk_reward"] >= CONFIG["risk"]["min_risk_reward_ratio"]
    assert plan["stop_loss"] < plan["entry_low"] < plan["entry_high"] < plan["target"]


def test_trade_plan_blocked_when_risk_reward_too_low():
    # A tiny ATR relative to price with min_reward_risk_multiple still applied should
    # normally pass; force a failure by requiring an unreachable minimum ratio.
    import copy

    cfg = copy.deepcopy(CONFIG)
    cfg["risk"]["min_risk_reward_ratio"] = 999.0
    snap = make_snapshot()
    plan = signals.build_trade_plan(snap, cfg)
    assert plan["tradeable"] is False
    assert any("risk/reward" in reason.lower() for reason in plan["blocked_reasons"])
