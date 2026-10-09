import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources.decision_events import events_for_decision_row


def make_row(**overrides):
    row = {
        "ticker": "AMD", "strategy": "momentum_breakout", "decision": "AUTO_EXECUTE",
        "regime": "BULL_TREND", "signal_score": 85, "outcome_status": None,
        "provenance": None, "trade_id": None, "dollar_risk": 100.0, "decision_reasons": None,
        "as_of": "2026-10-28T20:30:00+00:00",
    }
    row.update(overrides)
    return row


def test_a_minimal_row_produces_scan_risk_decision_and_strategy_note_but_no_debate_or_execution():
    events = events_for_decision_row(make_row(), mode="live")
    types = [e.event_type for e in events]
    assert types == ["scan", "risk_check", "decision", "info"]


def test_a_row_with_provenance_adds_a_bull_and_bear_debate_event():
    events = events_for_decision_row(make_row(provenance="deterministic_pipeline"), mode="live")
    debate_agents = [e.agent for e in events if e.event_type == "debate"]
    assert debate_agents == ["bull_analyst", "bear_analyst"]


def test_a_row_with_a_trade_id_adds_an_execution_event_for_the_execution_agent():
    events = events_for_decision_row(make_row(trade_id="t-123"), mode="live")
    execution_events = [e for e in events if e.event_type == "execution"]
    assert len(execution_events) == 1
    assert execution_events[0].agent == "execution_agent"
    assert execution_events[0].data["trade_id"] == "t-123"


def test_a_row_with_an_outcome_adds_a_learning_event():
    events = events_for_decision_row(make_row(outcome_status="WIN", pnl_pct=4.2), mode="live")
    learning_events = [e for e in events if e.event_type == "learning"]
    assert len(learning_events) == 1
    assert learning_events[0].agent == "learning_agent"


def test_mode_is_stamped_on_every_event_in_the_sequence():
    events = events_for_decision_row(make_row(trade_id="t-1", outcome_status="WIN", provenance="x"), mode="replay")
    assert all(e.mode == "replay" for e in events)


def test_every_event_carries_the_rows_own_ticker():
    events = events_for_decision_row(make_row(ticker="QQQ"), mode="live")
    assert all(e.ticker == "QQQ" for e in events)
