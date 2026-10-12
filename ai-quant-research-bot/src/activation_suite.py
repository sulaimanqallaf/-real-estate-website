"""Real-world ACTIVATION suite (Sprint 5: "AI QUANT LABS - NEXT
PRIORITY: REAL-WORLD ACTIVATION" - items 2-7: real yfinance + the
cost-inclusive strategy validation report, real OpenAlex/arXiv/
Semantic Scholar responses with confirmation that a real paper enters
permanent research memory, real SEC ticker->CIK + FRED data, real
Alpaca Basic/IEX data, real PASS/FAIL/BLOCKED/SKIPPED statuses - never
claiming success from a mock, and a real Telegram delivery test).

**Reuse over reinvention, same discipline as every Sprint 4 agent.**
This module adds no new validation logic for strategy/IEX/broker
checks - it runs Sprint 4's own `validation_suite.run_validation_suite()`
UNCHANGED and merges its lines into one combined report, using the
exact same `SuiteLine`/`SuiteReport` shapes and
`STATUS_PASS`/`STATUS_FAIL`/`STATUS_BLOCKED`/`STATUS_SKIPPED`
vocabulary (imported from `validation_suite`, which itself imports
from `execution.connectivity_preflight` - still only ever one source
of truth for what these four words mean).

**What's genuinely new here**: three checks Sprint 4 never had -

1. **Academic providers** (OpenAlex/arXiv/Semantic Scholar) - calls
   each real `fetch_papers()` with a small smoke-test query and
   classifies the REAL `ProviderResult` it gets back
   (`_classify_provider_result()`): `STATUS_OK` with real papers ->
   PASS; `STATUS_BLOCKED` (a connection/timeout/5xx failure - see
   `data_providers.base`'s new Sprint 5 status) -> BLOCKED;
   `STATUS_ERROR` -> FAIL; `STATUS_UNAVAILABLE` (no results for this
   query) -> SKIPPED. If at least one provider returned a real paper,
   this check ALSO records one deduplicated sample into
   `research.paper_memory`'s permanent SQLite store and reads it back
   - the concrete, end-to-end proof item 3 of the sprint asked for
   ("confirm actual papers enter permanent research memory"), not
   just a provider-reachability ping. This is the one check in this
   suite with a real (tiny, intentional) side effect - every other
   check, including Sprint 4's reused three, is read-only.
2. **SEC ticker->CIK + FRED** - a real `fetch_company_tickers()` call,
   classified the same way, with an additional sanity check that the
   real AAPL CIK resolves out of the fetched map (a PASS that fetched
   tickers but can't resolve AAPL is itself a FAIL - something is
   wrong with the real response's shape); a real `fetch_series_latest()`
   call for FRED's Fed funds rate series, classified the same way.
   Both SKIPPED outright (never attempted) when `SEC_IDENTITY`/
   `FRED_API_KEY` aren't configured - see `.env.example`.
3. **Telegram delivery** - sends ONE real, clearly-labeled test
   message via `telegram_bot.send_telegram_message()` when
   `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are configured; PASS/FAIL on
   whether it actually sent, SKIPPED when not configured. Never
   constructs, logs, or echoes the token/chat id themselves - same
   `.env`-only credential path every other command in this project
   uses.

**Security, unchanged from Sprint 4's `validation_suite.py`.** Never
prompts for, logs, or echoes a credential value. Every line this
module prints or saves is passed through `utils.redact_secrets()`.
Never touches `autonomous_paper.*`/`execution.mode`, never calls
`submit_order`/`cancel_order`/`replace_order`, never starts/stops/
modifies the Mac launchd research scheduler. Nothing here increases
execution privilege or weakens a risk control - the one real write
this module performs (a single sample paper row) goes to
`research.paper_memory`'s own database, never anywhere execution-
adjacent.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import validation_suite
from .data_providers import base
from .utils import resolve_path
from .validation_suite import STATUS_BLOCKED, STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED, SuiteLine, SuiteReport

__all__ = [
    "STATUS_PASS", "STATUS_FAIL", "STATUS_BLOCKED", "STATUS_SKIPPED",
    "run_activation_suite", "format_activation_report_text", "save_activation_report_text",
]

ACADEMIC_SMOKE_QUERY = "stock market momentum anomaly"
FRED_SMOKE_SERIES = "FEDFUNDS"
SEC_SMOKE_TICKER = "AAPL"


def _classify_provider_result(result: base.ProviderResult[Any]) -> tuple[str, str]:
    """Maps the real `ProviderResult` status this check actually got
    back onto the suite's PASS/FAIL/BLOCKED/SKIPPED vocabulary - never
    a fabricated PASS. See module docstring for the mapping rationale."""
    if result.ok:
        count = len(result.data) if hasattr(result.data, "__len__") else 1
        return STATUS_PASS, f"{count} real result(s) returned"
    if result.status == base.STATUS_BLOCKED:
        return STATUS_BLOCKED, result.error or "connection/timeout failure - never reached a real answer"
    if result.status == base.STATUS_ERROR:
        return STATUS_FAIL, result.error or "reached the source, but it returned something wrong"
    return STATUS_SKIPPED, result.error or "not configured, or no results for this query"


def _run_academic_providers(config: dict[str, Any], logger: logging.Logger) -> list[SuiteLine]:
    from .data_providers import arxiv_provider, openalex_provider, semantic_scholar_provider
    from .research import paper_memory
    from .research.papers import dedupe_papers, score_evidence

    lines: list[SuiteLine] = []
    real_papers: list[Any] = []

    for provider_module, source_name in (
        (openalex_provider, "openalex"), (arxiv_provider, "arxiv"), (semantic_scholar_provider, "semantic_scholar"),
    ):
        try:
            result = provider_module.fetch_papers(ACADEMIC_SMOKE_QUERY, config, logger, limit=5)
        except Exception as exc:  # noqa: BLE001 - one provider's crash must never stop the other two or the rest of the suite
            lines.append(SuiteLine("academic_providers", source_name, STATUS_FAIL, f"raised: {exc}"))
            continue
        status, detail = _classify_provider_result(result)
        lines.append(SuiteLine("academic_providers", source_name, status, detail))
        if result.ok:
            real_papers.extend(result.data)

    if real_papers:
        sample = dedupe_papers(real_papers)[0]
        evidence = score_evidence(sample)
        db_path = paper_memory.default_db_path(config)
        key = paper_memory.record_paper(db_path, sample, evidence)
        stored = paper_memory.get_paper(db_path, key)
        if stored is not None and stored["title"] == sample.title:
            lines.append(
                SuiteLine("academic_providers", "paper_memory", STATUS_PASS, f"real sample paper {key!r} recorded and read back from {db_path}")
            )
        else:
            lines.append(
                SuiteLine("academic_providers", "paper_memory", STATUS_FAIL, f"sample paper was written but could not be read back from {db_path}")
            )
    else:
        lines.append(SuiteLine("academic_providers", "paper_memory", STATUS_SKIPPED, "no real paper was fetched this run - nothing to record"))

    return lines


def _run_sec_and_fred(config: dict[str, Any], logger: logging.Logger) -> list[SuiteLine]:
    from .data_providers import macro_provider, sec_provider

    lines: list[SuiteLine] = []

    if not sec_provider.sec_identity_configured():
        lines.append(SuiteLine("sec_and_fred", "sec_ticker_cik", STATUS_SKIPPED, "SEC_IDENTITY not configured in .env - see .env.example"))
    else:
        try:
            tickers_result = sec_provider.fetch_company_tickers(config, logger)
        except Exception as exc:  # noqa: BLE001
            lines.append(SuiteLine("sec_and_fred", "sec_ticker_cik", STATUS_FAIL, f"raised: {exc}"))
        else:
            status, detail = _classify_provider_result(tickers_result)
            if status == STATUS_PASS:
                cik = tickers_result.data.get(SEC_SMOKE_TICKER)
                if cik:
                    detail = f"{len(tickers_result.data)} ticker(s) mapped; {SEC_SMOKE_TICKER} -> CIK {cik}"
                else:
                    status = STATUS_FAIL
                    detail = f"{len(tickers_result.data)} ticker(s) mapped, but {SEC_SMOKE_TICKER} was not found - unexpected for a real response"
            lines.append(SuiteLine("sec_and_fred", "sec_ticker_cik", status, detail))

    if not macro_provider.fred_configured():
        lines.append(SuiteLine("sec_and_fred", "fred", STATUS_SKIPPED, "FRED_API_KEY not configured in .env - see .env.example"))
    else:
        try:
            result = macro_provider.fetch_series_latest(FRED_SMOKE_SERIES)
        except Exception as exc:  # noqa: BLE001
            lines.append(SuiteLine("sec_and_fred", "fred", STATUS_FAIL, f"raised: {exc}"))
        else:
            status, detail = _classify_provider_result(result)
            lines.append(SuiteLine("sec_and_fred", "fred", status, detail))

    return lines


def _run_telegram_delivery(config: dict[str, Any], logger: logging.Logger) -> list[SuiteLine]:
    import os

    from . import telegram_bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return [SuiteLine("telegram", "test_message", STATUS_SKIPPED, "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured in .env")]

    sent = telegram_bot.send_telegram_message(
        token, chat_id,
        "AI Quant Research Bot - activation suite test ping. No action needed; this confirms Telegram delivery is working.",
        logger,
    )
    if sent:
        return [SuiteLine("telegram", "test_message", STATUS_PASS, "test message delivered - check your Telegram chat for it")]
    return [SuiteLine("telegram", "test_message", STATUS_FAIL, "send_telegram_message() returned False - see the log for detail")]


def run_activation_suite(config: dict[str, Any], logger: logging.Logger, run_reconnect_check: bool = False) -> SuiteReport:
    """Runs Sprint 4's own `validation_suite.run_validation_suite()`
    unmodified, then the three new checks above, merging all of it
    into one combined report. Every check is isolated via
    `utils.safe_run()` exactly like `validation_suite.py` - a crash in
    one never prevents the rest from running or being reported."""
    from .utils import safe_run

    report = SuiteReport(started_at=datetime.now())

    base_report = validation_suite.run_validation_suite(config, logger, run_reconnect_check=run_reconnect_check)
    report.lines.extend(base_report.lines)

    for label, fn in (
        ("academic providers", lambda: _run_academic_providers(config, logger)),
        ("SEC ticker->CIK + FRED", lambda: _run_sec_and_fred(config, logger)),
        ("Telegram delivery", lambda: _run_telegram_delivery(config, logger)),
    ):
        lines = safe_run(logger, label, fn)
        if lines is None:
            report.lines.append(SuiteLine(label, "-", STATUS_BLOCKED, f"{label} raised an unexpected error - see the log for details"))
        else:
            report.lines.extend(lines)

    report.finished_at = datetime.now()
    return report


def format_activation_report_text(report: SuiteReport) -> str:
    return validation_suite.format_suite_report_text(
        report, title="AI QUANT LABS - REAL-WORLD ACTIVATION SUITE (Sprint 5)"
    )


def save_activation_report_text(text: str, config: dict[str, Any], now: datetime | None = None) -> Path:
    reports_dir = resolve_path(config.get("data", {}).get("reports_dir", "data/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    out_path = reports_dir / f"activation_suite_{timestamp}.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main() -> int:
    """`python -m src.activation_suite [--full-reconnect-check]` - see
    module docstring. The one real side effect across every check in
    this suite is a single sample-paper row in `research.paper_memory`
    when at least one academic provider returns real data; everything
    else is read-only. Never places, cancels, or modifies a broker
    order; never touches autonomous trading settings."""
    import argparse

    from .utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Real-world activation suite (Sprint 5).")
    parser.add_argument(
        "--full-reconnect-check", action="store_true",
        help="Also run the interactive IBKR reconnect-handling step (requires manually stopping/restarting TWS/Gateway when prompted).",
    )
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="activation_suite.log")

    report = run_activation_suite(config, logger, run_reconnect_check=args.full_reconnect_check)
    text = format_activation_report_text(report)
    print(text)
    saved_path = save_activation_report_text(text, config)
    print(f"\nSaved to {saved_path}")
    return 1 if report.any_failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
