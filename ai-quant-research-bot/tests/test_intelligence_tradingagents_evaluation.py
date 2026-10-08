"""src/intelligence/tradingagents_evaluation.py - the three-way
comparison (upstream TradingAgents vs. the deterministic engine vs.
quant_agent) against real resolved outcomes. Uses real SQLite databases
(tmp_path), same as the deterministic engine's own evaluation tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import memory, tradingagents_adapter, tradingagents_evaluation
from src.intelligence.schemas import ACTION_BUY, ACTION_SELL, AgentOpinion, AgentResearchAssessment
from src.ml import decision_ledger


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def make_assessment(ticker, report_date, action, quant_agent_decision=None, duration_ms=None):
    opinions = {"x": AgentOpinion(analyst="x", action=action, confidence=0.5, thesis="t")}
    return AgentResearchAssessment(
        ticker=ticker, report_date=report_date, as_of=f"{report_date}T00:00:00+00:00", action=action, confidence=0.5,
        thesis="t", bull_points=[], bear_points=[], risk_notes=[], analyst_opinions=opinions, data_provenance=[],
        quant_agent_decision=quant_agent_decision, duration_ms=duration_ms,
    )


def test_compare_three_way_reports_none_when_nothing_recorded(config):
    summary = tradingagents_evaluation.compare_three_way(config)
    assert summary["deterministic_engine"]["total_assessments"] == 0
    assert summary["deterministic_engine"]["accuracy_pct"] is None
    assert summary["upstream_tradingagents"]["total_assessments"] == 0


def test_compare_three_way_scores_accuracy_for_both_layers_independently(config):
    det_db = memory.resolve_db_path(config)
    upstream_db = tradingagents_adapter.resolve_memory_db_path(config)
    ledger_db = decision_ledger.resolve_db_path(config)

    memory.record_assessment(det_db, make_assessment("AMD", "2026-09-09", ACTION_BUY, duration_ms=50.0))
    memory.record_assessment(upstream_db, make_assessment("AMD", "2026-09-09", ACTION_SELL, duration_ms=9000.0))

    decision_ledger.record_decision(ledger_db, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09", trade_id="t1")
    decision_ledger.record_outcome(ledger_db, trade_id="t1", outcome_status="TARGET_HIT", pnl_pct=0.05)  # profitable -> BUY was "correct"

    summary = tradingagents_evaluation.compare_three_way(config)
    assert summary["deterministic_engine"]["accuracy_pct"] == 100.0  # predicted BUY, profitable
    assert summary["upstream_tradingagents"]["accuracy_pct"] == 0.0  # predicted SELL, profitable
    assert summary["deterministic_engine"]["avg_duration_ms"] == 50.0
    assert summary["upstream_tradingagents"]["avg_duration_ms"] == 9000.0


def test_compare_three_way_computes_agreement_between_the_two_layers(config):
    det_db = memory.resolve_db_path(config)
    upstream_db = tradingagents_adapter.resolve_memory_db_path(config)

    memory.record_assessment(det_db, make_assessment("AMD", "2026-09-09", ACTION_BUY))
    memory.record_assessment(upstream_db, make_assessment("AMD", "2026-09-09", ACTION_BUY))  # agrees
    memory.record_assessment(det_db, make_assessment("NVDA", "2026-09-09", ACTION_BUY))
    memory.record_assessment(upstream_db, make_assessment("NVDA", "2026-09-09", ACTION_SELL))  # disagrees

    summary = tradingagents_evaluation.compare_three_way(config)
    assert summary["agreement_between_deterministic_and_upstream"]["compared"] == 2
    assert summary["agreement_between_deterministic_and_upstream"]["agreement_count"] == 1
    assert summary["agreement_between_deterministic_and_upstream"]["agreement_pct"] == 50.0


def test_compare_three_way_computes_upstream_vs_quant_agent_agreement(config):
    upstream_db = tradingagents_adapter.resolve_memory_db_path(config)
    memory.record_assessment(upstream_db, make_assessment("AMD", "2026-09-09", ACTION_BUY, quant_agent_decision="ELIGIBLE"))
    memory.record_assessment(upstream_db, make_assessment("NVDA", "2026-09-09", ACTION_SELL, quant_agent_decision="ELIGIBLE"))

    summary = tradingagents_evaluation.compare_three_way(config)
    assert summary["upstream_vs_quant_agent"]["compared"] == 2
    assert summary["upstream_vs_quant_agent"]["agreement_count"] == 1


def test_format_three_way_report_never_raises_on_an_empty_summary(config):
    summary = tradingagents_evaluation.compare_three_way(config)
    text = tradingagents_evaluation.format_three_way_report(summary)
    assert "SHADOW MODE" in text
    assert "dollar cost is not tracked" in text


# --- Telegram summary --------------------------------------------------------------------


def test_send_three_way_report_sends_the_formatted_text(config, monkeypatch):
    sent = {}

    def fake_send(token, chat_id, text, logger):
        sent["text"] = text
        return True

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", fake_send)

    import logging

    result = tradingagents_evaluation.send_three_way_report(config, logging.getLogger("test"), "TOKEN", "123")

    assert result is True
    assert "Three-way research comparison" in sent["text"]
