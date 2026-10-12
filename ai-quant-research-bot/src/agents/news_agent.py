"""News Agent (Sprint 4, Priority 3: "Create an independent News Agent
to review SEC filings, financial news and economic events, then issue
useful Telegram briefings and alerts without triggering unverified
trades.").

**Three legitimate, free, official sources - no new paid service, no
scraping:**

1. **SEC filings** - `data_providers.sec_provider.fetch_recent_filings()`
   for each watchlist ticker's 8-K (material corporate events) and
   Form 4 (insider transactions) filings.
2. **Financial news** - scoped HONESTLY to the same SEC 8-K filings
   above. An 8-K IS how material corporate news gets officially
   disclosed - this agent does not scrape or sign up for a news API
   (both ruled out: scraping risks the "legitimate sources" the sprint
   explicitly asked for, and a new signup is a new service this sprint
   was told not to purchase, even on a free tier). See README "News
   Agent" for this scoping decision stated plainly.
3. **Economic events** - `data_providers.macro_provider.fetch_series_latest()`
   for a configured set of FRED series (Fed funds rate, CPI,
   unemployment, Treasury yields) - the same free, official source
   this project has used since an earlier phase.

**Only genuinely NEW items are briefed.** A persistent "seen" record
(`data/journal/agents/news_agent_seen.json` - deliberately NOT
`agents.checkpoint`'s resumable-work file, which is cleared on every
successful run; this state must persist indefinitely across runs, a
different contract) tracks every SEC accession number and FRED
(series, observation_date) pair this agent has already reported, so
the daily Telegram briefing never repeats something it already told
you.

**Never triggers a trade.** This agent has no import of `src.execution`
anywhere, never writes to `decision_ledger.py`/`paper_trades.csv`, and
never calls anything that could construct or submit an order - it
only reads public filings/data and sends a Telegram message.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..data_providers import base
from ..utils import redact_secrets, resolve_path
from . import budget, checkpoint, heartbeat, registry

AGENT_NAME = "news_agent"

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "SPY"]
DEFAULT_FRED_SERIES = ["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"]
DEFAULT_DAILY_LIMIT_PER_SOURCE = 50


def _news_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("research", {}).get("news_agent", {})


def _seen_state_path(config: dict[str, Any]) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / "agents" / "news_agent_seen.json"


def _load_seen_state(config: dict[str, Any]) -> dict[str, list[str]]:
    path = _seen_state_path(config)
    if not path.exists():
        return {"sec_accessions": [], "fred_observations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"sec_accessions": list(data.get("sec_accessions", [])), "fred_observations": list(data.get("fred_observations", []))}
    except (OSError, ValueError):
        return {"sec_accessions": [], "fred_observations": []}


def _save_seen_state(config: dict[str, Any], state: dict[str, list[str]]) -> None:
    path = _seen_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sec_accessions": sorted(set(state["sec_accessions"])), "fred_observations": sorted(set(state["fred_observations"]))}, indent=2), encoding="utf-8")


def _fetch_filings_for_ticker(ticker: str, config: dict[str, Any], logger: logging.Logger, summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns new, not-yet-seen 8-K/Form 4 filings for `ticker` - `[]`
    (never raises) if the CIK can't be resolved, SEC isn't configured,
    or the daily budget for this source is exhausted.

    Sprint 5 ("failed external providers must be visible as failures,
    not silently appearing healthy"): calls `fetch_company_tickers()`
    directly (rather than the convenience wrapper
    `resolve_cik_for_ticker()`, which collapses every failure reason
    into a plain `None`) so a real `STATUS_ERROR`/`STATUS_BLOCKED` on
    the ticker->CIK lookup, or on the filings fetch itself, is recorded
    into `summary["provider_issues"]` - previously invisible beyond a
    `logger.warning` line. A ticker genuinely not found in SEC's own
    map (lookup itself `STATUS_OK`) is NOT an issue - that is an
    honest, expected non-answer."""
    from ..data_providers import sec_provider

    daily_limit = _news_config(config).get("daily_api_limit_per_source", DEFAULT_DAILY_LIMIT_PER_SOURCE)
    if not budget.check_and_increment(config, AGENT_NAME, "sec_filings", daily_limit=daily_limit):
        logger.info("News Agent: daily SEC filings budget exhausted, skipping %s.", ticker)
        return []

    try:
        cik = sec_provider.resolve_cik_for_ticker(ticker, config, logger)
        if cik is None:
            # resolve_cik_for_ticker() collapses every failure reason into
            # a plain None - call fetch_company_tickers() once more just
            # to classify WHY, so a real lookup failure is recorded as an
            # issue rather than silently treated the same as "ticker
            # genuinely not in SEC's map" (an honest non-answer).
            tickers_result = sec_provider.fetch_company_tickers(config, logger)
            if tickers_result.status in (base.STATUS_ERROR, base.STATUS_BLOCKED):
                summary["provider_issues"].append(
                    {"source": "sec_filings", "ticker": ticker, "status": tickers_result.status, "detail": redact_secrets(tickers_result.error or "")}
                )
            else:
                logger.info("News Agent: could not resolve a CIK for %s.", ticker)
            return []

        result = sec_provider.fetch_recent_filings(cik, form_types=("8-K", "4"))
    except Exception as exc:  # noqa: BLE001 - one ticker's failure must never stop the rest of the watchlist
        logger.warning("News Agent: SEC fetch for %s raised: %s", ticker, exc)
        summary["provider_issues"].append({"source": "sec_filings", "ticker": ticker, "status": "exception", "detail": redact_secrets(str(exc))})
        return []

    if result.status in (base.STATUS_ERROR, base.STATUS_BLOCKED):
        summary["provider_issues"].append({"source": "sec_filings", "ticker": ticker, "status": result.status, "detail": redact_secrets(result.error or "")})

    if not result.ok:
        return []
    return [{**f, "ticker": ticker} for f in result.data]


