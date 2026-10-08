"""Four analyst functions - technical, fundamentals, sentiment, news -
inspired by TradingAgents' analyst roster (GitHub Issue #1 comment), but
built from data this codebase ALREADY computes per ticker (`indicators.py`,
`market_regime.py`, `big_money.py`'s institutional/insider/options-flow/
sector components), not a port of upstream code.

**No LLM is called here.** Each analyst is a deterministic, rule-based
synthesis of already-computed numeric signals into a structured
`AgentOpinion` - this keeps the whole research layer reproducible,
free, and testable without network access or API keys, while still
being pluggable: a real LLM or news/fundamentals provider can be
dropped in later behind the same `AgentOpinion` contract. Every analyst
degrades to `data_available=False` with an explicit "Data Unavailable"
thesis rather than ever inventing a signal - the same discipline
`big_money.py`, `data_collector.py`, and `ml/predictor.py` already use
throughout this codebase.
"""

from __future__ import annotations

from typing import Any

from .schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL, ANALYST_FUNDAMENTALS, ANALYST_NEWS, ANALYST_SENTIMENT, ANALYST_TECHNICAL, AgentOpinion


def _action_from_score(score: float, bullish_threshold: float = 0.15, bearish_threshold: float = -0.15) -> str:
    if score >= bullish_threshold:
        return ACTION_BUY
    if score <= bearish_threshold:
        return ACTION_SELL
    return ACTION_HOLD


def technical_analyst(entry: dict[str, Any]) -> AgentOpinion:
    """Reads the rule-based signal score, regime fit, and identified
    strategy already on `entry` - the same facts `report_writer.py`
    already displays, never anything new fetched from the market."""
    rule_score = entry.get("score", 0) or 0
    regime_eval = entry.get("regime_evaluation") or {}
    regime = regime_eval.get("regime", "UNKNOWN")
    blocked = bool(regime_eval.get("blocked"))
    strategy = (entry.get("best_risk_result") or {}).get("strategy", "Unknown")

    # score is 0-100; rescale to a -1..+1 "technical strength" the same
    # way quant_agent.py already treats rule_score as the primary signal.
    normalized = (rule_score - 50.0) / 50.0
    evidence = [f"Rule-based signal score: {rule_score}/100 (strategy: {strategy}).", f"Market regime: {regime}."]
    if blocked:
        evidence.append("Regime filter currently BLOCKS new entries for this ticker.")
        return AgentOpinion(
            analyst=ANALYST_TECHNICAL, action=ACTION_HOLD, confidence=0.6,
            thesis=f"Technical setup for {strategy} is blocked by the current {regime} regime filter.",
            evidence=evidence,
        )

    action = _action_from_score(normalized)
    confidence = min(1.0, abs(normalized))
    thesis = f"{strategy} signal score of {rule_score}/100 in a {regime} regime suggests a {action} technical bias."
    return AgentOpinion(analyst=ANALYST_TECHNICAL, action=action, confidence=confidence, thesis=thesis, evidence=evidence)


def fundamentals_analyst(entry: dict[str, Any]) -> AgentOpinion:
    """**Not full fundamental financials** (no earnings/revenue/valuation
    provider is wired into this codebase yet) - uses the institutional
    13F accumulation and Form 4 insider-transaction components
    `big_money.py` already computes, clearly labeled as ownership/insider
    context rather than claimed as traditional fundamental analysis.
    Degrades to Data Unavailable when `big_money.py` didn't run or had
    nothing for this ticker - never invents a fundamentals stance."""
    big_money = entry.get("big_money_score")
    components = getattr(big_money, "components", None) or {}
    institutional = components.get("institutional_accumulation_score")
    insider = components.get("insider_score")

    available = [v for v in (institutional, insider) if v is not None]
    if not available:
        return AgentOpinion(
            analyst=ANALYST_FUNDAMENTALS, action=ACTION_HOLD, confidence=0.0,
            thesis="No institutional 13F or insider Form 4 data is available for this ticker - Data Unavailable.",
            evidence=["institutional_accumulation_score: unavailable", "insider_score: unavailable"],
            data_available=False,
        )

    score = sum(available) / len(available)
    action = _action_from_score(score)
    confidence = min(1.0, abs(score))
    evidence = [
        f"Institutional 13F accumulation score: {institutional:+.2f}." if institutional is not None else "Institutional 13F data: unavailable.",
        f"Insider Form 4 transaction score: {insider:+.2f}." if insider is not None else "Insider Form 4 data: unavailable.",
    ]
    thesis = f"Institutional/insider ownership activity nets to a {action} lean (ownership context, not earnings/valuation)."
    return AgentOpinion(analyst=ANALYST_FUNDAMENTALS, action=action, confidence=confidence, thesis=thesis, evidence=evidence)


