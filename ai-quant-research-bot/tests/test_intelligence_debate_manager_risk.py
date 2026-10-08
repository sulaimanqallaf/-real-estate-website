"""src/intelligence/debate.py, research_manager.py, risk_reviewer.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.intelligence import debate, research_manager, risk_reviewer
from src.intelligence.schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL, AgentOpinion


def opinion(analyst, action, confidence=0.8, data_available=True, thesis=None):
    return AgentOpinion(analyst=analyst, action=action, confidence=confidence, thesis=thesis or f"{analyst} says {action}", evidence=[f"{analyst} evidence"], data_available=data_available)


# --- debate -----------------------------------------------------------------------------


def test_debate_verdict_is_buy_when_bulls_outweigh_bears():
    opinions = {"technical": opinion("technical", ACTION_BUY, 0.9), "fundamentals": opinion("fundamentals", ACTION_SELL, 0.1)}
    result = debate.run_debate(opinions)
    assert result.verdict == ACTION_BUY
    assert len(result.bull_points) == 1 and len(result.bear_points) == 1


def test_debate_verdict_is_hold_on_a_tie():
    opinions = {"technical": opinion("technical", ACTION_BUY, 0.5), "fundamentals": opinion("fundamentals", ACTION_SELL, 0.5)}
    assert debate.run_debate(opinions).verdict == ACTION_HOLD


def test_debate_ignores_data_unavailable_opinions():
    opinions = {"technical": opinion("technical", ACTION_BUY, 0.9, data_available=False)}
    result = debate.run_debate(opinions)
    assert result.verdict == ACTION_HOLD
    assert result.bull_points == []


def test_debate_holds_when_every_opinion_is_hold():
    opinions = {"technical": opinion("technical", ACTION_HOLD, 0.0)}
    assert debate.run_debate(opinions).verdict == ACTION_HOLD


# --- research_manager ---------------------------------------------------------------------


def test_research_manager_follows_the_debate_verdict_exactly():
    opinions = {"technical": opinion("technical", ACTION_BUY, 0.9)}
    debate_result = debate.run_debate(opinions)
    action, confidence, thesis = research_manager.synthesize_recommendation(debate_result, opinions)
    assert action == ACTION_BUY
    assert confidence > 0
    assert "technical" in thesis


def test_research_manager_zero_confidence_on_hold_with_no_data():
    opinions = {"technical": opinion("technical", ACTION_BUY, 0.9, data_available=False)}
    debate_result = debate.run_debate(opinions)
    action, confidence, thesis = research_manager.synthesize_recommendation(debate_result, opinions)
    assert action == ACTION_HOLD
    assert confidence == 0.0


# --- risk_reviewer --------------------------------------------------------------------------


def _entry(**overrides):
    entry = {
        "label": "Buy", "best_risk_result": {"tradeable": True}, "regime_evaluation": {"blocked": False},
        "portfolio_evaluation": {"decision": "ACCEPT"},
    }
    entry.update(overrides)
    return entry


def test_risk_reviewer_passes_through_an_eligible_buy():
    action, confidence, notes = risk_reviewer.review(_entry(), ACTION_BUY, 0.7, "thesis")
    assert action == ACTION_BUY
    assert confidence == 0.7


def test_risk_reviewer_vetoes_a_buy_when_upstream_already_rejected():
    action, confidence, notes = risk_reviewer.review(_entry(label="Avoid"), ACTION_BUY, 0.9, "thesis")
    assert action == ACTION_HOLD
    assert confidence == 0.0
    assert any("Vetoed" in n for n in notes)


def test_risk_reviewer_vetoes_a_buy_when_portfolio_risk_rejected():
    action, confidence, notes = risk_reviewer.review(_entry(portfolio_evaluation={"decision": "REJECT"}), ACTION_BUY, 0.9, "thesis")
    assert action == ACTION_HOLD


def test_risk_reviewer_never_upgrades_a_hold_to_buy():
    action, confidence, notes = risk_reviewer.review(_entry(label="Avoid"), ACTION_HOLD, 0.0, "thesis")
    assert action == ACTION_HOLD
    assert notes == []  # a HOLD never needed a veto in the first place
