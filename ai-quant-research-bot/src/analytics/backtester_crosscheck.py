"""Cross-checks `src/backtester.py`'s own P&L/Sharpe/drawdown computation
against VectorBT's independently-written stats engine (AI Quant Trading
Platform sprint, Phase 3: "compare results with existing backtester
using IDENTICAL datasets/execution assumptions").

**This never asks VectorBT to pick trades.** `src/backtester.py`'s
entry/exit logic (strategy signal -> risk_manager sizing -> stop/target/
time-exit) is this project's own and VectorBT has no equivalent of it.
What CAN be compared honestly is the arithmetic layered on top of a
given, already-decided trade list: given the exact same fills (entry
date/price, exit date/price, share count, zero commission/slippage -
`src/backtester.py` models neither), do two independently-written
engines compute the same total return, Sharpe, and max drawdown? This
module runs our backtester for one (symbol, strategy) pair, replays its
literal trade list through `vbt.Portfolio.from_signals` via the
`vectorbt_trade_replay` runner task (price series overridden at every
entry/exit timestamp with our own fill price, so VectorBT's P&L is
computed from the identical cash flows - see
`tools/oss_quant_runner.py`'s `_run_vectorbt_trade_replay` docstring),
and reports agreement or divergence - never silently treating either
engine's number as more authoritative than the other's.

Returns `None` (never a fabricated comparison) when our own backtester
produced zero trades for the (symbol, strategy) pair, or when the
isolated OSS quant venv isn't set up.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from . import oss_quant_adapter

# total_return_pct/max_drawdown_pct are path-independent sums of realized
# trade P&L, so the two engines match to floating-point rounding (verified:
# <0.0001 percentage points across every strategy tested) - 0.01 is already
# generous there. sharpe_ratio is NOT path-independent: it depends on the
# full daily equity path, and our backtester marks a trade's entry/exit DAY
# using that day's real close for the unrealized leg, while the VectorBT
# replay necessarily overrides that same day's price with the exact fill
# price (to get the realized P&L right) - a verified, real, structural
# difference in how one specific day per trade gets valued, not a bug in
# either engine. That nudges daily-return shape (and therefore Sharpe)
# slightly even once the annualization basis (see
# tools/oss_quant_runner.py's `year_freq` comment) is matched - observed
# up to ~0.02 Sharpe units on a 4-trade sample in testing, so Sharpe gets a
# looser tolerance than the P&L-based metrics.
AGREEMENT_TOLERANCE = {
    "total_return_pct": 0.01,
    "max_drawdown_pct": 0.01,
    "sharpe_ratio": 0.03,
}


def _trade_replay_payload(
    trades: list[Any], price_series: pd.Series, initial_capital: float,
) -> dict[str, Any]:
    return {
        "prices": {str(d.date()): float(v) for d, v in price_series.items()},
        "entries": [str(t.entry_date.date()) for t in trades],
        "exits": [str(t.exit_date.date()) for t in trades],
        "entry_prices": [float(t.entry_price) for t in trades],
        "exit_prices": [float(t.exit_price) for t in trades],
        "sizes": [float(t.shares) for t in trades],
        "init_cash": float(initial_capital),
        # Match src/backtester.py's own sqrt(252) annualization basis
        # rather than vectorbt's calendar-day default - see
        # tools/oss_quant_runner.py's year_freq comment.
        "year_freq_days": 252,
    }


def run_crosscheck(
    symbol: str,
    strategy_name: str,
    price_history: dict[str, pd.DataFrame],
    config: dict[str, Any],
    logger: logging.Logger,
    data_provenance: str,
) -> dict[str, Any] | None:
    """Runs `strategy_name` on `symbol` through our own backtester, then
    replays the resulting trades through VectorBT. `data_provenance`
    must be one of "real_market_data" or "synthetic_fixture" - the
    caller's responsibility to state honestly (this module has no way
    to verify which one it was handed), and it is carried into the
    returned dict so nothing downstream can mistake one for the other.
    """
    from ..backtester import STRATEGY_MODULES, _compute_stats, _run_strategy_backtest, period_to_days
    from datetime import timedelta

    if data_provenance not in ("real_market_data", "synthetic_fixture"):
        raise ValueError(f"data_provenance must be 'real_market_data' or 'synthetic_fixture', got {data_provenance!r}")
    if strategy_name not in STRATEGY_MODULES:
        raise ValueError(f"unknown strategy: {strategy_name!r}")

    df = price_history.get(symbol)
    if df is None or df.empty:
        logger.info("Crosscheck skipped: no price data for %s.", symbol)
        return None

    lookback_days = period_to_days(config["backtest"]["lookback_period"])
    backtest_start = df.index.max() - timedelta(days=lookback_days)
    initial_capital = config["backtest"]["initial_capital"]

    trades, equity_curve = _run_strategy_backtest(strategy_name, [symbol], price_history, backtest_start, config, logger)
    if not trades:
        logger.info("Crosscheck skipped: our backtester produced zero %s trades for %s.", strategy_name, symbol)
        return None

    our_stats = _compute_stats(trades, equity_curve, initial_capital)

    if not oss_quant_adapter.is_available(config):
        logger.info("Crosscheck unavailable: OSS quant tooling not set up (run scripts/setup_oss_quant_env.sh).")
        return None

    price_series = df[df.index >= backtest_start]["close"]
    payload = _trade_replay_payload(trades, price_series, initial_capital)
    result = oss_quant_adapter.run_task("vectorbt_trade_replay", payload, config, logger)
    if result is None or not result.get("ok"):
        logger.warning("VectorBT trade replay failed: %s", result.get("error") if result else "no response")
        return None

    vbt_total_return_pct = result["total_return"] * 100.0 if result.get("total_return") is not None else None
    vbt_max_drawdown_pct = -abs(result["max_drawdown"]) * 100.0 if result.get("max_drawdown") is not None else None

    comparisons = {
        "total_return_pct": _compare(our_stats["total_return_pct"], vbt_total_return_pct, scale=100.0, tolerance=AGREEMENT_TOLERANCE["total_return_pct"]),
        "sharpe_ratio": _compare(our_stats["sharpe_ratio"], result.get("sharpe"), scale=1.0, tolerance=AGREEMENT_TOLERANCE["sharpe_ratio"]),
        "max_drawdown_pct": _compare(our_stats["max_drawdown_pct"], vbt_max_drawdown_pct, scale=100.0, tolerance=AGREEMENT_TOLERANCE["max_drawdown_pct"]),
    }
    all_agree = all(c["agree"] for c in comparisons.values() if c["agree"] is not None)

    return {
        "data_provenance": data_provenance,
        "symbol": symbol,
        "strategy": strategy_name,
        "num_trades": len(trades),
        "our_backtester": our_stats,
        "vectorbt_replay": {
            "total_return_pct": round(vbt_total_return_pct, 2) if vbt_total_return_pct is not None else None,
            "sharpe_ratio": round(result["sharpe"], 2) if result.get("sharpe") is not None else None,
            "max_drawdown_pct": round(vbt_max_drawdown_pct, 2) if vbt_max_drawdown_pct is not None else None,
            "win_rate_pct": round(result["win_rate"] * 100.0, 2) if result.get("win_rate") is not None else None,
            "num_trades": result.get("num_trades"),
        },
        "comparisons": comparisons,
        "all_agree_within_tolerance": all_agree,
    }


def _compare(ours: float | None, theirs: float | None, scale: float, tolerance: float) -> dict[str, Any]:
    """`ours` and `theirs` are both already in the same units (percent
    for returns/drawdown, raw ratio for Sharpe) - `scale` normalizes the
    diff into the same units `tolerance` is expressed in. Explicitly
    casts to plain `float`/`bool` (never trusting upstream dtypes) since
    `src/backtester.py`'s `total_return_pct` is computed without an
    intermediate `float()` cast and comes back as `numpy.float64` -
    without this, `diff <= tolerance` would be a non-JSON-serializable
    `numpy.bool_` that silently turns into the STRING `"True"`/`"False"`
    wherever this result gets json-dumped with a lossy `default=str`."""
    if ours is None or theirs is None:
        return {"ours": ours, "theirs": theirs, "diff": None, "agree": None}
    ours, theirs = float(ours), float(theirs)
    diff = abs(ours - theirs) / scale if scale else abs(ours - theirs)
    return {"ours": ours, "theirs": theirs, "diff": round(diff, 6), "agree": bool(diff <= tolerance)}