def _fetch_economic_events(series_ids: list[str], config: dict[str, Any], logger: logging.Logger, summary: dict[str, Any]) -> list[dict[str, Any]]:
    from ..data_providers import macro_provider

    daily_limit = _news_config(config).get("daily_api_limit_per_source", DEFAULT_DAILY_LIMIT_PER_SOURCE)
    events: list[dict[str, Any]] = []
    for series_id in series_ids:
        if not budget.check_and_increment(config, AGENT_NAME, "fred", daily_limit=daily_limit):
            logger.info("News Agent: daily FRED budget exhausted, skipping %s.", series_id)
            continue
        try:
            result = macro_provider.fetch_series_latest(series_id)
        except Exception as exc:  # noqa: BLE001 - one series' failure must never stop the rest
            logger.warning("News Agent: FRED fetch for %s raised: %s", series_id, exc)
            summary["provider_issues"].append({"source": "fred", "series_id": series_id, "status": "exception", "detail": redact_secrets(str(exc))})
            continue
        if result.status in (base.STATUS_ERROR, base.STATUS_BLOCKED):
            summary["provider_issues"].append({"source": "fred", "series_id": series_id, "status": result.status, "detail": redact_secrets(result.error or "")})
        if result.ok:
            events.extend({"series_id": series_id, "value": o.value, "observation_date": str(o.observation_date), "available_at": str(o.available_at)} for o in result.data)
    return events