def sentiment_analyst(entry: dict[str, Any]) -> AgentOpinion:
    """Uses the options-flow and sector-rotation components `big_money.py`
    already computes as a sentiment proxy. Degrades to Data Unavailable
    when neither is present - never invents a sentiment reading from
    article text this codebase doesn't fetch."""
    big_money = entry.get("big_money_score")
    components = getattr(big_money, "components", None) or {}
    options_flow = components.get("options_flow_score")
    sector_flow = components.get("sector_flow_score")

    available = [v for v in (options_flow, sector_flow) if v is not None]
    if not available:
        return AgentOpinion(
            analyst=ANALYST_SENTIMENT, action=ACTION_HOLD, confidence=0.0,
            thesis="No options-flow or sector-rotation data is available for this ticker - Data Unavailable.",
            evidence=["options_flow_score: unavailable", "sector_flow_score: unavailable"],
            data_available=False,
        )

    score = sum(available) / len(available)
    action = _action_from_score(score)
    confidence = min(1.0, abs(score))
    evidence = [
        f"Options flow score: {options_flow:+.2f}." if options_flow is not None else "Options flow data: unavailable.",
        f"Sector rotation score: {sector_flow:+.2f}." if sector_flow is not None else "Sector rotation data: unavailable.",
    ]
    thesis = f"Options flow / sector rotation nets to a {action} sentiment lean."
    return AgentOpinion(analyst=ANALYST_SENTIMENT, action=action, confidence=confidence, thesis=thesis, evidence=evidence)


def news_analyst(entry: dict[str, Any], news_provider: Any = None) -> AgentOpinion:
    """**No news provider is configured in this codebase.** This analyst
    exists as a structural placeholder with a real, pluggable contract
    (`news_provider` - any object with a `.headlines(ticker) -> list[str]`
    method) so a real news/LLM-summarization source can be dropped in
    later without touching the pipeline. Until then it always reports
    Data Unavailable - never fabricates a news-driven thesis, and never
    treats untrusted article text as anything but untrusted input (see
    pipeline.py's prompt-injection note) once a provider IS wired in."""
    if news_provider is None:
        return AgentOpinion(
            analyst=ANALYST_NEWS, action=ACTION_HOLD, confidence=0.0,
            thesis="No news provider is configured - Data Unavailable.",
            evidence=["news_provider: not configured"],
            data_available=False,
        )
    try:
        headlines = news_provider.headlines(entry.get("symbol", ""))
    except Exception:  # noqa: BLE001 - a provider failure degrades, never crashes the pipeline
        headlines = []
    if not headlines:
        return AgentOpinion(
            analyst=ANALYST_NEWS, action=ACTION_HOLD, confidence=0.0,
            thesis="News provider returned no headlines - Data Unavailable.",
            evidence=["news_provider: no headlines returned"],
            data_available=False,
        )
    # Headlines are untrusted external text - this analyst only ever
    # counts/echoes them as evidence strings, never executes or follows
    # any instruction-shaped content found inside them.
    return AgentOpinion(
        analyst=ANALYST_NEWS, action=ACTION_HOLD, confidence=0.2,
        thesis=f"{len(headlines)} headline(s) retrieved; no scoring model is wired in yet to turn them into a directional view.",
        evidence=[f"Headline (untrusted, unscored): {h}" for h in headlines[:5]],
    )


def run_all_analysts(entry: dict[str, Any], news_provider: Any = None) -> dict[str, AgentOpinion]:
    return {
        ANALYST_TECHNICAL: technical_analyst(entry),
        ANALYST_FUNDAMENTALS: fundamentals_analyst(entry),
        ANALYST_SENTIMENT: sentiment_analyst(entry),
        ANALYST_NEWS: news_analyst(entry, news_provider=news_provider),
    }
