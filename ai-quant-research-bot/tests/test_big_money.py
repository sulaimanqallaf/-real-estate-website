"""Tests for src/big_money.py: transparent component scoring, the
unavailable-vs-neutral distinction, and the hard rule that Big Money context
can never bypass or promote past the existing signal/risk/regime/portfolio
gates."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import big_money
from src.data_providers.options_flow_provider import FlowEvent
from datetime import datetime


def _flow_event(call_put, premium):
    return FlowEvent(
        timestamp=datetime(2026, 8, 1), ticker="NVDA", event_type="call_sweep", side="ask",
        premium=premium, strike=130.0, expiry="2026-09-19", call_put=call_put, price=5.0, size=100,
        source="test", available_at=datetime(2026, 8, 1),
    )


# --- component scoring: unavailable vs neutral --------------------------------------


def test_institutional_score_is_none_not_zero_when_no_data():
    assert big_money.score_institutional_accumulation(None) is None
    assert big_money.score_institutional_accumulation({"has_data": False}) is None


def test_institutional_score_reflects_buy_vs_sell_side_facts():
    facts = {"has_data": True, "new_positions": 3, "increased_positions": 2, "reduced_positions": 1, "exited_positions": 0}
    score = big_money.score_institutional_accumulation(facts)
    assert score == round((5 - 1) / 6, 4)


def test_insider_score_is_none_when_no_open_market_activity():
    assert big_money.score_insider_activity(None) is None
    assert big_money.score_insider_activity({"has_data": False}) is None


def test_insider_score_boosted_by_cluster_buying_flag():
    features = {
        "has_data": True, "insider_buy_value_30d": 100_000, "insider_sell_value_30d": 0, "cluster_buying": True,
    }
    score = big_money.score_insider_activity(features)
    assert score == 1.0  # base 1.0 + 0.15 boost, capped at 1.0


def test_options_flow_score_is_none_without_events():
    assert big_money.score_options_flow(None) is None
    assert big_money.score_options_flow([]) is None


def test_options_flow_score_reflects_call_vs_put_premium():
    events = [_flow_event("call", 300_000.0), _flow_event("put", 100_000.0)]
    score = big_money.score_options_flow(events)
    assert score == (300_000.0 - 100_000.0) / 400_000.0


def test_relative_volume_score_none_for_nan():
    assert big_money.score_relative_volume(float("nan")) is None
    assert big_money.score_relative_volume(None) is None


def test_relative_volume_score_bounded_at_plus_minus_one():
    assert big_money.score_relative_volume(10.0) == 1.0
    assert big_money.score_relative_volume(0.0) == -1.0


# --- composite score: transparent breakdown, unavailable vs neutral ----------------


def test_composite_score_breaks_down_by_component():
    facts = {"has_data": True, "new_positions": 2, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("NVDA", institutional_facts=facts, relative_volume=1.5)
    assert score.components[big_money.COMPONENT_INSTITUTIONAL] == 1.0
    assert score.components[big_money.COMPONENT_RELATIVE_VOLUME] == 0.5
    assert score.components[big_money.COMPONENT_INSIDER] is None
    assert score.composite_score is not None


def test_component_breakdown_sums_correctly_to_the_composite():
    facts = {"has_data": True, "new_positions": 1, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    weights = {big_money.COMPONENT_INSTITUTIONAL: 2.0, big_money.COMPONENT_RELATIVE_VOLUME: 1.0}
    score = big_money.compute_big_money_score("NVDA", institutional_facts=facts, relative_volume=2.0, weights=weights)
    expected = (1.0 * 2.0 + 1.0 * 1.0) / 3.0  # institutional=1.0, rel_vol score=1.0 (bounded), weighted avg
    assert score.composite_score == round(expected, 4)


def test_unavailable_is_not_neutral_composite_is_none_when_no_data_at_all():
    score = big_money.compute_big_money_score("NVDA")
    assert score.composite_score is None
    assert score.has_any_data is False
    assert any("Data Unavailable" not in "" for _ in [1])  # sanity no-op
    assert "No Big Money component data" in score.notes[0]


def test_data_quality_score_reflects_fraction_of_components_present():
    facts = {"has_data": True, "new_positions": 1, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("NVDA", institutional_facts=facts)
    assert score.data_quality_score == round(1 / len(big_money.ALL_COMPONENTS), 4)


def test_missing_components_never_dragged_toward_zero():
    """A ticker with only ONE strongly positive component available must not
    have its composite diluted toward 0 by phantom "0" values for the
    missing ones - missing components are excluded, not zeroed."""
    facts = {"has_data": True, "new_positions": 5, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("NVDA", institutional_facts=facts)
    assert score.composite_score == 1.0  # not diluted by absent components


# --- the hard governance rule: never bypasses/promotes existing gates --------------


def test_ranking_filter_never_promotes_an_avoid_labeled_entry():
    entry = {"symbol": "NVDA", "label": "Avoid", "best_risk_result": None}
    facts = {"has_data": True, "new_positions": 5, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("NVDA", institutional_facts=facts)
    config = {"big_money": {"enabled": True, "use_for_ranking": True, "ranking_caution_threshold": -5.0}}

    big_money.apply_big_money_ranking_filter([entry], {"NVDA": score}, config)

    assert entry["label"] == "Avoid"  # unchanged
    assert "best_risk_result" in entry and entry["best_risk_result"] is None  # unchanged
    assert "big_money_notes" not in entry  # never annotated for an Avoid entry either


def test_ranking_filter_never_touches_risk_regime_or_portfolio_fields():
    entry = {
        "symbol": "AMD", "label": "Strong candidate",
        "best_risk_result": {"tradeable": True, "shares": 10},
        "regime_evaluation": {"blocked": False},
        "portfolio_evaluation": {"decision": "ACCEPT"},
    }
    facts = {"has_data": True, "new_positions": 0, "increased_positions": 0, "reduced_positions": 5, "exited_positions": 0}
    score = big_money.compute_big_money_score("AMD", institutional_facts=facts)
    config = {"big_money": {"enabled": True, "use_for_ranking": True, "ranking_caution_threshold": 0.5}}

    before = dict(entry)
    big_money.apply_big_money_ranking_filter([entry], {"AMD": score}, config)

    assert entry["best_risk_result"] == before["best_risk_result"]
    assert entry["regime_evaluation"] == before["regime_evaluation"]
    assert entry["portfolio_evaluation"] == before["portfolio_evaluation"]
    assert entry["label"] == before["label"]


def test_ranking_filter_disabled_still_attaches_score_for_reporting():
    entry = {"symbol": "AMD", "label": "Strong candidate"}
    config = {"big_money": {"enabled": True, "use_for_ranking": False}}
    big_money.apply_big_money_ranking_filter([entry], {}, config)
    assert "big_money_score" in entry
    assert entry["big_money_score"] is None  # no score computed for AMD in this call


def test_big_money_module_disabled_clears_score_field():
    entry = {"symbol": "AMD", "label": "Strong candidate", "big_money_score": "stale"}
    config = {"big_money": {"enabled": False}}
    big_money.apply_big_money_ranking_filter([entry], {}, config)
    assert entry["big_money_score"] is None
