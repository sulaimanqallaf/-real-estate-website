"""Bull vs. Bear debate (TradingAgents-inspired) - a transparent tally
across every analyst's `AgentOpinion`, not an LLM-generated argument.
Each analyst's thesis becomes either a bull point or a bear point based
on its own reported action; a HOLD contributes to neither side but is
never silently dropped - `research_manager.py` still sees it via the
opinions dict."""

from __future__ import annotations

from .schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL, AgentOpinion, DebateResult


def run_debate(opinions: dict[str, AgentOpinion]) -> DebateResult:
    bull_points: list[str] = []
    bear_points: list[str] = []
    bull_score = 0.0
    bear_score = 0.0

    for opinion in opinions.values():
        if not opinion.data_available:
            continue  # Data Unavailable opinions never count toward either side
        if opinion.action == ACTION_BUY:
            bull_points.append(f"[{opinion.analyst}] {opinion.thesis}")
            bull_score += opinion.confidence
        elif opinion.action == ACTION_SELL:
            bear_points.append(f"[{opinion.analyst}] {opinion.thesis}")
            bear_score += opinion.confidence

    if bull_score > bear_score and bull_points:
        verdict = ACTION_BUY
    elif bear_score > bull_score and bear_points:
        verdict = ACTION_SELL
    else:
        verdict = ACTION_HOLD

    return DebateResult(bull_points=bull_points, bear_points=bear_points, bull_score=round(bull_score, 3), bear_score=round(bear_score, 3), verdict=verdict)
