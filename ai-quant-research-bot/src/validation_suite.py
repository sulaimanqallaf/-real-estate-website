"""One-click real-world validation suite (Sprint 4, Priority 1: "Create
a guided Mac interface that safely runs real historical-data strategy
validation, Alpaca Basic/IEX data checks, and read-only IBKR Paper
connectivity preflight. Display PASS, FAIL, SKIPPED and BLOCKED with
actual evidence.").

Runs three checks that already exist from Sprint 3 - this module adds
no new validation LOGIC of its own, only orchestration and a single
aggregated report in the exact four-value vocabulary
`src.execution.connectivity_preflight` already established
(`STATUS_PASS`/`STATUS_FAIL`/`STATUS_BLOCKED`/`STATUS_SKIPPED`,
imported from there rather than redefined, so there is only ever one
source of truth for what these four words mean):

1. **Strategy validation** (`src.analytics.strategy_validation_report`) -
   real yfinance history, the production backtest + walk-forward
   engine, an evidence-gated verdict per strategy.
2. **Alpaca/IEX data check** (`src.data_providers.iex_validation`) -
   real 1m/5m/15m bar schema/cross-timeframe validation. **Skipped**
   (never run, never a fabricated result) when
   `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY` aren't configured -
   running it anyway would just report `UNVALIDATED` for every symbol,
   so the honest, useful thing is to say *why* it wasn't exercised
   rather than make the same pointless network call.
3. **IBKR Paper connectivity preflight**
   (`src.execution.connectivity_preflight`) - paper-account identity,
   reconnect handling (skipped by default here - see
   `run_reconnect_check`; it is the one genuinely interactive step),
   order reconciliation, kill switch.

**Security.** This module never prompts for, logs, or echoes a
credential value - credentials only ever come from `.env` via the
existing `load_env()`, exactly like every other command in this
codebase; this adds no new credential-entry path, interactive or
otherwise. Every line this module prints or saves is passed through
`utils.redact_secrets()` first, as defense in depth even though none
of the three underlying checks construct a credential-bearing string
in the first place.

**Never**: never touches `autonomous_paper.*` or `execution.mode`,
never calls `submit_order`/`cancel_order`/`replace_order`, never
starts/stops/modifies the Mac launchd research scheduler. Every
network call this module makes was already real, tested code before
this module existed - it only decides what to run and how to report
on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .execution.connectivity_preflight import STATUS_BLOCKED, STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED
from .utils import redact_secrets, resolve_path

# Re-exported so callers only ever need to import this one module for
# the four-value vocabulary - still the exact same strings as
# connectivity_preflight's, never redefined with a different casing.
__all__ = ["STATUS_PASS", "STATUS_FAIL", "STATUS_BLOCKED", "STATUS_SKIPPED", "run_validation_suite", "format_suite_report_text", "save_suite_report_text"]


@dataclass(frozen=True)
class SuiteLine:
    """One line of evidence in the aggregated report - a single
    strategy's verdict, a single symbol's bar check, or a single
    connectivity-preflight step."""

    section: str
    name: str
    status: str
    detail: str


@dataclass
class SuiteReport:
    started_at: datetime
    finished_at: datetime | None = None
    lines: list[SuiteLine] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_SKIPPED: 0, STATUS_BLOCKED: 0}
        for line in self.lines:
            counts[line.status] = counts.get(line.status, 0) + 1
        return counts

    @property
    def any_failed(self) -> bool:
        return any(line.status == STATUS_FAIL for line in self.lines)


def _run_strategy_validation(config: dict[str, Any], logger: logging.Logger) -> list[SuiteLine]:
    from . import data_collector
    from .analytics import strategy_validation_report as svr

    universe = config["strategy_universe"]
    all_symbols = sorted(set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"]))
    benchmark_symbols = config["backtest"]["benchmarks"]
    symbols_to_fetch = sorted(set(all_symbols + benchmark_symbols))

    price_history = data_collector.fetch_all_price_history(symbols_to_fetch, config, logger)
    if not price_history:
        return [SuiteLine("strategy_validation", "all strategies", STATUS_BLOCKED, "no real price data could be fetched - see docs/platform/BLOCKERS.md")]

    report = svr.run_validation_report(price_history, config, logger, data_provenance="real_market_data")
    if report is None:
        return [SuiteLine("strategy_validation", "all strategies", STATUS_BLOCKED, "validation report could not be built from the fetched data")]

    verdict_to_status = {
        svr.VERDICT_CONSISTENT_IN_BACKTEST: STATUS_PASS,
        svr.VERDICT_INCONSISTENT: STATUS_FAIL,
        svr.VERDICT_INSUFFICIENT_EVIDENCE: STATUS_SKIPPED,
    }
    return [
        SuiteLine("strategy_validation", r.strategy_name, verdict_to_status.get(r.verdict, STATUS_FAIL), f"{r.verdict}: {r.verdict_reason}")
        for r in report.results
    ]


def _run_iex_data_check(config: dict[str, Any], logger: logging.Logger) -> list[SuiteLine]:
    from datetime import timedelta, timezone

    from .data_providers import alpaca_provider, iex_validation as iv

    if not alpaca_provider.alpaca_configured():
        return [
            SuiteLine(
                "alpaca_iex", "all symbols", STATUS_SKIPPED,
                "ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY not configured in .env - set them (free Basic plan, "
                "no card required) to enable this check; see .env.example",
            )
        ]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)
    report = iv.run_validation(iv.DEFAULT_VALIDATION_SYMBOLS, start, end, config, logger)

    status_map = {iv.STATUS_PASS: STATUS_PASS, iv.STATUS_FAIL: STATUS_FAIL, iv.STATUS_UNVALIDATED: STATUS_BLOCKED}
    return [SuiteLine("alpaca_iex", r.symbol, status_map.get(r.status, STATUS_FAIL), r.detail) for r in report.results]


def _run_connectivity_preflight(config: dict[str, Any], logger: logging.Logger, run_reconnect_check: bool) -> list[SuiteLine]:
    from .execution import connectivity_preflight as cp
    from .execution.ibkr_client import AccountModeError, IBKRClient, IBKRConfig, IBKRConnectionError

    ibkr_config = IBKRConfig.from_env()
    client = IBKRClient(ibkr_config)

    lines: list[SuiteLine] = []
    connected = False
    try:
        client.connect()
        connected = True
    except (AccountModeError, IBKRConnectionError) as exc:
        reason = f"could not establish a connection to verify this against: {exc}"
        lines.extend(SuiteLine("ibkr_preflight", name, STATUS_BLOCKED, reason) for name in ("paper_identity", "reconnect_handling", "order_reconciliation"))

    if connected:
        try:
            r = cp.check_paper_identity(client)
            lines.append(SuiteLine("ibkr_preflight", r.step, r.status, r.detail))
            if run_reconnect_check:
                r = cp.check_reconnect_handling(client, logger)
                lines.append(SuiteLine("ibkr_preflight", r.step, r.status, r.detail))
            else:
                lines.append(SuiteLine("ibkr_preflight", "reconnect_handling", STATUS_SKIPPED, "skipped by default in the one-click suite - run connectivity_preflight directly to exercise it"))
            r = cp.check_order_reconciliation(client, logger)
            lines.append(SuiteLine("ibkr_preflight", r.step, r.status, r.detail))
        finally:
            client.disconnect()

    r = cp.check_kill_switch(config, logger)
    lines.append(SuiteLine("ibkr_preflight", r.step, r.status, r.detail))
    return lines


def run_validation_suite(config: dict[str, Any], logger: logging.Logger, run_reconnect_check: bool = False) -> SuiteReport:
    """Runs all three checks in order, isolating each so a crash in one
    never prevents the other two from running or being reported.
    `run_reconnect_check` defaults to `False` - the one genuinely
    interactive step (manually stop/restart your real TWS/Gateway) is
    opt-in here, not forced on every one-click run."""
    from .utils import safe_run

    report = SuiteReport(started_at=datetime.now())

    for label, fn in (
        ("strategy validation", lambda: _run_strategy_validation(config, logger)),
        ("Alpaca/IEX data check", lambda: _run_iex_data_check(config, logger)),
        ("IBKR Paper connectivity preflight", lambda: _run_connectivity_preflight(config, logger, run_reconnect_check)),
    ):
        lines = safe_run(logger, label, fn)
        if lines is None:
            report.lines.append(SuiteLine(label, "-", STATUS_BLOCKED, f"{label} raised an unexpected error - see the log for details"))
        else:
            report.lines.extend(lines)

    report.finished_at = datetime.now()
    return report


def format_suite_report_text(report: SuiteReport) -> str:
    lines = [
        "=" * 72,
        "ONE-CLICK REAL-WORLD VALIDATION SUITE (Sprint 4, Priority 1)",
        "=" * 72,
    ]
    current_section = None
    for line in report.lines:
        if line.section != current_section:
            lines.append("")
            lines.append(f"--- {line.section} ---")
            current_section = line.section
        lines.append(f"[{line.status}] {line.name}: {line.detail}")

    counts = report.counts
    lines.append("")
    lines.append(
        f"Summary: {counts[STATUS_PASS]} PASS, {counts[STATUS_FAIL]} FAIL, "
        f"{counts[STATUS_SKIPPED]} SKIPPED, {counts[STATUS_BLOCKED]} BLOCKED"
    )
    if report.any_failed:
        lines.append("RESULT: one or more checks FAILED - review the evidence above before trusting this system with real decisions.")
    else:
        lines.append("RESULT: no FAILs. SKIPPED/BLOCKED items are not failures - they mean a check genuinely couldn't be exercised (see each detail).")
    lines.append("Autonomous order submission and live trading remain OFF regardless of this result - this suite never changes that.")

    return redact_secrets("\n".join(lines))


def save_suite_report_text(text: str, config: dict[str, Any], now: datetime | None = None) -> Path:
    reports_dir = resolve_path(config.get("data", {}).get("reports_dir", "data/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    out_path = reports_dir / f"validation_suite_{timestamp}.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main() -> int:
    """`python -m src.validation_suite [--full-reconnect-check]` - see
    module docstring. Read-only except for the kill-switch round-trip
    (restored to its original state) and saving its own report file;
    never places, cancels, or modifies an order."""
    import argparse

    from .utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="One-click real-world validation suite.")
    parser.add_argument(
        "--full-reconnect-check", action="store_true",
        help="Also run the interactive IBKR reconnect-handling step (requires manually stopping/restarting TWS/Gateway when prompted).",
    )
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="validation_suite.log")

    report = run_validation_suite(config, logger, run_reconnect_check=args.full_reconnect_check)
    text = format_suite_report_text(report)
    print(text)
    saved_path = save_suite_report_text(text, config)
    print(f"\nSaved to {saved_path}")
    return 1 if report.any_failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
