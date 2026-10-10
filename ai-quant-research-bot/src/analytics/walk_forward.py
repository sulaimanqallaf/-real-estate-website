"""Walk-forward, out-of-sample strategy validation (Sprint 3, "Real
Strategy Validation" milestone, Task V2: "Use walk-forward and
out-of-sample testing.").

**What "out-of-sample" honestly means here.** None of the three
strategies in `src/strategies/` have any parameter FITTED to historical
data - there is no optimization step anywhere in this codebase (no grid
search, no ML training for the rule thresholds). So there is no
in-sample/out-of-sample split in the traditional walk-forward-
OPTIMIZATION sense (there's nothing to overfit a parameter to). What
this module actually validates is **consistency**: does this fixed,
never-tuned rule set perform similarly across several independent,
non-overlapping historical periods, or did `src/backtester.py`'s single
one-year window just get lucky (or unlucky)? That's the real
evidentiary question Sprint 3's "do not claim profitability without
sufficient evidence" is asking for, scoped honestly to what this
codebase actually is.

**How folds are built.** `_build_folds()` splits the data `symbols`
have in common (oldest common start to newest common end) into
sequential, NON-OVERLAPPING `fold_period`-long windows, oldest to
newest - fold 2 never sees fold 1's future, and no fold is ever reused.
Each fold is run through the EXACT SAME `backtester._run_strategy_backtest()`
production code path used everywhere else in this project (same
indicators, same risk_manager gates, same transaction-cost model from
Task V1) - this module adds no new trading logic of its own, only the
fold-splitting and cross-fold aggregation around it.

**Indicator lookback is never truncated.** A fold only has its FUTURE
side cut off (bars at/after the fold's end are dropped before calling
the backtester) - everything before the fold's start stays in the
DataFrame handed to `_run_strategy_backtest()`, exactly as
`backtester.run_backtest()` itself does (compute indicators on the full
series, then filter the OUTPUT by `backtest_start`). Truncating the
PAST too would starve a 200-day SMA/EMA of the real history it needs
and manufacture an artificial "no signal" result for the first ~200
days of every fold - a correctness bug, not realism.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import backtester

DEFAULT_FOLD_PERIOD = "90d"


@dataclass
class Fold:
    fold_index: int
    start: str
    end: str
    stats: dict[str, Any]
    benchmark_buy_and_hold_pct: dict[str, float | None] = field(default_factory=dict)


@dataclass
class WalkForwardReport:
    strategy_name: str
    symbols: list[str]
    fold_period: str
    folds: list[Fold] = field(default_factory=list)

    @property
    def num_folds(self) -> int:
        return len(self.folds)

    @property
    def fold_returns_pct(self) -> list[float]:
        return [f.stats["total_return_pct"] for f in self.folds if f.stats.get("total_return_pct") is not None]

    @property
    def num_profitable_folds(self) -> int:
        return sum(1 for r in self.fold_returns_pct if r > 0)

    @property
    def fraction_profitable(self) -> float | None:
        returns = self.fold_returns_pct
        return (self.num_profitable_folds / len(returns)) if returns else None

    @property
    def mean_fold_return_pct(self) -> float | None:
        returns = self.fold_returns_pct
        return float(np.mean(returns)) if returns else None

    @property
    def std_fold_return_pct(self) -> float | None:
        returns = self.fold_returns_pct
        return float(np.std(returns, ddof=1)) if len(returns) > 1 else None

    def fraction_beating_benchmark(self, benchmark: str) -> float | None:
        """Across folds where BOTH our return and `benchmark`'s
        buy-and-hold return for that same fold are known, what fraction
        did we beat it in? `None` (never 0.0) when no fold has both
        numbers - an honest "can't answer," not a fabricated 0%."""
        pairs = [
            (f.stats["total_return_pct"], f.benchmark_buy_and_hold_pct.get(benchmark))
            for f in self.folds
            if f.stats.get("total_return_pct") is not None and f.benchmark_buy_and_hold_pct.get(benchmark) is not None
        ]
        if not pairs:
            return None
        return sum(1 for ours, theirs in pairs if ours > theirs) / len(pairs)


def _build_folds(price_history: dict[str, pd.DataFrame], fold_period_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Sequential [start, end) windows covering only the date range
    every symbol in `price_history` actually has data for - never a
    window one symbol has no real bars in at all."""
    non_empty = {s: df for s, df in price_history.items() if not df.empty}
    if not non_empty:
        return []
    common_start = max(df.index.min() for df in non_empty.values())
    common_end = min(df.index.max() for df in non_empty.values())

    folds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = common_start
    step = timedelta(days=fold_period_days)
    while cursor + step <= common_end:
        folds.append((cursor, cursor + step))
        cursor = cursor + step
    return folds


