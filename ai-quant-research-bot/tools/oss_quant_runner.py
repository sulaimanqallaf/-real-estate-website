#!/usr/bin/env python
"""Runs INSIDE the isolated `.venvs/oss_quant/` environment ONLY - never
imported or run from this project's own Python environment. See
`docs/platform/OSS_INTEGRATION_AUDIT.md` and
`src/analytics/oss_quant_adapter.py`'s module docstring for why.

This script is deliberately a thin, dumb boundary, mirroring
`tools/tradingagents_runner.py`'s exact pattern: it does real work with
QuantStats/VectorBT/CCXT and dumps plain JSON results - it does NOT
decide whether a strategy is good, does not promote anything, and does
not know anything about this project's own ledgers/schemas. All of that
interpretation lives in `src/analytics/`, which runs in the MAIN project
environment and only ever talks to this script over a subprocess
boundary (one JSON request file in; one JSON object on stdout; stderr
for logs) - so a dependency conflict or a breaking upstream release in
any of these three packages can never affect `src/main.py`,
`src/execution/`, or this project's own test suite.

Usage: python oss_quant_runner.py <request.json>
`request["task"]` selects one of: "performance_report",
"vectorbt_benchmark", "vectorbt_trade_replay", "ccxt_ohlcv". Never
raises past `main()` - any failure becomes `{"ok": false, "error":
"...", "error_type": "..."}` on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: oss_quant_runner.py <request.json>", "error_type": "UsageError"}))
        return 1

    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            request = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"could not read request file: {exc}", "error_type": type(exc).__name__}))
        return 1

    try:
        result = _dispatch(request)
    except Exception as exc:  # noqa: BLE001 - this process's only job is to never crash without reporting why
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}))
        return 0

    print(json.dumps(result, default=str))
    return 0


def _dispatch(request: dict[str, Any]) -> dict[str, Any]:
    task = request.get("task")
    if task == "performance_report":
        return _run_performance_report(request)
    if task == "vectorbt_benchmark":
        return _run_vectorbt_benchmark(request)
    if task == "vectorbt_trade_replay":
        return _run_vectorbt_trade_replay(request)
    if task == "ccxt_ohlcv":
        return _run_ccxt_ohlcv(request)
    return {"ok": False, "error": f"unknown task: {task!r}", "error_type": "UsageError"}


def _run_performance_report(request: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd
    import quantstats as qs

    returns_dict = request.get("returns") or {}
    if not returns_dict:
        return {"ok": False, "error": "no returns provided", "error_type": "ValueError"}

    returns = pd.Series(returns_dict)
    returns.index = pd.to_datetime(returns.index)
    returns = returns.sort_index()

    metrics = {
        "sharpe": _safe_float(qs.stats.sharpe(returns)),
        "sortino": _safe_float(qs.stats.sortino(returns)),
        "max_drawdown": _safe_float(qs.stats.max_drawdown(returns)),
        "profit_factor": _safe_float(qs.stats.profit_factor(returns)),
        "win_rate": _safe_float(qs.stats.win_rate(returns)),
        "avg_win": _safe_float(qs.stats.avg_win(returns)),
        "avg_loss": _safe_float(qs.stats.avg_loss(returns)),
        "exposure": _safe_float(qs.stats.exposure(returns)),
        "cagr": _safe_float(qs.stats.cagr(returns)),
        "volatility": _safe_float(qs.stats.volatility(returns)),
        "n_periods": int(len(returns)),
    }
    return {"ok": True, "metrics": metrics}


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _run_vectorbt_benchmark(request: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd
    import vectorbt as vbt

    prices_dict = request.get("prices") or {}
    if not prices_dict:
        return {"ok": False, "error": "no prices provided", "error_type": "ValueError"}

    price = pd.Series(prices_dict)
    price.index = pd.to_datetime(price.index)
    price = price.sort_index()

    fast_window = int(request.get("fast_window", 20))
    slow_window = int(request.get("slow_window", 50))
    commission = float(request.get("commission", 0.0))
    slippage = float(request.get("slippage", 0.0))
    init_cash = float(request.get("init_cash", 10000.0))

    fast_ma = vbt.MA.run(price, fast_window)
    slow_ma = vbt.MA.run(price, slow_window)
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)

    pf = vbt.Portfolio.from_signals(
        price, entries, exits, fees=commission, slippage=slippage, init_cash=init_cash, freq="1D",
    )

    trades_df = pf.trades.records_readable
    trades = []
    for _, row in trades_df.iterrows():
        trades.append({
            "entry_time": str(row.get("Entry Timestamp")),
            "exit_time": str(row.get("Exit Timestamp")),
            "entry_price": _safe_float(row.get("Avg Entry Price")),
            "exit_price": _safe_float(row.get("Avg Exit Price")),
            "pnl": _safe_float(row.get("PnL")),
            "return_pct": _safe_float(row.get("Return")),
        })

    return {
        "ok": True,
        "total_return": _safe_float(pf.total_return()),
        "sharpe": _safe_float(pf.sharpe_ratio()),
        "max_drawdown": _safe_float(pf.max_drawdown()),
        "num_trades": int(pf.trades.count()),
        "trades": trades,
    }


def _run_vectorbt_trade_replay(request: dict[str, Any]) -> dict[str, Any]:
    """Replays an EXACT, already-decided trade list (entries/exits/fill
    prices/share counts this project's own `src/backtester.py` already
    computed) through `vbt.Portfolio.from_signals` - this task does NOT
    generate its own signals (unlike `vectorbt_benchmark`'s MA
    crossover). It exists so `src/analytics/backtester_crosscheck.py`
    can ask "does VectorBT's independently-written stats engine agree
    with ours, fed the identical fills?" rather than "does VectorBT
    pick the same trades?" (a different, strategy-specific question
    VectorBT was never going to answer, since the entry/exit logic is
    this project's own, not VectorBT's).

    Overriding the price series at each entry/exit timestamp with the
    exact fill price (rather than relying on VectorBT's own candle
    data) is what makes the PnL figures comparable at all - see the
    inline comment below for why that's safe here (one position per
    symbol at a time, entry/exit dates from our backtester never
    collide).
    """
    import numpy as np
    import pandas as pd
    import vectorbt as vbt

    prices_dict = request.get("prices") or {}
    entries = request.get("entries") or []
    exits = request.get("exits") or []
    entry_prices = request.get("entry_prices") or []
    exit_prices = request.get("exit_prices") or []
    sizes = request.get("sizes") or []
    init_cash = float(request.get("init_cash", 10000.0))
    # VectorBT's own Sharpe/Sortino default to annualizing with
    # year_freq="365 days" (calendar days). src/backtester.py's
    # _compute_stats annualizes with sqrt(252) (US trading days/year) -
    # the correct convention for US equities, which is what this
    # project trades. Left at vectorbt's default, the two Sharpe
    # figures diverge by a confirmed, reproducible factor of
    # sqrt(365/252) =~ 1.20 even when fed byte-identical cash flows -
    # a methodology mismatch, not a computation error in either engine
    # (verified directly: forcing year_freq="252 days" here reproduces
    # our own backtester's Sharpe to within rounding). Matching the
    # convention, not just documenting the mismatch, is what makes this
    # a real apples-to-apples crosscheck.
    year_freq = f"{int(request.get('year_freq_days', 252))} days"

    if not prices_dict:
        return {"ok": False, "error": "no prices provided", "error_type": "ValueError"}
    if not (len(entries) == len(exits) == len(entry_prices) == len(exit_prices) == len(sizes)):
        return {"ok": False, "error": "entries/exits/entry_prices/exit_prices/sizes must be the same length", "error_type": "ValueError"}

    price = pd.Series(prices_dict, dtype=float)
    price.index = pd.to_datetime(price.index)
    price = price.sort_index()

    fill_price = price.copy()
    entries_bool = pd.Series(False, index=price.index)
    exits_bool = pd.Series(False, index=price.index)
    size_arr = pd.Series(np.nan, index=price.index)

    # Each (entry_date, exit_date) pair is written independently - this
    # project's backtester never opens a new position for a symbol
    # until at least one bar after the prior one closes, so these
    # timestamps never collide within one symbol's series.
    for entry_date, exit_date, entry_price, exit_price, size in zip(entries, exits, entry_prices, exit_prices, sizes):
        entry_ts = pd.Timestamp(entry_date)
        exit_ts = pd.Timestamp(exit_date)
        if entry_ts not in price.index or exit_ts not in price.index:
            return {"ok": False, "error": f"trade date {entry_date!r}/{exit_date!r} not found in the provided price series", "error_type": "ValueError"}
        entries_bool.loc[entry_ts] = True
        exits_bool.loc[exit_ts] = True
        fill_price.loc[entry_ts] = float(entry_price)
        fill_price.loc[exit_ts] = float(exit_price)
        size_arr.loc[entry_ts] = float(size)

    pf = vbt.Portfolio.from_signals(
        fill_price, entries_bool, exits_bool, size=size_arr, fees=0.0, slippage=0.0, init_cash=init_cash, freq="1D",
    )

    trades_df = pf.trades.records_readable
    trades = []
    for _, row in trades_df.iterrows():
        trades.append({
            "entry_time": str(row.get("Entry Timestamp")),
            "exit_time": str(row.get("Exit Timestamp")),
            "entry_price": _safe_float(row.get("Avg Entry Price")),
            "exit_price": _safe_float(row.get("Avg Exit Price")),
            "size": _safe_float(row.get("Size")),
            "pnl": _safe_float(row.get("PnL")),
            "return_pct": _safe_float(row.get("Return")),
        })

    return {
        "ok": True,
        "total_return": _safe_float(pf.total_return()),
        "sharpe": _safe_float(pf.sharpe_ratio(year_freq=year_freq)),
        "max_drawdown": _safe_float(pf.max_drawdown()),
        "num_trades": int(pf.trades.count()),
        "win_rate": _safe_float(pf.trades.win_rate()) if pf.trades.count() > 0 else None,
        "profit_factor": _safe_float(pf.trades.profit_factor()) if pf.trades.count() > 0 else None,
        "trades": trades,
    }


def _run_ccxt_ohlcv(request: dict[str, Any]) -> dict[str, Any]:
    import ccxt

    exchange_id = request.get("exchange", "binance")
    symbol = request.get("symbol")
    timeframe = request.get("timeframe", "1d")
    limit = int(request.get("limit", 30))

    if not symbol:
        return {"ok": False, "error": "no symbol provided", "error_type": "ValueError"}
    if not hasattr(ccxt, exchange_id):
        return {"ok": False, "error": f"unknown ccxt exchange id: {exchange_id!r}", "error_type": "ValueError"}

    exchange = getattr(ccxt, exchange_id)()
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as exc:  # noqa: BLE001 - a real network/exchange error, reported honestly, never fabricated as data
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    rows = [
        {"timestamp_ms": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in ohlcv
    ]
    return {"ok": True, "exchange": exchange_id, "symbol": symbol, "timeframe": timeframe, "rows": rows}


if __name__ == "__main__":
    raise SystemExit(main())
