"""Phase 7 Part Z - Autonomous policy (A+ auto only when explicitly enabled,
default config never auto trades, weaker trade requires approval, Avoid/
risk-rejected/regime-blocked/portfolio-rejected never execute, aggressive
disabled remains blocked, excellent ML cannot bypass rejection) and
pretrade checks (trading hours, event risk, slippage).
"""

import sys
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import execution_policy as policy
from src.execution import pretrade_checks as pt


def qa(confidence="VERY_HIGH", edge="POSITIVE"):
    return type("QA", (), {"ml_confidence": confidence, "strategy_edge": edge})()


def good_entry(**overrides):
    entry = {
        "symbol": "AMD",
        "label": "Top Candidate",
        "score": 90,
        "best_risk_result": {"tradeable": True},
        "regime_evaluation": {"blocked": False},
        "portfolio_evaluation": {"decision": "ACCEPT", "position": {"risk_reward": 3.0}},
        "quant_assessment": qa(),
    }
    entry.update(overrides)
    return entry


AUTO_ENABLED_CONFIG = {
    "autonomous_paper": {
        "enabled": True,
        "auto_execute": {
            "enabled": True, "allowed_confidence": ["VERY_HIGH"], "minimum_signal_score": 85,
            "minimum_risk_reward": 2.0, "require_strategy_edge": False, "require_good_data_quality": True,
        },
    }
}


# --- default config never auto-trades ------------------------------------------------


