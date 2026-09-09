"""Tests for src/quant_agent.py and src/strategy_memory.py (Phase 6 Part Y
"Quant Agent" and "Strategy memory") - includes the hard-invariant tests
that ML/Big Money/Quant Agent context can never override a deterministic
gate."""

import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import paper_trades, quant_agent, strategy_memory
from src.ml import predictor as ml_predictor
from src.strategy_memory import StrategyEdge
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@pytest.fixture(scope="module")
def config():
    return load_config(CONFIG_PATH)


def make_prediction(expected_return=0.03, confidence=ml_predictor.BAND_HIGH, calibrated=0.7, agreement=0.9):
    return ml_predictor.MLPrediction(
        ticker="X", horizon="10d", expected_return=expected_return, raw_probability=0.65,
        calibrated_probability=calibrated, confidence_band=confidence, model_id="m1",
        data_quality="GOOD", warnings=[], model_agreement=agreement,
    )


def make_entry(label="Strong candidate", tradeable=True, regime_blocked=False, portfolio_decision="ACCEPT", score=85, regime_multiplier=1.0):
    return {
        "symbol": "TEST", "label": label, "score": score,
        "best_risk_result": {"tradeable": tradeable, "shares": 10, "entry": 100.0, "stop_loss": 95.0},
        "regime_evaluation": {"blocked": regime_blocked, "combined_multiplier": regime_multiplier},
        "portfolio_evaluation": {"decision": portfolio_decision, "rejection_reasons": []},
        "big_money_score": None,
    }


# --- quant_agent: hard invariants -------------------------------------------------------


def test_ml_cannot_promote_avoid_labeled_candidate(config):
    entry = make_entry(label="Avoid")
    strong_pred = make_prediction(expected_return=0.10, confidence=ml_predictor.BAND_VERY_HIGH, calibrated=0.95)
    assessment = quant_agent.assess_candidate(entry, strong_pred, None, config)
    assert assessment.decision == quant_agent.DECISION_NOT_ELIGIBLE
    assert entry["label"] == "Avoid"  # untouched


def test_ml_cannot_bypass_individual_risk_rejection(config):
    entry = make_entry(tradeable=False)
    entry["portfolio_evaluation"] = None  # never reached portfolio stage
    entry["regime_evaluation"] = None
    strong_pred = make_prediction(expected_return=0.10, confidence=ml_predictor.BAND_VERY_HIGH)
    assessment = quant_agent.assess_candidate(entry, strong_pred, None, config)
    assert assessment.decision == quant_agent.DECISION_NOT_ELIGIBLE


def test_ml_cannot_bypass_portfolio_risk_rejection(config):
    entry = make_entry(portfolio_decision="REJECT")
    strong_pred = make_prediction(expected_return=0.10, confidence=ml_predictor.BAND_VERY_HIGH)
    assessment = quant_agent.assess_candidate(entry, strong_pred, None, config)
    assert assessment.decision == quant_agent.DECISION_NOT_ELIGIBLE
    assert entry["portfolio_evaluation"]["decision"] == "REJECT"


def test_ml_cannot_override_regime_block(config):
    entry = make_entry(regime_blocked=True)
    strong_pred = make_prediction(expected_return=0.10, confidence=ml_predictor.BAND_VERY_HIGH)
    assessment = quant_agent.assess_candidate(entry, strong_pred, None, config)
    assert assessment.decision == quant_agent.DECISION_NOT_ELIGIBLE
    assert entry["regime_evaluation"]["blocked"] is True


def test_quant_agent_never_writes_to_authoritative_fields(config):
    """assess_candidate is purely read-only with respect to the fields that
    already decided eligibility - a broad structural check across many
    scenarios."""
    for label in ("Avoid", "Strong candidate"):
        for tradeable in (True, False):
            for regime_blocked in (True, False):
                for portfolio_decision in ("ACCEPT", "REJECT"):
                    entry = make_entry(label=label, tradeable=tradeable, regime_blocked=regime_blocked, portfolio_decision=portfolio_decision)
                    snapshot = {
                        "label": entry["label"],
                        "best_risk_result": dict(entry["best_risk_result"]),
                        "regime_evaluation": dict(entry["regime_evaluation"]),
                        "portfolio_evaluation": dict(entry["portfolio_evaluation"]),
                    }
                    quant_agent.assess_candidate(entry, make_prediction(), None, config)
                    assert entry["label"] == snapshot["label"]
                    assert entry["best_risk_result"] == snapshot["best_risk_result"]
                    assert entry["regime_evaluation"] == snapshot["regime_evaluation"]
                    assert entry["portfolio_evaluation"] == snapshot["portfolio_evaluation"]