def run_once(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """One full agent cycle. Never raises - caught and recorded as a
    FAILED run in the registry, re-reported in the returned summary."""
    heartbeat.record_heartbeat(config, AGENT_NAME, now=now)
    registry.start_run(config, AGENT_NAME, now=now)

    news_cfg = _news_config(config)
    watchlist: list[str] = news_cfg.get("watchlist") or DEFAULT_WATCHLIST
    fred_series: list[str] = news_cfg.get("fred_series") or DEFAULT_FRED_SERIES

    summary: dict[str, Any] = {
        "tickers_checked": [], "new_filings": [], "new_economic_events": [], "errors": [], "provider_issues": [],
    }

    try:
        seen = _load_seen_state(config)
        resume_state = checkpoint.load_checkpoint(config, AGENT_NAME) or {}
        already_checked = set(resume_state.get("tickers_checked", []))

        for ticker in watchlist:
            if ticker in already_checked:
                logger.info("News Agent: %s already checked this cycle (resuming), skipping.", ticker)
                continue

            filings = _fetch_filings_for_ticker(ticker, config, logger, summary)
            for filing in filings:
                accession = filing.get("accession_number")
                if accession and accession not in seen["sec_accessions"]:
                    summary["new_filings"].append(filing)
                    seen["sec_accessions"].append(accession)

            already_checked.add(ticker)
            summary["tickers_checked"].append(ticker)
            checkpoint.save_checkpoint(config, AGENT_NAME, {"tickers_checked": sorted(already_checked)})

        events = _fetch_economic_events(fred_series, config, logger, summary)
        for event in events:
            key = f"{event['series_id']}:{event['observation_date']}"
            if key not in seen["fred_observations"]:
                summary["new_economic_events"].append(event)
                seen["fred_observations"].append(key)

        _save_seen_state(config, seen)
        checkpoint.clear_checkpoint(config, AGENT_NAME)

    except Exception as exc:  # noqa: BLE001 - this agent's top-level isolation boundary
        logger.error("News Agent: run_once failed: %s", exc, exc_info=True)
        summary["errors"].append(str(exc))
        registry.finish_run(config, AGENT_NAME, registry.STATUS_FAILED, error=str(exc), extra=summary, now=now)
        return summary

    registry.finish_run(
        config, AGENT_NAME, registry.STATUS_SUCCESS,
        summary=f"{len(summary['new_filings'])} new filing(s), {len(summary['new_economic_events'])} new economic event(s)",
        extra=summary, now=now,
    )
    return summary


def format_briefing_text(summary: dict[str, Any]) -> str:
    lines = ["News Agent - briefing (informational only, never a trade signal)"]

    if summary["new_filings"]:
        lines.append("")
        lines.append(f"New SEC filings ({len(summary['new_filings'])}):")
        for f in summary["new_filings"]:
            lines.append(f"  [{f.get('ticker')}] {f.get('form')} filed {f.get('filing_date')} (accession {f.get('accession_number')})")
    else:
        lines.append("New SEC filings: none")

    if summary["new_economic_events"]:
        lines.append("")
        lines.append(f"New economic data ({len(summary['new_economic_events'])}):")
        for e in summary["new_economic_events"]:
            lines.append(f"  {e['series_id']}: {e['value']} (observed {e['observation_date']}, released {e['available_at']})")
    else:
        lines.append("New economic data: none")

    if summary["errors"]:
        lines.append("")
        lines.append(f"Errors this cycle: {len(summary['errors'])} (see logs)")

    if summary["provider_issues"]:
        lines.append("")
        sources = ", ".join(sorted({issue["source"] for issue in summary["provider_issues"]}))
        lines.append(f"Provider issues this cycle: {len(summary['provider_issues'])} ({sources}) - see logs for detail")

    lines.append("")
    lines.append("This agent never places, suggests, or triggers a trade - informational only.")
    return "\n".join(lines)


def _send_telegram_briefing(config: dict[str, Any], logger: logging.Logger, summary: dict[str, Any]) -> None:
    import os

    from .. import telegram_bot

    if not summary["new_filings"] and not summary["new_economic_events"] and not summary["provider_issues"]:
        logger.info("News Agent: nothing new this cycle, skipping Telegram briefing.")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.info("News Agent: Telegram not configured, skipping briefing notification.")
        return

    telegram_bot.send_report(token, chat_id, format_briefing_text(summary), 3500, logger)


def main() -> int:
    """`python -m src.agents.news_agent` - runs one full cycle. Has its
    own singleton lock, independent of every other process in this
    codebase and of the Research Scholar Agent."""
    from ..execution.process_lock import ProcessAlreadyRunningError, acquire_singleton_lock
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="news_agent.log")

    lock_path = resolve_path(config.get("data", {}).get("journal_dir", "data/journal")) / "agents" / "news_agent.lock"
    try:
        _lock_handle = acquire_singleton_lock(lock_path, "news_agent")
    except ProcessAlreadyRunningError as exc:
        print(f"News Agent already running: {exc}")
        return 1

    summary = run_once(config, logger)
    print(f"News Agent: {summary}")
    _send_telegram_briefing(config, logger, summary)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
