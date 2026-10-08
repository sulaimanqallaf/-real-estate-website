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


def _ta_assessment(action="BUY", confidence=0.9, bull=None, bear=None, quant_decision=None, risk_notes=None, raw_label=None):
    from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment

    opinions = {"tradingagents_upstream": AgentOpinion(analyst="tradingagents_upstream", action=action, confidence=confidence, thesis="t", raw_label=raw_label)}
    return AgentResearchAssessment(
        ticker="AMD", report_date="2026-09-09", as_of="2026-09-09T00:00:00+00:00", action=action, confidence=confidence,
        thesis="thesis", bull_points=[bull] if bull else [], bear_points=[bear] if bear else [],
        risk_notes=risk_notes or [], analyst_opinions=opinions, data_provenance=[], quant_agent_decision=quant_decision,
        raw_label=raw_label,
    )


def test_tradingagents_context_line_is_empty_with_no_assessment():
    assert report_writer.format_tradingagents_context_line({}) == ""


def test_tradingagents_context_line_shows_action_and_confidence():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="SELL", confidence=0.8)})
    assert "SELL" in line
    assert "80%" in line


def test_tradingagents_context_line_shows_confidence_as_unavailable_when_uncalibrated():
    """GitHub Issue #1 follow-up: upstream TradingAgents gives no
    calibrated success probability - this must never be rendered as a
    fabricated percentage."""
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(confidence=None)})
    assert "confidence 100%" not in line
    assert "confidence unavailable (uncalibrated)" in line


def test_tradingagents_context_line_preserves_the_original_raw_label_distinct_from_the_normalized_action():
    """GitHub Issue #1 follow-up: QQQ's raw rating was Overweight, but
    the normalized action is BUY - both must be shown, clearly
    distinguished, never one silently standing in for the other."""
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="BUY", raw_label="Overweight")})
    assert "BUY" in line
    assert "raw_rating=OVERWEIGHT" in line


def test_tradingagents_context_line_omits_the_raw_label_annotation_when_it_already_matches_the_action():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="SELL", raw_label="Sell")})
    assert "raw_rating" not in line


def test_tradingagents_context_line_shows_bull_and_bear_summaries():
    line = report_writer.format_tradingagents_context_line(
        {"tradingagents_assessment": _ta_assessment(bull="Strong earnings beat.", bear="Valuation looks stretched.")}
    )
    assert "Bull case: Strong earnings beat." in line
    assert "Bear case: Valuation looks stretched." in line


def test_tradingagents_context_line_never_re_truncates_an_already_prepared_excerpt():
    """Truncation/labeling now happens once, upstream, in
    tradingagents_adapter._summarize_debate_text() - this layer must
    display exactly what it is given, never cut it a second time."""
    already_labeled = "Bull: margins improving… (excerpt, 120 of 900 chars - see cached transcript for the full debate)"
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(bull=already_labeled)})
    assert already_labeled in line


def test_tradingagents_context_line_reports_no_debate_content_when_both_are_empty():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment()})
    assert "no debate content" in line


def test_tradingagents_context_line_never_claims_agreement_for_an_eligible_quant_decision():
    """GitHub Issue #1 follow-up: ELIGIBLE means a candidate passed the
    Quant gate, not that it is directionally BUY - never claim agree/
    disagree against an upstream BUY/SELL/HOLD call."""
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="BUY", quant_decision="ELIGIBLE")})
    assert "Quant status: ELIGIBLE - directional comparison unavailable." in line
    assert "agrees" not in line
    assert "disagrees" not in line


def test_tradingagents_context_line_never_claims_disagreement_either():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(action="SELL", quant_decision="NOT_ELIGIBLE")})
    assert "Quant status: NOT_ELIGIBLE - directional comparison unavailable." in line
    assert "agrees" not in line
    assert "disagrees" not in line


def test_tradingagents_context_line_shows_no_quant_comparison_when_unavailable():
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(quant_decision=None)})
    assert "no quant_agent assessment available" in line


def test_tradingagents_context_line_includes_cost_and_token_risk_notes():
    notes = ["Raw upstream rating: Buy", "Estimated cost: $0.0312 (real token usage x configured pricing).", "Token usage: gpt-6-sol: in=1,000 out=500"]
    line = report_writer.format_tradingagents_context_line({"tradingagents_assessment": _ta_assessment(risk_notes=notes)})
    assert "Estimated cost: $0.0312" in line
    assert "Token usage: gpt-6-sol" in line
