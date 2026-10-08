"""report_writer.py's new multi-agent-research display functions -
format_agent_research_context_line() and format_agent_research_section().
Mirrors the existing test-free-but-simple pattern of this module's other
context-line helpers; added here since this is the first dedicated test
touching report_writer.py's agent-research additions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import report_writer
from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment


def make_assessment(action="BUY", confidence=0.6, bull=1, bear=0):
    opinions = {"technical": AgentOpinion(analyst="technical", action=action, confidence=confidence, thesis="t")}
    return AgentResearchAssessment(
        ticker="AMD", report_date="2026-09-09", as_of="2026-09-09T00:00:00+00:00", action=action, confidence=confidence,
        thesis="thesis", bull_points=["b"] * bull, bear_points=["b"] * bear, risk_notes=[], analyst_opinions=opinions,
        data_provenance=[],
    )


def test_context_line_is_empty_with_no_assessment():
    assert report_writer.format_agent_research_context_line({}) == ""


def test_context_line_reports_action_and_confidence():
    line = report_writer.format_agent_research_context_line({"agent_assessment": make_assessment(action="BUY", confidence=0.75)})
    assert "SHADOW" in line
    assert "BUY" in line
    assert "75%" in line


def test_section_reports_data_unavailable_with_no_assessments():
    text = report_writer.format_agent_research_section([{"symbol": "AMD"}], {})
    assert "Data Unavailable" in text


def test_section_is_skipped_when_intelligence_disabled():
    assert report_writer.format_agent_research_section([{"symbol": "AMD"}], {"intelligence": {"enabled": False}}) == ""


def test_section_tallies_actions_and_agreement_with_quant_agent():
    quant = type("QA", (), {"decision": "ELIGIBLE"})()
    entries = [
        {"symbol": "AMD", "agent_assessment": make_assessment(action="BUY"), "quant_assessment": quant},
        {"symbol": "NVDA", "agent_assessment": make_assessment(action="SELL"), "quant_assessment": quant},
    ]
    text = report_writer.format_agent_research_section(entries, {})
    assert "BUY 1 / HOLD 0 / SELL 1" in text
    assert "Agreement with existing quant_agent decision: 1/2 (50%)" in text


# --- format_tradingagents_context_line (GitHub Issue #1 follow-up: per-ticker report) --


def _ta_assessment(action="BUY", confidence=0.9, bull=None, bear=None, quant_decision=None, risk_notes=None):
    from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment

    opinions = {"tradingagents_upstream": AgentOpinion(analyst="tradingagents_upstream", action=action, confidence=confidence, thesis="t")}
    return AgentResearchAssessment(
        ticker="AMD", report_date="2026-09-09", as_of="2026-09-09T00:00:00+00:00", action=action, confidence=confidence,
        thesis="thesis", bull_points=[bull] if bull else [], bear_points=[bear] if bear else [],
        risk_notes=risk_notes or [], analyst_opinions=opinions, data_provenance=[], quant_agent_decision=quant_decision,
    )


def test_tradingagents_context_line_is_empty_with_no_assessment():
    assert report_writer.format_tradingagents_context_line({}) == ""


def test_tradingagents_context_line_shows_action_and_confidence():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="SELL", confidence=0.8)})
    assert "SELL" in line
    assert "80%" in line


def test_tradingagents_context_line_shows_bull_and_bear_summaries():
    line = report_writer.format_tradingagents_context_line(
        {"tradingagents_assessment": _ta_assessment(bull="Strong earnings beat.", bear="Valuation looks stretched.")}
    )
    assert "Bull case: Strong earnings beat." in line
    assert "Bear case: Valuation looks stretched." in line


def test_tradingagents_context_line_truncates_long_bull_bear_text():
    long_text = "x" * 500
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(bull=long_text)})
    assert "…" in line
    assert len(long_text) > 200 and ("x" * 201) not in line


def test_tradingagents_context_line_reports_no_debate_content_when_both_are_empty():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment()})
    assert "no debate content" in line


def test_tradingagents_context_line_shows_quant_comparison_agreement():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="BUY", quant_decision="ELIGIBLE")})
    assert "ELIGIBLE" in line
    assert "agrees" in line


def test_tradingagents_context_line_shows_quant_comparison_disagreement():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="SELL", quant_decision="ELIGIBLE")})
    assert "disagrees" in line


def test_tradingagents_context_line_shows_no_quant_comparison_when_unavailable():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(quant_decision=None)})
    assert "no quant_agent assessment available" in line


def test_tradingagents_context_line_includes_cost_and_token_risk_notes():
    notes = ["Raw upstream rating: Buy", "Estimated cost: $0.0312 (real token usage x configured pricing).", "Token usage: gpt-6-sol: in=1,000 out=500"]
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(risk_notes=notes)})
    assert "Estimated cost: $0.0312" in line
    assert "Token usage: gpt-6-sol" in line
