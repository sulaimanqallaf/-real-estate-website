"""Monte Carlo stress testing via bootstrap resampling of REAL per-trade
returns (AI Quant Trading Platform sprint, Phase 4: "add Monte Carlo
stress tests... never promote a strategy on in-sample performance
alone").

Built natively rather than via QuantStats' `qs.stats.montecarlo()`:
that function's real signature (verified by direct execution during
the OSS audit - see docs/platform/OSS_INTEGRATION_AUDIT.md) does not
take the `n`-simulations keyword this module needs, and a hand-written
bootstrap is simple enough, and important enough to get exactly right,
to own directly rather than work around a mismatched third-party API.

**What this answers**: given the trades that actually closed, how much
would the reported total return and max drawdown have varied if those
same trades had happened in a different ORDER (or with some repeated
and others never occurring - that's what "with replacement" means)?
A strategy whose real performance depends heavily on one lucky trade
landing early, or on a lucky absence of an unlucky streak, shows up
here as a wide, mostly-positive-skewed distribution even though its
single observed backtest/paper-trading run looked clean.

**What this does NOT answer**: nothing about whether FUTURE trades
will resemble these ones (the trade count, win rate, and the
distribution's shape are all frozen at whatever this project's real
trade history happens to be) - it stress-tests sequencing risk within
the data that exists, not out-of-sample generalization. The bar for
real forward-test evidence stays `performance_report.py` /
`backtester_crosscheck.py`'s honest out-of-sample validation, not this
module.
"""

from __future__ import annotations

from typing import Any

import numpy as np

MIN_TRADES_FOR_STRESS_TEST = 10


def run_monte_carlo_stress_test(
    trade_returns_pct: list[float],
    n_simulations: int = 2000,
    seed: int | None = None,
) -> dict[str, Any] | None:
    """`trade_returns_pct` is one return per CLOSED trade, in percent
    (matching `decision_ledger`'s `pnl_pct` column), in no particular
    order - this function treats trade sequencing itself as the thing
    being stress-tested, so it deliberately does NOT assume (or
    require) `trade_returns_pct` to already be chronologically sorted.

    Returns `None` (never a fabricated distribution) below
    `MIN_TRADES_FOR_STRESS_TEST` trades - a bootstrap over a handful of
    trades mostly just re-samples the same few numbers and would
    overstate how much is really known about sequencing risk.
    """
    n_trades = len(trade_returns_pct)
    if n_trades < MIN_TRADES_FOR_STRESS_TEST:
        return None

    returns_fraction = np.asarray(trade_returns_pct, dtype=float) / 100.0
    rng = np.random.default_rng(seed)

    final_returns = np.empty(n_simulations, dtype=float)
    max_drawdowns = np.empty(n_simulations, dtype=float)

    for i in range(n_simulations):
        sampled = rng.choice(returns_fraction, size=n_trades, replace=True)
        equity = np.cumprod(1.0 + sampled)
        final_returns[i] = equity[-1] - 1.0
        running_max = np.maximum.accumulate(equity)
        drawdown = (equity - running_max) / running_max
        max_drawdowns[i] = drawdown.min()

    observed_equity = np.cumprod(1.0 + returns_fraction)
    observed_total_return = float(observed_equity[-1] - 1.0)
    observed_running_max = np.maximum.accumulate(observed_equity)
    observed_max_drawdown = float(((observed_equity - observed_running_max) / observed_running_max).min())

    def _pct(arr: np.ndarray, q: float) -> float:
        return round(float(np.percentile(arr, q)) * 100.0, 2)

    return {
        "n_trades": n_trades,
        "n_simulations": n_simulations,
        "observed_total_return_pct": round(observed_total_return * 100.0, 2),
        "observed_max_drawdown_pct": round(observed_max_drawdown * 100.0, 2),
        "simulated_total_return_pct": {
            "p5": _pct(final_returns, 5), "p25": _pct(final_returns, 25), "p50": _pct(final_returns, 50),
            "p75": _pct(final_returns, 75), "p95": _pct(final_returns, 95),
        },
        "simulated_max_drawdown_pct": {
            "p5": _pct(max_drawdowns, 5), "p25": _pct(max_drawdowns, 25), "p50": _pct(max_drawdowns, 50),
            "p75": _pct(max_drawdowns, 75), "p95": _pct(max_drawdowns, 95),
        },
        "probability_of_loss": round(float((final_returns < 0.0).mean()), 4),
        "worst_case_drawdown_pct": _pct(max_drawdowns, 1),
    }
