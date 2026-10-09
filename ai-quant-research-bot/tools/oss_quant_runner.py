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
"vectorbt_benchmark", "ccxt_ohlcv". Never raises past `main()` - any
failure becomes `{"ok": false, "error": "...", "error_type": "..."}` on
stdout.
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
