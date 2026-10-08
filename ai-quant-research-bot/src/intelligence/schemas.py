"""Structured, auditable types for the multi-agent research layer. Every
field here is either a plain fact already computed elsewhere in this
codebase, or an explicitly-labeled research OPINION - never a broker
price, a position size, or anything `execution_policy.py` would read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_HOLD = "HOLD"
ALL_ACTIONS = (ACTION_BUY, ACTION_SELL, ACTION_HOLD)

ANALYST_TECHNICAL = "technical"
ANALYST_FUNDAMENTALS = "fundamentals"
ANALYST_SENTIMENT = "sentiment"
ANALYST_NEWS = "news"
ALL_ANALYSTS = (ANALYST_TECHNICAL, ANALYST_FUNDAMENTALS, ANALYST_SENTIMENT, ANALYST_NEWS)


@dataclass(frozen=True)
class AgentOpinion:
    """One analyst's structured opinion on one ticker. `confidence` is
    0.0-1.0 and is a SELF-REPORTED heuristic strength, not a calibrated
    probability (see `research_manager.py`'s docstring on why this is
    never represented as calibrated unless empirically validated).
    `data_available=False` means the opinion was built entirely from
    "Data Unavailable" degradation - never fabricated analysis."""

    analyst: str
    action: str
    confidence: float
    thesis: str
    evidence: list[str] = field(default_factory=list)
    data_available: bool = True


@dataclass(frozen=True)
class DebateResult:
    """Bull vs. Bear synthesis across every analyst's opinion - a
    transparent tally, not an LLM-generated argument (no LLM is wired
    into this module at all; see pipeline.py)."""

    bull_points: list[str]
    bear_points: list[str]
    bull_score: float
    bear_score: float
    verdict: str  # one of ALL_ACTIONS


@dataclass(frozen=True)
class AgentResearchAssessment:
    """The one structured, auditable output of the whole pipeline for one
    ticker on one report date. Always attached to `entry["agent_assessment"]`
    for reporting; NEVER read by `execution_policy.py`, `circuit_breaker.py`,
    or any code path that can reach a broker - see pipeline.py."""

    ticker: str
    report_date: str
    as_of: str
    action: str
    confidence: float
    thesis: str
    bull_points: list[str]
    bear_points: list[str]
    risk_notes: list[str]
    analyst_opinions: dict[str, AgentOpinion]
    data_provenance: list[str]
    quant_agent_decision: str | None = None
    reflection_note: str | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "report_date": self.report_date, "as_of": self.as_of,
            "action": self.action, "confidence": self.confidence, "thesis": self.thesis,
            "bull_points": list(self.bull_points), "bear_points": list(self.bear_points),
            "risk_notes": list(self.risk_notes),
            "analyst_opinions": {
                name: {
                    "action": op.action, "confidence": op.confidence, "thesis": op.thesis,
                    "evidence": list(op.evidence), "data_available": op.data_available,
                }
                for name, op in self.analyst_opinions.items()
            },
            "data_provenance": list(self.data_provenance),
            "quant_agent_decision": self.quant_agent_decision,
            "reflection_note": self.reflection_note,
            "duration_ms": self.duration_ms,
        }
