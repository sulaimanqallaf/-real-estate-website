"""Research Scholar Agent (Sprint 4, Priority 2: "Create an
independent daily Research Scholar Agent using legitimate OpenAlex,
arXiv, Semantic Scholar and official financial research sources.
Deduplicate papers, evaluate evidence, generate falsifiable strategy
hypotheses and store permanent research memory with citations.").

One run: for each configured research query, fetches real papers from
OpenAlex + arXiv (scoped to its `q-fin` category) + Semantic Scholar
(each gated by `agents.budget`'s daily call cap - these are free APIs
with real rate limits, not unlimited), merges duplicates across
sources (`research.papers.dedupe_papers`), scores each for evidence
strength, and records EVERY paper seen into permanent memory
(`research.paper_memory`) regardless of score - "store permanent
research memory" means every paper read, not just the interesting
ones.

Only papers clearing `min_evidence_score_for_hypothesis` generate a
falsifiable hypothesis (`research.hypothesis_mapper`), which is then
actually run through the real backtester
(`research.sandbox.run_hypothesis`) against real yfinance history and
recorded into `research.hypothesis_ledger` - never fabricated,
never skipped silently.

**Safety, unchanged from Sprint 2's sandbox invariants**: this agent
never imports `src.execution`, never promotes a hypothesis into a
strategy change (there is no such function anywhere in this codebase),
and never places or even constructs a broker order. It is read-only
research, start to finish.

**Independent of the existing Mac research scheduler.** This agent
has its own process lock
(`data/journal/agents/research_scholar.lock`), its own heartbeat, and
its own launchd label if you choose to schedule it
(`com.aiquantresearchbot.researchscholar`, NOT any of the 7 existing
`com.aiquantresearchbot.*` jobs) - see README "Research Scholar
Agent."
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..data_providers import base
from ..utils import redact_secrets
from . import budget, checkpoint, heartbeat, registry

AGENT_NAME = "research_scholar"

DEFAULT_QUERIES = [
    "momentum anomaly equity returns",
    "mean reversion stock returns",
    "trend following futures returns",
]
DEFAULT_SYMBOLS = ["SPY", "QQQ"]
DEFAULT_MIN_EVIDENCE_SCORE = 0.5
DEFAULT_DAILY_LIMIT_PER_SOURCE = 20


def _research_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("research", {}).get("scholar", {})


def _fetch_one_source(
    provider_module: Any, source_name: str, query: str, config: dict[str, Any], logger: logging.Logger, summary: dict[str, Any]
) -> list[Any]:
    """Isolated so one provider's failure (or an exhausted daily
    budget) never prevents the other two from contributing for this
    query. Returns `[]` (never raises) on any failure - a provider's
    own `fetch_papers` already never raises, but this is the
    agent-level isolation boundary regardless.

    Sprint 5 ("failed external providers must be visible as failures,
    not silently appearing healthy"): a REAL failure (an exception, or
    `STATUS_ERROR`/`STATUS_BLOCKED`) is recorded into
    `summary["provider_issues"]` - previously this only reached a
    `logger.warning` line, invisible in the dict `main()` prints, sent
    to Telegram, or recorded in the registry. `STATUS_UNAVAILABLE`
    (not configured, or genuinely no results for this query) and a
    budget exhaustion are NOT issues - both are honest, expected
    non-answers, not something gone wrong."""
    research_cfg = _research_config(config)
    daily_limit = research_cfg.get("daily_api_limit_per_source", DEFAULT_DAILY_LIMIT_PER_SOURCE)

    if not budget.check_and_increment(config, AGENT_NAME, source_name, daily_limit=daily_limit):
        logger.info("Research Scholar: daily budget for %s exhausted, skipping query %r.", source_name, query)
        return []

    try:
        result = provider_module.fetch_papers(query, config, logger)
    except Exception as exc:  # noqa: BLE001 - a provider failing must never stop the other two
        logger.warning("Research Scholar: %s fetch for %r raised: %s", source_name, query, exc)
        summary["provider_issues"].append({"source": source_name, "query": query, "status": "exception", "detail": redact_secrets(str(exc))})
        return []

    if result.ok:
        return list(result.data)

    if result.status in (base.STATUS_ERROR, base.STATUS_BLOCKED):
        summary["provider_issues"].append({"source": source_name, "query": query, "status": result.status, "detail": redact_secrets(result.error or "")})

    return []


def run_once(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """One full agent cycle. Never raises - any unexpected failure is
    caught, recorded as a FAILED run in the registry, and re-reported
    in the returned summary dict rather than propagating."""
    from ..research import hypothesis_mapper, paper_memory
    from ..research.hypothesis_ledger import default_db_path as hypothesis_db_path, record_result
    from ..research.paper_memory import default_db_path as paper_db_path
    from ..research.papers import dedupe_papers, score_evidence
    from ..research.sandbox import run_hypothesis

    heartbeat.record_heartbeat(config, AGENT_NAME, now=now)
    registry.start_run(config, AGENT_NAME, now=now)

    research_cfg = _research_config(config)
    queries: list[str] = research_cfg.get("queries") or DEFAULT_QUERIES
    symbols: list[str] = research_cfg.get("symbols") or DEFAULT_SYMBOLS
    min_score: float = research_cfg.get("min_evidence_score_for_hypothesis", DEFAULT_MIN_EVIDENCE_SCORE)

    summary = {
        "queries_run": [], "papers_seen": 0, "papers_deduped": 0, "hypotheses_generated": 0, "hypotheses_tested": 0,
        "errors": [], "provider_issues": [],
    }

    try:
        from ..data_providers import arxiv_provider, openalex_provider, semantic_scholar_provider

        providers = (
            (openalex_provider, "openalex"),
            (arxiv_provider, "arxiv"),
            (semantic_scholar_provider, "semantic_scholar"),
        )

        resume_state = checkpoint.load_checkpoint(config, AGENT_NAME) or {}
        completed_queries = set(resume_state.get("completed_queries", []))

        price_history = None  # fetched lazily, only once a hypothesis actually needs it

        for query in queries:
            if query in completed_queries:
                logger.info("Research Scholar: query %r already completed this cycle (resuming), skipping.", query)
                continue

            raw_papers = []
            for provider_module, source_name in providers:
                raw_papers.extend(_fetch_one_source(provider_module, source_name, query, config, logger, summary))

            summary["papers_seen"] += len(raw_papers)
            deduped = dedupe_papers(raw_papers)
            summary["papers_deduped"] += len(deduped)

            for paper in deduped:
                evidence = score_evidence(paper, now=now)
                hypothesis_ids: list[str] = []

                if evidence.overall_score >= min_score:
                    hypotheses = hypothesis_mapper.generate_hypotheses(paper, symbols)
                    if hypotheses:
                        if price_history is None:
                            from .. import data_collector

                            price_history = data_collector.fetch_all_price_history(symbols, config, logger)

                        for hypothesis in hypotheses:
                            summary["hypotheses_generated"] += 1
                            try:
                                result = run_hypothesis(hypothesis, price_history or {}, config, logger)
                            except Exception as exc:  # noqa: BLE001 - one bad hypothesis must never stop the rest
                                summary["errors"].append(f"hypothesis {hypothesis.hypothesis_id} raised: {exc}")
                                continue
                            if result is not None:
                                record_result(hypothesis_db_path(config), result)
                                hypothesis_ids.append(hypothesis.hypothesis_id)
                                summary["hypotheses_tested"] += 1

                paper_memory.record_paper(paper_db_path(config), paper, evidence, hypothesis_ids=hypothesis_ids, now=now)

            completed_queries.add(query)
            summary["queries_run"].append(query)
            checkpoint.save_checkpoint(config, AGENT_NAME, {"completed_queries": sorted(completed_queries)})

        checkpoint.clear_checkpoint(config, AGENT_NAME)

    except Exception as exc:  # noqa: BLE001 - this agent's top-level isolation boundary
        logger.error("Research Scholar: run_once failed: %s", exc, exc_info=True)
        summary["errors"].append(str(exc))
        registry.finish_run(config, AGENT_NAME, registry.STATUS_FAILED, error=str(exc), extra=summary, now=now)
        return summary

    registry.finish_run(
        config, AGENT_NAME, registry.STATUS_SUCCESS,
        summary=f"{summary['papers_deduped']} paper(s) seen, {summary['hypotheses_tested']} hypothesis(es) tested",
        extra=summary, now=now,
    )
    return summary


def _send_telegram_summary(config: dict[str, Any], logger: logging.Logger, summary: dict[str, Any]) -> None:
    import os

    from .. import telegram_bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.info("Research Scholar: Telegram not configured, skipping summary notification.")
        return

    lines = [
        "Research Scholar Agent - daily summary",
        f"Queries run: {len(summary['queries_run'])}",
        f"Papers seen: {summary['papers_seen']} (deduped: {summary['papers_deduped']})",
        f"Hypotheses generated: {summary['hypotheses_generated']}, tested: {summary['hypotheses_tested']}",
    ]
    if summary["errors"]:
        lines.append(f"Errors: {len(summary['errors'])} (see logs)")
    if summary["provider_issues"]:
        sources = ", ".join(sorted({issue["source"] for issue in summary["provider_issues"]}))
        lines.append(f"Provider issues: {len(summary['provider_issues'])} ({sources}) - see logs for detail")
    telegram_bot.send_report(token, chat_id, "\n".join(lines), 3500, logger)


def main() -> int:
    """`python -m src.agents.research_scholar` - runs one full cycle.
    Has its own singleton lock, independent of every other process in
    this codebase."""
    from ..execution.process_lock import ProcessAlreadyRunningError, acquire_singleton_lock
    from ..utils import load_config, load_env, resolve_path, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="research_scholar.log")

    lock_path = resolve_path(config.get("data", {}).get("journal_dir", "data/journal")) / "agents" / "research_scholar.lock"
    try:
        _lock_handle = acquire_singleton_lock(lock_path, "research_scholar")
    except ProcessAlreadyRunningError as exc:
        print(f"Research Scholar Agent already running: {exc}")
        return 1

    summary = run_once(config, logger)
    print(f"Research Scholar Agent: {summary}")
    _send_telegram_summary(config, logger, summary)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
