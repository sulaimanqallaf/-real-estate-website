"""src/intelligence/memory.py, reflection.py, evaluation.py - persistent
memory, reflection on past misses, and comparison against the existing
quant_agent/decision_ledger. Uses the REAL SQLite databases (tmp_path),
not mocks, since these modules' whole job is the join between two
intentionally separate data models."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import evaluation, memory, reflection
from src.intelligence.schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL, AgentOpinion, AgentResearchAssessment
from src.ml import decision_ledger


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def make_assessment(ticker="AMD", report_date="2026-09-09", action=ACTION_BUY, confidence=0.7, quant_agent_decision="ELIGIBLE"):
    opinions = {"technical": AgentOpinion(analyst="technical", action=action, confidence=confidence, thesis="t", evidence=["e"])}
    return AgentResearchAssessment(
        ticker=ticker, report_date=report_date, as_of="2026-09-09T12:00:00+00:00", action=action, confidence=confidence,
        thesis="thesis", bull_points=["bull"], bear_points=[], risk_notes=[], analyst_opinions=opinions,
        data_provenance=["technical: available"], quant_agent_decision=quant_agent_decision,
    )


# --- memory --------------------------------------------------------------------------------


def test_resolve_db_path_colocates_with_journal_dir(config):
    path = memory.resolve_db_path(config)
    assert path.parent == Path(config["data"]["journal_dir"])


def test_record_and_query_assessment_round_trip(config):
    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment())
    rows = memory.query_assessments(db_path, ticker="AMD")
    assert len(rows) == 1
    assert rows[0]["action"] == ACTION_BUY
    assert rows[0]["bull_points"] == ["bull"]


def test_query_assessments_returns_empty_list_for_a_missing_database(config):
    assert memory.query_assessments(memory.resolve_db_path(config)) == []


def test_record_reflection_sets_the_field_without_touching_anything_else(config):
    db_path = memory.resolve_db_path(config)
    assessment_id = memory.record_assessment(db_path, make_assessment())
    memory.record_reflection(db_path, assessment_id, "Missed: predicted BUY, trade lost.")
    rows = memory.query_assessments(db_path)
    assert rows[0]["reflection_note"] == "Missed: predicted BUY, trade lost."
    assert rows[0]["action"] == ACTION_BUY  # untouched


def test_fetch_recent_reflections_returns_only_reflected_rows(config):
    db_path = memory.resolve_db_path(config)
    aid = memory.record_assessment(db_path, make_assessment(report_date="2026-09-01"))
    memory.record_assessment(db_path, make_assessment(report_date="2026-09-02"))  # no reflection yet
    memory.record_reflection(db_path, aid, "Correct: agreed.")
    assert memory.fetch_recent_reflections(db_path, "AMD") == ["Correct: agreed."]


# --- reflection ------------------------------------------------------------------------------


def test_generate_reflections_skips_holds_and_unresolved_rows(config, caplog):
    import logging

    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(action=ACTION_HOLD))
    memory.record_assessment(db_path, make_assessment(ticker="NVDA", action=ACTION_BUY))  # no matching ledger outcome
    written = reflection.generate_reflections(config, logging.getLogger("test"))
    assert written == []


def test_generate_reflections_flags_a_missed_buy_call(config):
    import logging

    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(ticker="AMD", report_date="2026-09-09", action=ACTION_BUY))

    ledger_db = decision_ledger.resolve_db_path(config)
    decision_ledger.record_decision(ledger_db, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09", strategy="Trend Following", trade_id="t1")
    decision_ledger.record_outcome(ledger_db, trade_id="t1", outcome_status="STOPPED", pnl_pct=-0.05)

    written = reflection.generate_reflections(config, logging.getLogger("test"))
    assert len(written) == 1
    assert written[0]["agreed"] is False
    assert "Missed" in written[0]["note"]

    rows = memory.query_assessments(db_path, ticker="AMD")
    assert rows[0]["reflection_note"] is not None


def test_generate_reflections_flags_a_correct_sell_call(config):
    import logging

    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(ticker="AMD", report_date="2026-09-09", action=ACTION_SELL))

    ledger_db = decision_ledger.resolve_db_path(config)
    decision_ledger.record_decision(ledger_db, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09", trade_id="t1")
    decision_ledger.record_outcome(ledger_db, trade_id="t1", outcome_status="STOPPED", pnl_pct=-0.05)

    written = reflection.generate_reflections(config, logging.getLogger("test"))
    assert written[0]["agreed"] is True
    assert "Correct" in written[0]["note"]


def test_generate_reflections_never_overwrites_an_existing_reflection(config):
    import logging

    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(ticker="AMD", report_date="2026-09-09", action=ACTION_BUY))
    ledger_db = decision_ledger.resolve_db_path(config)
    decision_ledger.record_decision(ledger_db, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09", trade_id="t1")
    decision_ledger.record_outcome(ledger_db, trade_id="t1", outcome_status="TARGET_HIT", pnl_pct=0.05)

    first = reflection.generate_reflections(config, logging.getLogger("test"))
    assert len(first) == 1
    second = reflection.generate_reflections(config, logging.getLogger("test"))
    assert second == []  # already reflected on - never re-processed


# --- evaluation -----------------------------------------------------------------------------


def test_compare_against_quant_agent_computes_agreement_rate(config):
    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(ticker="AMD", action=ACTION_BUY, quant_agent_decision="ELIGIBLE"))
    memory.record_assessment(db_path, make_assessment(ticker="NVDA", action=ACTION_SELL, quant_agent_decision="ELIGIBLE"))

    summary = evaluation.compare_against_quant_agent(config)
    assert summary["compared_with_quant_agent"] == 2
    assert summary["agreement_count"] == 1
    assert summary["agreement_pct"] == 50.0


def test_compare_against_quant_agent_scores_accuracy_against_resolved_outcomes(config):
    db_path = memory.resolve_db_path(config)
    memory.record_assessment(db_path, make_assessment(ticker="AMD", report_date="2026-09-09", action=ACTION_BUY, quant_agent_decision="NOT_ELIGIBLE"))

    ledger_db = decision_ledger.resolve_db_path(config)
    decision_ledger.record_decision(ledger_db, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09", trade_id="t1")
    decision_ledger.record_outcome(ledger_db, trade_id="t1", outcome_status="TARGET_HIT", pnl_pct=0.05)

    summary = evaluation.compare_against_quant_agent(config)
    assert summary["resolved_directional_trades"] == 1
    assert summary["agent_correct"] == 1  # predicted BUY, trade was profitable
    assert summary["quant_agent_correct"] == 0  # quant_agent said NOT_ELIGIBLE (mapped to SELL), trade was profitable


def test_format_comparison_report_never_raises_on_an_empty_summary(config):
    summary = evaluation.compare_against_quant_agent(config)
    text = evaluation.format_comparison_report(summary)
    assert "SHADOW MODE" in text