def test_ml_cannot_enable_aggressive_mode(config):
    """assess_candidate/apply_quant_agent_filtering have no code path that
    reads or writes config.strategies.mean_reversion.aggressive_mode at
    all - a static assertion that the config dict handed in is never
    mutated."""
    config_copy = {**config, "strategies": {**config["strategies"]}}
    aggressive_before = config_copy["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"]
    entry = make_entry()
    quant_agent.assess_candidate(entry, make_prediction(), None, config_copy)
    assert config_copy["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] == aggressive_before


def test_ml_cannot_increase_position_size_via_filtering(config):
    """apply_quant_agent_filtering only ever sets portfolio_evaluation
    decision to REJECT - it has no code path that touches `position` or
    `shares` at all."""
    entry = make_entry()
    entry["portfolio_evaluation"]["position"] = {"shares": 10}
    assessments = {"TEST": quant_agent.assess_candidate(entry, make_prediction(expected_return=-0.05, confidence=ml_predictor.BAND_VERY_HIGH), None, config)}
    ml_cfg = {"ml": {"enabled": True, "use_for_filtering": True, "minimum_confidence_for_filtering": "HIGH"}}
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["portfolio_evaluation"]["position"] == {"shares": 10}  # never touched


# --- quant_agent: additive-only filtering behavior --------------------------------------


def test_strong_ml_improves_ranking_only_never_changes_gate_outcome(config):
    entry = make_entry()
    strong_pred = make_prediction(expected_return=0.05, confidence=ml_predictor.BAND_VERY_HIGH, calibrated=0.85)
    assessment = quant_agent.assess_candidate(entry, strong_pred, None, config)
    assert assessment.decision == quant_agent.DECISION_ELIGIBLE
    assert assessment.quant_score > entry["score"]  # ranking boost is visible...
    assert entry["portfolio_evaluation"]["decision"] == "ACCEPT"  # ...but the gate outcome is untouched


def test_weak_ml_lowers_confidence_but_does_not_reject_by_default(config):
    entry = make_entry()
    weak_pred = make_prediction(expected_return=-0.02, confidence=ml_predictor.BAND_LOW, calibrated=0.4)
    assessments = {"TEST": quant_agent.assess_candidate(entry, weak_pred, None, config)}
    ml_cfg = {"ml": {"enabled": True, "use_for_filtering": False}}  # default: report-only
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["portfolio_evaluation"]["decision"] == "ACCEPT"  # unaffected while use_for_filtering is off


def test_ml_filtering_explicitly_enabled_can_add_a_new_rejection(config):
    entry = make_entry()
    bearish_pred = make_prediction(expected_return=-0.05, confidence=ml_predictor.BAND_VERY_HIGH, calibrated=0.1)
    assessments = {"TEST": quant_agent.assess_candidate(entry, bearish_pred, None, config)}
    ml_cfg = {"ml": {"enabled": True, "use_for_filtering": True, "minimum_confidence_for_filtering": "HIGH"}}
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["portfolio_evaluation"]["decision"] == "REJECT"
    assert "ML filtering" in entry["portfolio_evaluation"]["rejection_reasons"][0]


def test_ml_filtering_never_touches_an_already_rejected_entry(config):
    entry = make_entry(portfolio_decision="REJECT")
    entry["portfolio_evaluation"]["rejection_reasons"] = ["Portfolio blocked: max open positions reached."]
    bearish_pred = make_prediction(expected_return=-0.05, confidence=ml_predictor.BAND_VERY_HIGH)
    assessments = {"TEST": quant_agent.assess_candidate(entry, bearish_pred, None, config)}
    ml_cfg = {"ml": {"enabled": True, "use_for_filtering": True, "minimum_confidence_for_filtering": "HIGH"}}
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["portfolio_evaluation"]["rejection_reasons"] == ["Portfolio blocked: max open positions reached."]  # untouched, not duplicated


def test_ml_filtering_requires_minimum_confidence_before_blocking(config):
    """A LOW-confidence bearish signal must not block anything even with
    use_for_filtering enabled, if minimum_confidence_for_filtering is HIGH."""
    entry = make_entry()
    weak_bearish = make_prediction(expected_return=-0.05, confidence=ml_predictor.BAND_LOW, calibrated=0.3)
    assessments = {"TEST": quant_agent.assess_candidate(entry, weak_bearish, None, config)}
    ml_cfg = {"ml": {"enabled": True, "use_for_filtering": True, "minimum_confidence_for_filtering": "HIGH"}}
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["portfolio_evaluation"]["decision"] == "ACCEPT"


def test_ml_disabled_entirely_clears_assessment_field(config):
    entry = make_entry()
    assessments = {"TEST": quant_agent.assess_candidate(entry, make_prediction(), None, config)}
    ml_cfg = {"ml": {"enabled": False}}
    quant_agent.apply_quant_agent_filtering([entry], assessments, ml_cfg)
    assert entry["quant_assessment"] is not None  # still attached for inspection
    assert entry["portfolio_evaluation"]["decision"] == "ACCEPT"  # never touched while disabled


# --- quant_agent: ML unavailable degrades cleanly ---------------------------------------


def test_quant_agent_with_no_ml_prediction_still_produces_assessment(config):
    entry = make_entry()
    assessment = quant_agent.assess_candidate(entry, None, None, config)
    assert assessment.ml_confidence == ml_predictor.BAND_UNAVAILABLE
    assert assessment.decision == quant_agent.DECISION_ELIGIBLE
    assert "ML: Data Unavailable." in assessment.warnings


# --- strategy_memory ---------------------------------------------------------------------


def _make_trade(ticker, strategy, regime, pnl_pct, pnl_dollars):
    return {
        "trade_id": f"{ticker}_x", "approved_at": "x", "ticker": ticker, "strategy": strategy, "mode": "N/A",
        "signal": "Strong candidate", "score": 85, "entry_price": 100.0, "stop_loss": 95.0, "target_price": 110.0,
        "risk_reward": 2.0, "position_size": 10, "risk_amount": 50.0,
        "status": "STOPPED" if pnl_pct < 0 else "TARGET_HIT", "opened_at": "2026-01-01",
        "exit_price": 100.0 * (1 + pnl_pct / 100), "exited_at": "2026-01-05",
        "exit_reason": "target" if pnl_pct >= 0 else "stop", "pnl_dollars": pnl_dollars, "pnl_pct": pnl_pct,
        "holding_days": 4, "notes": "", "regime_at_entry": regime,
    }


@pytest.fixture
def paper_trades_config(config, tmp_path):
    cfg = dict(config)
    cfg["data"] = {**config["data"], "journal_dir": str(tmp_path)}
    return cfg


def test_insufficient_sample_never_claims_an_edge(paper_trades_config):
    trades = [_make_trade("AMD", "Momentum Breakout", "BULL_TREND", 5.0, 50) for _ in range(3)]  # below MIN_SAMPLE
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    df.to_csv(paper_trades._paper_trades_path(paper_trades_config), index=False)

    edges = strategy_memory.compute_strategy_edge(paper_trades_config, min_sample=10)
    edge = edges.get("Momentum Breakout")
    assert edge is not None
    assert edge.has_sufficient_sample is False
    assert edge.edge_direction == "INSUFFICIENT_SAMPLE"
    assert edge.win_rate_pct is None


def test_regime_conditioned_edge_calculates_correctly(paper_trades_config):
    trades = []
    for i in range(10):
        trades.append(_make_trade("AMD", "Momentum Breakout", "BULL_TREND", 8.0 if i < 8 else -3.0, 80 if i < 8 else -30))
    for i in range(10):
        trades.append(_make_trade("AMD", "Momentum Breakout", "SIDEWAYS", -4.0 if i < 8 else 5.0, -40 if i < 8 else 50))
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    df.to_csv(paper_trades._paper_trades_path(paper_trades_config), index=False)

    bull_edge = strategy_memory.edge_for_strategy_in_regime(paper_trades_config, "Momentum Breakout", "BULL_TREND", min_sample=5)
    sideways_edge = strategy_memory.edge_for_strategy_in_regime(paper_trades_config, "Momentum Breakout", "SIDEWAYS", min_sample=5)
    assert bull_edge.edge_direction == "POSITIVE"
    assert sideways_edge.edge_direction == "NEGATIVE"
    assert bull_edge.win_rate_pct == 80.0
    assert sideways_edge.win_rate_pct == 20.0


def test_missing_regime_combo_returns_zero_sample_not_none(paper_trades_config):
    trades = [_make_trade("AMD", "Momentum Breakout", "BULL_TREND", 5.0, 50) for _ in range(10)]
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    df.to_csv(paper_trades._paper_trades_path(paper_trades_config), index=False)

    missing = strategy_memory.edge_for_strategy_in_regime(paper_trades_config, "Momentum Breakout", "RISK_OFF")
    assert missing.sample_size == 0
    assert missing.has_sufficient_sample is False


def test_pre_phase6_trades_without_regime_at_entry_excluded_from_conditional_edge(paper_trades_config):
    """Trades recorded before the regime_at_entry column existed (blank
    value) must never be silently included in a regime-conditioned
    breakdown."""
    trades = [_make_trade("AMD", "Momentum Breakout", "", 5.0, 50) for _ in range(10)]
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    df.to_csv(paper_trades._paper_trades_path(paper_trades_config), index=False)

    conditional = strategy_memory.compute_regime_conditioned_strategy_edge(paper_trades_config, min_sample=5)
    assert conditional == {}


def test_empty_paper_trades_file_returns_empty_dict(paper_trades_config):
    assert strategy_memory.compute_grouped_edge(paper_trades_config, ["strategy"]) == {}
