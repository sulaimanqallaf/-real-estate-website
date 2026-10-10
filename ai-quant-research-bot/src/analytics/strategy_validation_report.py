"""Transparent, consolidated strategy validation report (Sprint 3, "Real
Strategy Validation" milestone, Task V3: "Produce a transparent
performance report. Do not claim profitability without sufficient
evidence.").

Consolidates everything this sprint's other validation work already
produced - Task V1's cost-inclusive single-window backtest
(`src/backtester.py`) and Task V2's walk-forward fold consistency
(`src/analytics/walk_forward.py`) - into ONE report per strategy, and
adds the one thing neither of those modules does on its own: an
explicit, rule-based **evidence verdict** that refuses to call a
strategy's backtest behavior "consistent" unless genuinely sufficient
evidence exists (`MIN_TRADES_FOR_EVIDENCE`/`MIN_FOLDS_FOR_EVIDENCE`/
`CONSISTENCY_FRACTION_PROFITABLE` below - every threshold named, not
buried). This module invents no new backtest logic of its own.

**The verdict is evidence about the backtest, never a forecast.** Even
the strongest verdict (`VERDICT_CONSISTENT_IN_BACKTEST`) is worded as
past, cost-inclusive backtest behavior over the available history -
never "will be profitable," "is a good strategy," or any other
forward-looking claim. See README "Disclaimer".

**`price_history` is a parameter, never fetched internally** - same
design as `backtester_crosscheck.run_crosscheck()`, for the same
reason: it lets this module be exercised end-to-end against a
deterministic, clearly-labeled synthetic fixture in tests without any
network mocking, and the required `data_provenance` argument (the
caller's honest label, this module has no way to verify which one it
was handed) is carried into the report so nothing downstream can
mistake one for the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .. import backtester
from . import walk_forward
from ..utils import resolve_path

# Every threshold this report's verdict logic runs on, named here so
# none of them hide inside a formula - Sprint 3: "do not claim
# profitability without sufficient evidence."
MIN_TRADES_FOR_EVIDENCE = 30        # a widely-used rule-of-thumb floor for any trade-level stat to mean much
MIN_FOLDS_FOR_EVIDENCE = 3          # fewer than 3 independent periods can't demonstrate "consistency" at all
CONSISTENCY_FRACTION_PROFITABLE = 0.5   # must be profitable in at least half the folds, not just the aggregate window

VERDICT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VERDICT_INCONSISTENT = "INCONSISTENT_ACROSS_FOLDS"
VERDICT_CONSISTENT_IN_BACKTEST = "CONSISTENT_IN_BACKTEST"


@dataclass
class StrategyValidationResult:
    strategy_name: str
    symbols: list[str]
    single_window_stats: dict[str, Any]
    walk_forward_report: walk_forward.WalkForwardReport
    benchmark_buy_and_hold_pct: dict[str, float | None]
    verdict: str
    verdict_reason: str


@dataclass
class ValidationReport:
    data_provenance: str  # "real_market_data" or "synthetic_fixture" - the caller's honest label
    backtest_window: dict[str, str]  # {"start": ..., "end": ...}
    results: list[StrategyValidationResult] = field(default_factory=list)


def _evidence_verdict(single_window_stats: dict[str, Any], wf_report: walk_forward.WalkForwardReport) -> tuple[str, str]:
    total_trades = single_window_stats.get("total_trades", 0)
    if total_trades < MIN_TRADES_FOR_EVIDENCE or wf_report.num_folds < MIN_FOLDS_FOR_EVIDENCE:
        return (
            VERDICT_INSUFFICIENT_EVIDENCE,
            f"only {total_trades} trade(s) in the single-window backtest and {wf_report.num_folds} walk-forward "
            f"fold(s) - below this report's {MIN_TRADES_FOR_EVIDENCE}-trade / {MIN_FOLDS_FOR_EVIDENCE}-fold floor "
            "for saying anything about consistency. Do not treat the single-window number alone as evidence.",
        )

    fraction_profitable = wf_report.fraction_profitable or 0.0
    if fraction_profitable < CONSISTENCY_FRACTION_PROFITABLE:
        return (
            VERDICT_INCONSISTENT,
            f"profitable in only {fraction_profitable:.0%} of {wf_report.num_folds} walk-forward folds (below the "
            f"{CONSISTENCY_FRACTION_PROFITABLE:.0%} floor) - the single-window backtest's "
            f"{single_window_stats.get('total_return_pct')}% return is not representative of this strategy's "
            "behavior across independent historical periods. Not sufficient evidence of profitability.",
        )

    return (
        VERDICT_CONSISTENT_IN_BACKTEST,
        f"profitable in {fraction_profitable:.0%} of {wf_report.num_folds} walk-forward folds and in the "
        f"single-window backtest ({single_window_stats.get('total_return_pct')}%) - consistent, cost-inclusive "
        "PAST backtest behavior across the available history. This is NOT a forecast or a guarantee of future "
        "performance.",
    )


def run_validation_report(
    price_history: dict[str, pd.DataFrame],
    config: dict[str, Any],
    logger: logging.Logger,
    data_provenance: str,
) -> ValidationReport | None:
    """Builds one `StrategyValidationResult` per strategy in
    `config["strategy_universe"]`, combining a single-window backtest
    with a walk-forward consistency check. Returns `None` (never a
    fabricated report) when `price_history` is empty."""
    if data_provenance not in ("real_market_data", "synthetic_fixture"):
        raise ValueError(f"data_provenance must be 'real_market_data' or 'synthetic_fixture', got {data_provenance!r}")
    if not price_history:
        logger.info("Strategy validation report unavailable: no price history provided.")
        return None

    universe = config["strategy_universe"]
    strategy_universes = {
        "Mean Reversion": universe["mean_reversion"],
        "Momentum Breakout": universe["momentum_breakout"],
        "Trend Following": universe["trend_following"],
    }
    benchmark_symbols = config["backtest"]["benchmarks"]
    initial_capital = config["backtest"]["initial_capital"]
    lookback_days = backtester.period_to_days(config["backtest"]["lookback_period"])
    latest_date = max(df.index.max() for df in price_history.values() if not df.empty)
    backtest_start = latest_date - timedelta(days=lookback_days)

    results: list[StrategyValidationResult] = []
    for strategy_name, symbols in strategy_universes.items():
        trades, equity_curve = backtester._run_strategy_backtest(strategy_name, symbols, price_history, backtest_start, config, logger)
        single_window_stats = backtester._compute_stats(trades, equity_curve, initial_capital)

        wf_report = walk_forward.run_walk_forward(strategy_name, symbols, price_history, config, logger, benchmark_symbols=benchmark_symbols)

        bench_returns: dict[str, float | None] = {}
        for bench in benchmark_symbols:
            bdf = price_history.get(bench)
            if bdf is not None:
                bench_returns[bench] = backtester._buy_and_hold_return_pct(bdf, backtest_start)

        verdict, reason = _evidence_verdict(single_window_stats, wf_report)
        results.append(
            StrategyValidationResult(
                strategy_name=strategy_name,
                symbols=symbols,
                single_window_stats=single_window_stats,
                walk_forward_report=wf_report,
                benchmark_buy_and_hold_pct=bench_returns,
                verdict=verdict,
                verdict_reason=reason,
            )
        )

    logger.info("Strategy validation report: %d strategy(ies) evaluated, provenance=%s.", len(results), data_provenance)
    return ValidationReport(
        data_provenance=data_provenance,
        backtest_window={"start": str(backtest_start.date()), "end": str(latest_date.date())},
        results=results,
    )


def format_validation_report_text(report: ValidationReport) -> str:
    lines = [
        "=" * 72,
        "STRATEGY VALIDATION REPORT (Sprint 3, Task V3)",
        f"Data provenance: {report.data_provenance}",
        f"Single-window backtest range: {report.backtest_window['start']} to {report.backtest_window['end']}",
        "This report describes PAST, cost-inclusive backtest behavior over the",
        "available history only. It is NOT a forecast, guarantee, or trading",
        "recommendation - see README \"Disclaimer\".",
        "=" * 72,
    ]
    for r in report.results:
        s = r.single_window_stats
        lines.append("")
        lines.append(f"--- {r.strategy_name} ({', '.join(r.symbols)}) ---")
        lines.append(
            f"  Single-window: return={s.get('total_return_pct')}% trades={s.get('total_trades')} "
            f"win_rate={s.get('win_rate_pct')}% sharpe={s.get('sharpe_ratio')} max_drawdown={s.get('max_drawdown_pct')}%"
        )
        bench_str = ", ".join(
            f"{b}={pct:.2f}%" if pct is not None else f"{b}=n/a" for b, pct in r.benchmark_buy_and_hold_pct.items()
        ) or "none configured"
        lines.append(f"  Buy & hold benchmarks (same window): {bench_str}")
        wf = r.walk_forward_report
        lines.append(
            f"  Walk-forward: {wf.num_folds} fold(s) of {wf.fold_period}, "
            f"{wf.num_profitable_folds} profitable "
            f"({(wf.fraction_profitable or 0.0) * 100:.0f}%)"
        )
        lines.append(f"  VERDICT: {r.verdict}")
        lines.append(f"    {r.verdict_reason}")
    return "\n".join(lines)


def save_validation_report_text(text: str, config: dict[str, Any], now: datetime | None = None) -> Path:
    reports_dir = resolve_path(config["data"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = (now or datetime.now()).strftime("%Y-%m-%d")
    out_path = reports_dir / f"strategy_validation_{date_str}.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main() -> int:
    """`python -m src.analytics.strategy_validation_report` - fetches real
    history for every strategy's configured universe plus benchmarks
    (same fetch `src/backtester.py`'s own CLI uses), builds the report
    against REAL market data, prints it, and saves it to
    `data/reports/strategy_validation_<date>.txt`. Read-only; never
    touches a broker."""
    from .. import data_collector
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="strategy_validation.log")

    universe = config["strategy_universe"]
    all_symbols = sorted(set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"]))
    benchmark_symbols = config["backtest"]["benchmarks"]
    symbols_to_fetch = sorted(set(all_symbols + benchmark_symbols))

    price_history = data_collector.fetch_all_price_history(symbols_to_fetch, config, logger)
    if not price_history:
        print("Strategy validation report aborted: no price data could be fetched.")
        return 1

    report = run_validation_report(price_history, config, logger, data_provenance="real_market_data")
    if report is None:
        print("Strategy validation report unavailable.")
        return 1

    text = format_validation_report_text(report)
    print(text)
    saved_path = save_validation_report_text(text, config)
    print(f"\nSaved to {saved_path}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
