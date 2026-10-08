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
