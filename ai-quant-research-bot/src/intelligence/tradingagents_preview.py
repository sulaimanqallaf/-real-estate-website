"""Safe, local, zero-cost preview of the TradingAgents section of the
daily report/Telegram message (GitHub Issue #1 follow-up: "validate the
enhanced TradingAgents Telegram report using existing cached results
only").

**Reads ONLY `tradingagents_adapter.py`'s own cache**
(`list_cached_raw_results()`) - makes zero new LLM API calls (no
subprocess, no network), places zero IBKR orders (never imports
anything execution-facing), and never sends a Telegram message (never
imports `telegram_bot`). Renders through the EXACT SAME
`report_writer.format_tradingagents_context_line()` the real daily
report/Telegram message uses, so what this prints is what would
actually appear there - not a reimplementation that could drift from
it.

Shadow mode and DRY_RUN execution are completely unaffected: this
module has no code path into `execution_policy.py`, `circuit_breaker.py`,
`portfolio_risk.py`, or any order-submission code, by construction - see
`tests/test_intelligence_tradingagents_safety.py`'s grep-based guardrail,
which also covers this file.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import report_writer
from . import memory, tradingagents_adapter


def _quant_decision_for(config: dict[str, Any], ticker: str, report_date: str) -> str | None:
    """Best-effort lookup of the deterministic engine's own quant_agent
    decision for the SAME ticker/report_date, from its separate memory
    database (`intelligence/memory.py`) - never fabricated, `None` when
    no matching assessment was ever recorded for that day."""
    try:
        db_path = memory.resolve_db_path(config)
        rows = memory.query_assessments(db_path, ticker=ticker, since=report_date, until=report_date)
    except Exception:  # noqa: BLE001 - the quant comparison is advisory; a lookup failure must never break the preview
        return None
    for row in reversed(rows):
        if row.get("quant_agent_decision"):
            return row["quant_agent_decision"]
    return None


def build_preview_entries(config: dict[str, Any], logger: logging.Logger) -> list[dict[str, Any]]:
    """Returns one `{"symbol": ..., "tradingagents_assessment": ...}` dict
    per cached TradingAgents result - the exact shape `report_writer.
    format_tradingagents_context_line()` expects, built entirely from the
    cache (`list_cached_raw_results()`, which already best-effort
    recovers any legacy `NULL` ticker/report_date - see
    `tradingagents_adapter.recover_cache_metadata_from_request_files()`).
    A row whose ticker/report_date could not be recovered is still
    included, clearly labeled `UNKNOWN`, rather than silently dropped."""
    entries = []
    for cached in tradingagents_adapter.list_cached_raw_results(config):
        ticker = cached["ticker"] or "UNKNOWN"
        report_date = cached["report_date"] or "UNKNOWN"
        as_of = cached["cached_at"] or ""
        quant_decision = _quant_decision_for(config, ticker, report_date) if ticker != "UNKNOWN" and report_date != "UNKNOWN" else None
        try:
            assessment = tradingagents_adapter.build_assessment(ticker, report_date, as_of, cached["raw"], quant_agent_decision=quant_decision)
        except Exception as exc:  # noqa: BLE001 - one malformed cache row must never break the whole preview
            logger.warning("TradingAgents preview: could not build assessment for %s/%s: %s", ticker, report_date, exc)
            continue
        entries.append({"symbol": ticker, "tradingagents_assessment": assessment})
    return entries


def format_preview(config: dict[str, Any], logger: logging.Logger) -> str:
    """The full preview text - same per-ticker block the real report
    uses, concatenated with a clear header stating this is a cache-only,
    zero-cost, zero-order, zero-Telegram preview."""
    entries = build_preview_entries(config, logger)
    header = "🧪 TradingAgents report preview (from cache only - NO new API calls, NO Telegram send, NO IBKR orders)"
    if not entries:
        return f"{header}\n\nNo cached TradingAgents results found - nothing to preview. Run with intelligence.tradingagents.enabled: true at least once first."

    blocks = [header, ""]
    for entry in entries:
        line = report_writer.format_tradingagents_context_line(entry).rstrip()
        blocks.append(f"{entry['symbol']}:\n{line}" if line else f"{entry['symbol']}: (no content)")
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def main() -> int:
    """`python -m src.intelligence.tradingagents_preview` - prints the
    preview to stdout. Never touches Telegram, never touches a broker;
    see module docstring."""
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="tradingagents_preview.log")
    print(format_preview(config, logger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