def run_walk_forward(
    strategy_name: str,
    symbols: list[str],
    price_history: dict[str, pd.DataFrame],
    config: dict[str, Any],
    logger: logging.Logger,
    fold_period: str | None = None,
    benchmark_symbols: list[str] | None = None,
) -> WalkForwardReport:
    """Runs `strategy_name` independently on each non-overlapping
    `fold_period`-long fold of real history, each fold starting fresh
    at `config["backtest"]["initial_capital"]` (these are fixed
    rule-based strategies with no state to carry across folds, so
    there's nothing to "warm start" - see module docstring). Returns a
    `WalkForwardReport` with zero folds (never fabricated results) when
    `symbols` have no overlapping history long enough for even one
    fold."""
    fold_period = fold_period or config.get("backtest", {}).get("walk_forward", {}).get("fold_period", DEFAULT_FOLD_PERIOD)
    benchmark_symbols = benchmark_symbols if benchmark_symbols is not None else config["backtest"]["benchmarks"]
    initial_capital = config["backtest"]["initial_capital"]

    relevant_history = {s: price_history[s] for s in symbols if s in price_history}
    fold_period_days = backtester.period_to_days(fold_period)
    windows = _build_folds(relevant_history, fold_period_days)

    folds: list[Fold] = []
    for i, (start, end) in enumerate(windows):
        # Future side cut off per fold; past side left intact for indicator lookback - see module docstring.
        windowed_history = {s: df[df.index < end] for s, df in relevant_history.items()}
        trades, equity_curve = backtester._run_strategy_backtest(strategy_name, symbols, windowed_history, start, config, logger)
        stats = backtester._compute_stats(trades, equity_curve, initial_capital)

        bench_returns: dict[str, float | None] = {}
        for bench in benchmark_symbols:
            bdf = price_history.get(bench)
            if bdf is not None:
                bench_returns[bench] = backtester._buy_and_hold_return_pct(bdf[bdf.index < end], start)

        folds.append(Fold(fold_index=i, start=str(start.date()), end=str(end.date()), stats=stats, benchmark_buy_and_hold_pct=bench_returns))

    logger.info("Walk-forward (%s, %s): %d fold(s) of %s each.", strategy_name, ",".join(symbols), len(folds), fold_period)
    return WalkForwardReport(strategy_name=strategy_name, symbols=symbols, fold_period=fold_period, folds=folds)


def run_walk_forward_all_strategies(
    price_history: dict[str, pd.DataFrame], config: dict[str, Any], logger: logging.Logger, fold_period: str | None = None
) -> dict[str, WalkForwardReport]:
    """Convenience wrapper mirroring `backtester.run_backtest()`'s own
    per-strategy loop, one `WalkForwardReport` per strategy over its
    own configured universe."""
    universe = config["strategy_universe"]
    strategy_universes = {
        "Mean Reversion": universe["mean_reversion"],
        "Momentum Breakout": universe["momentum_breakout"],
        "Trend Following": universe["trend_following"],
    }
    return {
        name: run_walk_forward(name, symbols, price_history, config, logger, fold_period=fold_period)
        for name, symbols in strategy_universes.items()
    }


def format_walk_forward_text(report: WalkForwardReport) -> str:
    lines = [f"Walk-forward validation: {report.strategy_name} on {', '.join(report.symbols)} ({report.fold_period} folds)"]
    if not report.folds:
        lines.append("  No folds - not enough overlapping history for even one fold period.")
        return "\n".join(lines)

    for f in report.folds:
        bench_str = ", ".join(f"{b}={pct:.2f}%" if pct is not None else f"{b}=n/a" for b, pct in f.benchmark_buy_and_hold_pct.items())
        lines.append(
            f"  Fold {f.fold_index} [{f.start} -> {f.end}]: return={f.stats.get('total_return_pct')}% "
            f"trades={f.stats.get('total_trades')} sharpe={f.stats.get('sharpe_ratio')} vs buy&hold: {bench_str}"
        )

    lines.append(
        f"  Summary: {report.num_folds} fold(s), {report.num_profitable_folds} profitable "
        f"({report.fraction_profitable * 100:.0f}%), mean return {report.mean_fold_return_pct:.2f}%, "
        f"std dev {report.std_fold_return_pct if report.std_fold_return_pct is not None else 'n/a'}"
    )
    for bench in {b for f in report.folds for b in f.benchmark_buy_and_hold_pct}:
        frac = report.fraction_beating_benchmark(bench)
        lines.append(f"  Beat {bench} buy & hold in {frac * 100:.0f}% of folds" if frac is not None else f"  Beat {bench} buy & hold: n/a")
    return "\n".join(lines)


def main() -> int:
    """`python -m src.analytics.walk_forward [--fold-period 90d]` - fetches
    real history for every strategy's configured universe (same fetch
    `src/backtester.py`'s own CLI uses) and prints a walk-forward report
    per strategy. Read-only; never touches a broker."""
    import argparse

    from .. import data_collector
    from ..utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Walk-forward, out-of-sample strategy validation.")
    parser.add_argument("--fold-period", default=None, help="Fold length, e.g. '90d', '6mo', '1y' (default: config/settings.yaml's backtest.walk_forward.fold_period).")
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="walk_forward.log")

    universe = config["strategy_universe"]
    all_symbols = sorted(set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"]))
    benchmark_symbols = config["backtest"]["benchmarks"]
    symbols_to_fetch = sorted(set(all_symbols + benchmark_symbols))

    price_history = data_collector.fetch_all_price_history(symbols_to_fetch, config, logger)
    if not price_history:
        print("Walk-forward validation aborted: no price data could be fetched.")
        return 1

    reports = run_walk_forward_all_strategies(price_history, config, logger, fold_period=args.fold_period)
    for name, report in reports.items():
        print(format_walk_forward_text(report))
        print()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
