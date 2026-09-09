"""Unit tests for signal_scorer.py: 0-100 scoring composition and labels."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import signal_scorer
from src.strategies import skew_map
from src.utils import load_config

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")


def make_snapshot(**overrides) -> dict:
    base = {
        "close": 100.0,
        "sma_50": 95.0,
        "sma_200": 90.0,
        "momentum_20d": 2.0,
        "momentum_60d": 3.0,
        "rsi_14": 55.0,
        "relative_volume": 1.2,
    }
    base.update(overrides)
    return base


def make_risk_result(risk_reward=2.0):
    return {"risk_reward": risk_reward, "tradeable": True}


def test_perfect_score_is_100():
    snapshot = make_snapshot()
    trend_result = {"trend_positive": True}
    breakout_result = {"triggered": True}
    result = signal_scorer.score_ticker(
        snapshot, trend_result, breakout_result, skew_map.CHASE, make_risk_result(2.0), CONFIG
    )
    assert result["score"] == 100
    assert all(result["breakdown"].values())
    assert result["label"] == signal_scorer.STRONG_CANDIDATE


def test_zero_bonus_conditions_scores_only_universal_checklist():
    # No strategy applies to this ticker (trend_result/breakout_result are None,
    # as main.py passes when the ticker isn't in that strategy's universe), and
    # skew is unavailable, and there's no risk result (no candidate ever triggered).
    snapshot = make_snapshot()
    result = signal_scorer.score_ticker(snapshot, None, None, skew_map.DATA_UNAVAILABLE, None, CONFIG)
    points = CONFIG["scoring"]["points"]
    expected = (
        points["above_sma_200"]
        + points["above_sma_50"]
        + points["momentum_20d_positive"]
        + points["momentum_60d_positive"]
        + points["rsi_in_healthy_range"]
        + points["volume_above_avg"]
    )
    assert result["score"] == expected
    assert result["breakdown"]["trend_following_positive"] is False
    assert result["breakdown"]["momentum_breakout_active"] is False
    assert result["breakdown"]["favorable_options_skew"] is False
    assert result["breakdown"]["risk_reward_above_threshold"] is False


def test_price_below_all_mas_and_bad_momentum_scores_zero_on_universal_checklist():
    snapshot = make_snapshot(close=80.0, sma_50=95.0, sma_200=90.0, momentum_20d=-1.0, momentum_60d=-2.0, rsi_14=30.0, relative_volume=0.5)
    result = signal_scorer.score_ticker(snapshot, None, None, skew_map.DATA_UNAVAILABLE, None, CONFIG)
    assert result["score"] == 0
    assert result["label"] == signal_scorer.AVOID


def test_favorable_skew_only_awarded_for_configured_labels():
    snapshot = make_snapshot()
    favorable = signal_scorer.score_ticker(snapshot, None, None, skew_map.CONTRARIAN_BID, None, CONFIG)
    unfavorable = signal_scorer.score_ticker(snapshot, None, None, skew_map.FEAR, None, CONFIG)
    assert favorable["breakdown"]["favorable_options_skew"] is True
    assert unfavorable["breakdown"]["favorable_options_skew"] is False


def test_risk_reward_bonus_only_when_above_threshold():
    snapshot = make_snapshot()
    min_rr = CONFIG["risk"]["min_risk_reward_ratio"]
    above = signal_scorer.score_ticker(snapshot, None, None, skew_map.NEUTRAL, make_risk_result(min_rr), CONFIG)
    below = signal_scorer.score_ticker(snapshot, None, None, skew_map.NEUTRAL, make_risk_result(min_rr - 0.1), CONFIG)
    assert above["breakdown"]["risk_reward_above_threshold"] is True
    assert below["breakdown"]["risk_reward_above_threshold"] is False


def test_label_thresholds():
    labels_cfg = CONFIG["scoring"]["labels"]
    assert signal_scorer.classify_label(labels_cfg["strong_candidate_min"], CONFIG) == signal_scorer.STRONG_CANDIDATE
    assert signal_scorer.classify_label(labels_cfg["strong_candidate_min"] - 1, CONFIG) == signal_scorer.WATCHLIST
    assert signal_scorer.classify_label(labels_cfg["watchlist_min"], CONFIG) == signal_scorer.WATCHLIST
    assert signal_scorer.classify_label(labels_cfg["watchlist_min"] - 1, CONFIG) == signal_scorer.WEAK_WATCHLIST
    assert signal_scorer.classify_label(labels_cfg["weak_watchlist_min"], CONFIG) == signal_scorer.WEAK_WATCHLIST
    assert signal_scorer.classify_label(labels_cfg["weak_watchlist_min"] - 1, CONFIG) == signal_scorer.AVOID


def test_skew_classification_directions():
    threshold = CONFIG["strategies"]["skew"]["skew_threshold"]
    assert skew_map.classify_skew(-5.0, -threshold - 0.01, CONFIG) == skew_map.CONTRARIAN_BID  # down + calls bid
    assert skew_map.classify_skew(5.0, -threshold - 0.01, CONFIG) == skew_map.CHASE            # up + calls bid
    assert skew_map.classify_skew(5.0, threshold + 0.01, CONFIG) == skew_map.HEDGED_RALLY       # up + puts bid
    assert skew_map.classify_skew(-5.0, threshold + 0.01, CONFIG) == skew_map.FEAR              # down + puts bid
    assert skew_map.classify_skew(None, 0.05, CONFIG) == skew_map.DATA_UNAVAILABLE
    assert skew_map.classify_skew(5.0, None, CONFIG) == skew_map.DATA_UNAVAILABLE
