"""Version 1 backtester for the three price-based strategies.

Simplifications (documented rather than hidden, since this is a research tool):
  - Daily bars only (yfinance intraday history is too short for a 1-year test).
  - Each strategy gets its own independent capital pool (config.backtest.initial_capital) -
    this is NOT a shared-margin, multi-strategy portfolio simulation. A "Combined"
    block sums the three pools' dollar P&L for a rough blended view.
  - One open position per symbol per strategy at a time (no pyramiding).
  - Signals are computed from a bar's CLOSE; entries fill at the NEXT bar's OPEN
    (avoids lookahead bias). Exits check stop before target if both would trigger
    intraday on the same bar (conservative assumption).
  - A position still open at the end of the data window is closed at the last
    available close ("EOD" mark-to-market exit) rather than left dangling.
  - Every simulated entry is passed through the same risk_manager rules used live
    (SMA200 filter, RSI cap, min risk/reward, downside<upside) - trades that would
    be blocked live are never opened in the backtest either.

Options skew is NOT backtested in Version 1 (Yahoo's free IV history isn't reliably
available historically) - only the three price-based strategies are.

Mean Reversion is backtested in its Safe mode only (mean_reversion.evaluate()'s
default mode="safe") - Aggressive mode is a live-report-only feature (see
report_writer.py's "High Risk Dip Watchlist") and is not exercised here.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import data_collector, indicators, risk_manager
from .strategies import mean_reversion, momentum_breakout, trend_following
from .utils import resolve_path, safe_run

STRATEGY_MODULES = {
    "Mean Reversion": mean_reversion,
    "Momentum Breakout": momentum_breakout,
    "Trend Following": trend_following,
}


@dataclass
class Trade:
    symbol: str
    strategy: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    shares: int
    exit_reason: str

    @property
    def pnl_dollars(self) -> float:
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def return_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0 if self.entry_price else 0.0


@dataclass
class OpenPosition:
    symbol: str
    strategy: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float
    target: float
    shares: int


def period_to_days(period_str: str) -> int:
    """Convert a yfinance-style period string ('1y', '6mo', '90d') to calendar days."""
    match = re.match(r"^(\d+)(d|mo|y)$", period_str.strip())
    if not match:
        raise ValueError(f"Unrecognized period string: {period_str!r}")
    n, unit = int(match.group(1)), match.group(2)
    return {"d": 1, "mo": 30, "y": 365}[unit] * n


def _run_strategy_backtest(
    strategy_name: str,
    symbols: list[str],
    price_history: dict[str, pd.DataFrame],
    backtest_start: pd.Timestamp,
    config: dict[str, Any],
    logger: logging.Logger,
) -> tuple[list[Trade], pd.Series]:
    strategy_module = STRATEGY_MODULES[strategy_name]
    initial_capital = config["backtest"]["initial_capital"]
    max_holding_days = config["backtest"]["max_holding_days"]

    sizing_config = copy.deepcopy(config)
    sizing_config["risk"]["account_equity"] = initial_capital
    sizing_config["risk"]["risk_pct_per_trade"] = config["backtest"]["risk_pct_per_trade"]

    all_trades: list[Trade] = []
    all_dates: set[pd.Timestamp] = set()
    daily_marks: dict[str, dict[pd.Timestamp, float]] = {}  # symbol -> date -> close, for mark-to-market

    for symbol in symbols:
        df = price_history.get(symbol)
        if df is None:
            continue

        df_ind = indicators.compute_all_indicators(df, config)
        df_ind = df_ind[df_ind.index >= backtest_start]
        if df_ind.empty:
            continue

        daily_marks[symbol] = df_ind["close"].to_dict()
        all_dates.update(df_ind.index)

        open_position: OpenPosition | None = None
        rows = df_ind.reset_index()

        for i in range(len(rows) - 1):  # need i+1 (next bar's open) to fill an entry
            row = rows.iloc[i]
            next_row = rows.iloc[i + 1]
            snapshot = indicators.snapshot_from_row(row)

            if open_position is not None:
                low, high, close = next_row["low"], next_row["high"], next_row["close"]
                days_held = (next_row["date"] - open_position.entry_date).days

                if low <= open_position.stop_loss:
                    all_trades.append(
                        Trade(
                            symbol, strategy_name, open_position.entry_date, open_position.entry_price,
                            next_row["date"], open_position.stop_loss, open_position.shares, "stop_loss",
                        )
                    )
                    open_position = None
                elif high >= open_position.target:
                    all_trades.append(
                        Trade(
                            symbol, strategy_name, open_position.entry_date, open_position.entry_price,
                            next_row["date"], open_position.target, open_position.shares, "target",
                        )
                    )
                    open_position = None
                elif days_held >= max_holding_days:
                    all_trades.append(
                        Trade(
                            symbol, strategy_name, open_position.entry_date, open_position.entry_price,
                            next_row["date"], close, open_position.shares, "time_exit",
                        )
                    )
                    open_position = None
                continue  # one position at a time; don't evaluate a new entry same loop step

            result = strategy_module.evaluate(snapshot, config)
            candidate = result.get("candidate")
            if not candidate:
                continue

            risk_result = risk_manager.evaluate_candidate(candidate, snapshot, sizing_config)
            if not risk_result["tradeable"] or risk_result["shares"] <= 0:
                continue

            entry_price = next_row["open"]
            open_position = OpenPosition(
                symbol=symbol,
                strategy=strategy_name,
                entry_date=next_row["date"],
                entry_price=entry_price,
                stop_loss=risk_result["stop_loss"],
                target=risk_result["target"],
                shares=risk_result["shares"],
            )

        if open_position is not None:
            last_row = rows.iloc[-1]
            all_trades.append(
                Trade(
                    symbol, strategy_name, open_position.entry_date, open_position.entry_price,
                    last_row["date"], last_row["close"], open_position.shares, "eod_mark",
                )
            )

    equity_curve = _build_equity_curve(all_trades, daily_marks, sorted(all_dates), initial_capital)
    logger.info("%s backtest: %d trades across %d symbols", strategy_name, len(all_trades), len(symbols))
    return all_trades, equity_curve


def _build_equity_curve(
    trades: list[Trade],
    daily_marks: dict[str, dict[pd.Timestamp, float]],
    all_dates: list[pd.Timestamp],
    initial_capital: float,
) -> pd.Series:
    equity = pd.Series(index=all_dates, dtype=float)

    for day in all_dates:
        realized = sum(t.pnl_dollars for t in trades if t.exit_date <= day)
        unrealized = 0.0
        for t in trades:
            if t.entry_date <= day < t.exit_date:
                mark = daily_marks.get(t.symbol, {}).get(day)
                if mark is not None:
                    unrealized += (mark - t.entry_price) * t.shares
        equity[day] = initial_capital + realized + unrealized

    return equity


def _compute_stats(trades: list[Trade], equity_curve: pd.Series, initial_capital: float) -> dict[str, Any]:
    if not trades or equity_curve.empty:
        return {
            "total_trades": 0, "total_return_pct": 0.0, "win_rate_pct": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_factor": None, "max_drawdown_pct": 0.0, "sharpe_ratio": None,
        }

    pnls = [t.pnl_dollars for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_return_pct = (equity_curve.iloc[-1] - initial_capital) / initial_capital * 100.0

    win_rate_pct = len(wins) / len(trades) * 100.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max * 100.0
    max_drawdown_pct = float(drawdown.min())

    daily_returns = equity_curve.pct_change().dropna()
    sharpe_ratio = None
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe_ratio = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))

    return {
        "total_trades": len(trades),
        "total_return_pct": round(total_return_pct, 2),
        "win_rate_pct": round(win_rate_pct, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2) if sharpe_ratio is not None else None,
    }


def _buy_and_hold_return_pct(df: pd.DataFrame, backtest_start: pd.Timestamp) -> float | None:
    window = df[df.index >= backtest_start]
    if len(window) < 2:
        return None
    return float((window["close"].iloc[-1] / window["close"].iloc[0] - 1.0) * 100.0)


def run_backtest(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    universe = config["strategy_universe"]
    all_symbols = sorted(set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"]))
    benchmark_symbols = config["backtest"]["benchmarks"]
    symbols_to_fetch = sorted(set(all_symbols + benchmark_symbols))

    price_history = data_collector.fetch_all_price_history(symbols_to_fetch, config, logger)
    if not price_history:
        logger.error("Backtest aborted: no price data could be fetched.")
        return {}

    lookback_days = period_to_days(config["backtest"]["lookback_period"])
    latest_date = max(df.index.max() for df in price_history.values())
    backtest_start = latest_date - timedelta(days=lookback_days)

    results: dict[str, Any] = {"backtest_start": str(backtest_start.date()), "backtest_end": str(latest_date.date())}

    strategy_universes = {
        "Mean Reversion": universe["mean_reversion"],
        "Momentum Breakout": universe["momentum_breakout"],
        "Trend Following": universe["trend_following"],
    }

    all_trades_combined: list[Trade] = []
    per_strategy: dict[str, Any] = {}

    for strategy_name, symbols in strategy_universes.items():
        trades, equity_curve = safe_run(
            logger,
            f"{strategy_name} backtest",
            lambda sn=strategy_name, sy=symbols: _run_strategy_backtest(
                sn, sy, price_history, backtest_start, config, logger
            ),
        ) or ([], pd.Series(dtype=float))

        stats = _compute_stats(trades, equity_curve, config["backtest"]["initial_capital"])
        per_strategy[strategy_name] = {
            "stats": stats,
            "trades": trades,
            "equity_curve": equity_curve,
        }
        all_trades_combined.extend(trades)

    combined_dates = sorted(set().union(*(ps["equity_curve"].index for ps in per_strategy.values() if not ps["equity_curve"].empty)))
    if combined_dates:
        combined_equity = sum(
            ps["equity_curve"].reindex(combined_dates).ffill().fillna(config["backtest"]["initial_capital"])
            for ps in per_strategy.values()
        )
        combined_capital = config["backtest"]["initial_capital"] * len(per_strategy)
        combined_stats = _compute_stats(all_trades_combined, combined_equity, combined_capital)
    else:
        combined_equity = pd.Series(dtype=float)
        combined_stats = _compute_stats([], combined_equity, config["backtest"]["initial_capital"])

    benchmark_returns = {}
    for bench in benchmark_symbols:
        df = price_history.get(bench)
        if df is not None:
            benchmark_returns[bench] = _buy_and_hold_return_pct(df, backtest_start)

    results["per_strategy"] = {name: ps["stats"] for name, ps in per_strategy.items()}
    results["combined"] = combined_stats
    results["benchmark_buy_and_hold_pct"] = benchmark_returns
    results["_trades"] = {name: ps["trades"] for name, ps in per_strategy.items()}
    results["_equity_curves"] = {name: ps["equity_curve"] for name, ps in per_strategy.items()}
    results["_combined_equity_curve"] = combined_equity

    return results


def save_backtest_report(results: dict[str, Any], config: dict[str, Any]) -> Path:
    reports_dir = resolve_path(config["data"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    summary_rows = []
    for name, stats in results.get("per_strategy", {}).items():
        summary_rows.append({"strategy": name, **stats})
    summary_rows.append({"strategy": "Combined (all strategies)", **results.get("combined", {})})
    for bench, ret in results.get("benchmark_buy_and_hold_pct", {}).items():
        summary_rows.append({"strategy": f"Buy & Hold {bench}", "total_return_pct": ret})

    csv_path = reports_dir / f"backtest_{date_str}.csv"
    pd.DataFrame(summary_rows).to_csv(csv_path, index=False)

    for name, trades in results.get("_trades", {}).items():
        if trades:
            trades_df = pd.DataFrame(
                [
                    {
                        "symbol": t.symbol, "entry_date": t.entry_date, "entry_price": t.entry_price,
                        "exit_date": t.exit_date, "exit_price": t.exit_price, "shares": t.shares,
                        "pnl_dollars": round(t.pnl_dollars, 2), "return_pct": round(t.return_pct, 2),
                        "exit_reason": t.exit_reason,
                    }
                    for t in trades
                ]
            )
            safe_name = name.lower().replace(" ", "_")
            trades_df.to_csv(reports_dir / f"backtest_trades_{safe_name}_{date_str}.csv", index=False)

    logger_path = reports_dir / f"backtest_{date_str}.csv"
    return logger_path


def print_summary(results: dict[str, Any]) -> None:
    print(f"\nBacktest window: {results.get('backtest_start')} to {results.get('backtest_end')}\n")
    for name, stats in results.get("per_strategy", {}).items():
        print(f"--- {name} ---")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print()
    print("--- Combined (all strategies) ---")
    for k, v in results.get("combined", {}).items():
        print(f"  {k}: {v}")
    print("\n--- Buy & Hold Benchmarks ---")
    for bench, ret in results.get("benchmark_buy_and_hold_pct", {}).items():
        print(f"  {bench}: {ret}%")


if __name__ == "__main__":
    from .utils import load_config, load_env, setup_logging

    load_env()
    cfg = load_config()
    log = setup_logging(cfg, log_filename="backtest.log")
    backtest_results = run_backtest(cfg, log)
    if backtest_results:
        save_backtest_report(backtest_results, cfg)
        print_summary(backtest_results)