def test_default_config_never_auto_executes_even_a_perfect_candidate():
    decision = policy.classify_candidate(good_entry(), config={})
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_a_plus_candidate_auto_executes_only_when_both_gates_explicitly_enabled():
    decision = policy.classify_candidate(good_entry(), AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_AUTO_EXECUTE


def test_autonomous_enabled_but_auto_execute_disabled_still_requires_approval():
    config = {"autonomous_paper": {"enabled": True, "auto_execute": {"enabled": False}}}
    decision = policy.classify_candidate(good_entry(), config)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_auto_execute_enabled_but_autonomous_top_level_disabled_still_requires_approval():
    config = {"autonomous_paper": {"enabled": False, "auto_execute": {"enabled": True}}}
    decision = policy.classify_candidate(good_entry(), config)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


# --- upstream rejection always wins, regardless of ML confidence ---------------------


def test_avoid_label_never_executes_even_with_extremely_high_ml_confidence():
    entry = good_entry(label="Avoid", quant_assessment=qa(confidence="VERY_HIGH"))
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REJECT


def test_untradeable_individual_risk_never_executes():
    entry = good_entry(best_risk_result={"tradeable": False})
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REJECT


def test_regime_blocked_never_executes():
    entry = good_entry(regime_evaluation={"blocked": True})
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REJECT


def test_portfolio_reject_never_executes():
    entry = good_entry(portfolio_evaluation={"decision": "REJECT", "position": None})
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REJECT


def test_missing_portfolio_evaluation_is_rejected_not_auto_executed():
    entry = good_entry(portfolio_evaluation=None)
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REJECT


# --- a weaker trade requires approval, never silently rejected either ----------------


def test_low_confidence_candidate_requires_approval_not_auto_execute():
    entry = good_entry(quant_assessment=qa(confidence="MEDIUM"))
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_score_below_minimum_requires_approval():
    entry = good_entry(score=50)
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_risk_reward_below_minimum_requires_approval():
    entry = good_entry(portfolio_evaluation={"decision": "ACCEPT", "position": {"risk_reward": 1.0}})
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_unavailable_ml_confidence_fails_data_quality_requirement():
    entry = good_entry(quant_assessment=qa(confidence="UNAVAILABLE"))
    decision = policy.classify_candidate(entry, AUTO_ENABLED_CONFIG)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_require_strategy_edge_blocks_when_edge_is_not_positive():
    config = {
        "autonomous_paper": {
            "enabled": True,
            "auto_execute": {
                "enabled": True, "allowed_confidence": ["VERY_HIGH"], "minimum_signal_score": 85,
                "minimum_risk_reward": 2.0, "require_strategy_edge": True, "require_good_data_quality": True,
            },
        }
    }
    entry = good_entry(quant_assessment=qa(edge="NEGATIVE"))
    decision = policy.classify_candidate(entry, config)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


def test_reduced_size_candidate_blocked_when_allow_reduced_size_is_false():
    config = {
        "autonomous_paper": {
            "enabled": True,
            "auto_execute": {
                "enabled": True, "allowed_confidence": ["VERY_HIGH"], "minimum_signal_score": 85,
                "minimum_risk_reward": 2.0, "allow_reduced_size": False,
            },
        }
    }
    entry = good_entry(portfolio_evaluation={"decision": "ACCEPT_WITH_REDUCED_SIZE", "position": {"risk_reward": 3.0}})
    decision = policy.classify_candidate(entry, config)
    assert decision.decision == policy.DECISION_REQUIRE_APPROVAL


# --- compute_broker_constrained_quantity: never max(), always min() ------------------


def test_broker_constrained_quantity_shrinks_when_funds_insufficient():
    qty, reason = policy.compute_broker_constrained_quantity(candidate_quantity=10, entry_price=100.0, broker_available_funds=350.0, config={})
    assert qty == 3
    assert reason is None


def test_broker_constrained_quantity_never_increases_above_candidate_quantity():
    qty, reason = policy.compute_broker_constrained_quantity(candidate_quantity=5, entry_price=10.0, broker_available_funds=100_000.0, config={})
    assert qty == 5  # plenty of funds - still capped at the candidate's own size, never raised


def test_broker_constrained_quantity_passthrough_when_funds_unknown():
    qty, reason = policy.compute_broker_constrained_quantity(candidate_quantity=7, entry_price=50.0, broker_available_funds=None, config={})
    assert qty == 7
    assert reason is None


def test_broker_constrained_quantity_returns_position_too_small_below_one_share():
    qty, reason = policy.compute_broker_constrained_quantity(candidate_quantity=10, entry_price=1000.0, broker_available_funds=50.0, config={})
    assert qty == 0
    assert reason == policy.REASON_POSITION_TOO_SMALL


# --- pretrade_checks: trading hours ---------------------------------------------------


def test_within_regular_session_on_a_weekday_passes():
    now = datetime(2026, 9, 9, 10, 0, tzinfo=ZoneInfo("America/New_York"))  # Wednesday
    assert pt.check_trading_hours(now, config={}) is None


def test_before_open_is_blocked():
    now = datetime(2026, 9, 9, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    assert pt.check_trading_hours(now, config={}) == pt.REASON_OUTSIDE_TRADING_HOURS


def test_after_close_is_blocked():
    now = datetime(2026, 9, 9, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    assert pt.check_trading_hours(now, config={}) == pt.REASON_OUTSIDE_TRADING_HOURS


def test_weekend_is_always_blocked_even_during_session_hours():
    saturday = datetime(2026, 9, 12, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert pt.check_trading_hours(saturday, config={}) == pt.REASON_OUTSIDE_TRADING_HOURS


def test_trading_hours_respect_config_overrides():
    now = datetime(2026, 9, 9, 7, 0, tzinfo=ZoneInfo("America/New_York"))
    config = {"execution": {"trading_hours_start": "06:00", "trading_hours_end": "08:00"}}
    assert pt.check_trading_hours(now, config) is None


# --- pretrade_checks: event risk --------------------------------------------------------


def test_event_risk_never_invents_a_status_when_data_unavailable():
    config = {"execution": {"event_risk": {"enabled": True}}}
    assert pt.check_event_risk("AMD", event_flags=None, config=config) is None


def test_event_risk_disabled_by_default_even_with_flags_present():
    assert pt.check_event_risk("AMD", event_flags={"earnings_today": True}, config={}) is None


def test_event_risk_blocks_when_enabled_and_earnings_flag_set():
    config = {"execution": {"event_risk": {"enabled": True}}}
    assert pt.check_event_risk("AMD", event_flags={"earnings_today": True}, config=config) == pt.REASON_EVENT_RISK_BLOCKED


# --- pretrade_checks: slippage -----------------------------------------------------------


def test_slippage_passes_within_tolerance():
    assert pt.check_slippage(100.0, 100.2, config={}) is None


def test_slippage_blocks_beyond_tolerance():
    assert pt.check_slippage(100.0, 105.0, config={}) == pt.REASON_PRICE_MOVED_TOO_FAR


def test_slippage_blocks_when_current_price_unavailable():
    assert pt.check_slippage(100.0, None, config={}) == pt.REASON_PRICE_MOVED_TOO_FAR


def test_slippage_respects_configured_tolerance():
    config = {"execution": {"max_entry_slippage_pct": 0.05}}
    assert pt.check_slippage(100.0, 104.0, config) is None
