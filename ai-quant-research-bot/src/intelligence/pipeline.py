"""Multi-agent research layer orchestrator (GitHub Issue #1 comment:
"Proposal: selectively integrate TauricResearch/TradingAgents as a
research-only multi-agent layer", reference
https://github.com/TauricResearch/TradingAgents).

**This is an independent, from-scratch implementation inspired by that
project's architecture (parallel analysts -> bull/bear debate -> research
manager -> risk review -> structured recommendation), not a port or
vendored copy of its code.** No upstream code was reused: before writing
anything here, the upstream repository, its dependencies (LangGraph/
LangChain, a different Python/package footprint than this project's), and
its Apache-2.0 license were reviewed per that comment's own instruction
("Inspect dependencies, architecture and licensing before reusing
upstream code") - the conclusion was to build a lighter, dependency-free,
deterministic equivalent against data this codebase already computes,
rather than add that dependency surface or copy licensed code. If any
upstream code is ever vendored in the future, its Apache-2.0 LICENSE and
NOTICE must ship with it; none has been as of this module.

**Hard invariants, enforced structurally, not just documented:**
1. SHADOW MODE ONLY. `run_shadow_research()` is READ-ONLY with respect to
   every field that can reach the broker or a trading decision
   (`best_risk_result`, `regime_evaluation`, `portfolio_evaluation`,
   `execution_decision`, position sizing). It only ever ADDS
   `entry["agent_assessment"]` for reporting/comparison. It is called from
   `main.py` strictly AFTER `_process_execution_layer()` has already
   decided and (if applicable) submitted every order for this run - the
   call is ordered so it is structurally impossible for this layer to
   have influenced that decision.
2. No LLM, no network call, no API key is used anywhere in this package
   (see `analysts.py`) - every analyst is a deterministic synthesis of
   data this codebase already computed. A future LLM-backed analyst or a
   real news provider can be dropped in behind the same `AgentOpinion`
   contract without touching this orchestrator's shape.
3. Untrusted external text (a future news provider's headlines) is only
   ever treated as evidence to display, never as instructions - see
   `analysts.news_analyst()`.
4. Two separate learning loops are kept apart (`memory.py`'s module
   docstring): this layer's own hypothetical-recommendation memory never
   merges with `ml/decision_ledger.py`'s real broker-paper/simulated
   P&L data model; `evaluation.py`/`reflection.py` only ever JOIN them
   read-only, by `(ticker, report_date)`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..utils import safe_run
from . import analysts, debate, memory, reflection, research_manager, risk_reviewer
from .schemas import AgentResearchAssessment


def _assess_one(entry: dict[str, Any], report_date: str, as_of: str, reflections: list[str], news_provider: Any = None) -> AgentResearchAssessment:
    start = time.monotonic()
    opinions = analysts.run_all_analysts(entry, news_provider=news_provider)
    debate_result = debate.run_debate(opinions)
    action, confidence, thesis = research_manager.synthesize_recommendation(debate_result, opinions)
    action, confidence, risk_notes = risk_reviewer.review(entry, action, confidence, thesis)

    if reflections:
        thesis = thesis + " | Past reflection(s) on this ticker: " + " ; ".join(reflections)

    quant_assessment = entry.get("quant_assessment")
    quant_decision = getattr(quant_assessment, "decision", None)

    provenance = [f"{name}: {'available' if op.data_available else 'Data Unavailable'}" for name, op in opinions.items()]

    return AgentResearchAssessment(
        ticker=entry.get("symbol", ""), report_date=report_date, as_of=as_of,
        action=action, confidence=confidence, thesis=thesis,
        bull_points=debate_result.bull_points, bear_points=debate_result.bear_points,
        risk_notes=risk_notes, analyst_opinions=opinions, data_provenance=provenance,
        quant_agent_decision=quant_decision, duration_ms=round((time.monotonic() - start) * 1000.0, 2),
    )


def run_shadow_research(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    config: dict[str, Any],
    logger: logging.Logger,
    as_of: datetime | None = None,
    news_provider: Any = None,
) -> None:
    """Attaches `entry["agent_assessment"]` to every entry and records it
    to the research-memory database. Every step is `safe_run`-isolated so
    a failure here can never affect (or even be noticed by) the execution
    layer that already ran before this is called - see module docstring
    invariant 1. Disabled entirely when `config.intelligence.enabled` is
    explicitly `false` (default: enabled, shadow mode only)."""
    if not config.get("intelligence", {}).get("enabled", True):
        return

    as_of_str = (as_of or datetime.now(timezone.utc)).isoformat()
    db_path = memory.resolve_db_path(config)

    for entry in ticker_results:
        ticker = entry.get("symbol")
        if not ticker:
            continue

        past_reflections = safe_run(logger, f"{ticker} fetch reflections", lambda t=ticker: memory.fetch_recent_reflections(db_path, t)) or []
        assessment = safe_run(
            logger, f"{ticker} agent research assessment",
            lambda e=entry, r=past_reflections: _assess_one(e, report_date, as_of_str, r, news_provider=news_provider),
        )
        if assessment is None:
            continue

        entry["agent_assessment"] = assessment
        safe_run(logger, f"{ticker} record agent assessment", lambda a=assessment: memory.record_assessment(db_path, a))

    written = safe_run(logger, "agent reflection pass", lambda: reflection.generate_reflections(config, logger)) or []
    if written:
        logger.info("Multi-agent research layer: wrote %d new reflection(s) on past assessments.", len(written))
