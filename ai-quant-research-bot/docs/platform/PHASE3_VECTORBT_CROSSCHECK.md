# Phase 3: VectorBT cross-check of `src/backtester.py`

Per the sprint instruction: *"evaluate VectorBT for parameter sweeps; compare
results with existing backtester using IDENTICAL datasets/execution
assumptions; require agreement on commissions/slippage/fills/timestamps/
returns... never promote a strategy on in-sample performance alone."*

## Scope decision: a replay cross-check, not a second signal engine

VectorBT does not know this project's strategies (SMA/EMA/RSI/ATR-based entry
rules, `risk_manager`'s position sizing, stop/target/time-exit logic) and was
never going to re-derive the same trades independently - that is this
project's own code, not something VectorBT ships. Asking "does VectorBT pick
the same trades as us" would therefore not be a meaningful test.

What **is** meaningful, and what this phase actually built
(`src/analytics/backtester_crosscheck.py` + `tools/oss_quant_runner.py`'s
`vectorbt_trade_replay` task): given the EXACT trade list our own backtester
already decided on (same entry/exit dates, same fill prices, same share
counts, zero commission and zero slippage - `src/backtester.py` models
neither), does VectorBT's independently-written portfolio/stats engine agree
with our own `_compute_stats` on total return, max drawdown, and Sharpe? This
is a correctness cross-check on the arithmetic layered on top of a decision,
not a test of the decision itself.

## Real data vs. fixture data (honest disclosure)

This sandbox's outbound network policy blocks `query1.finance.yahoo.com`
(`curl: (7) CONNECT tunnel failed, response 403`) exactly like the
CCXT/crypto-exchange block already documented in
`docs/platform/OSS_INTEGRATION_AUDIT.md` - confirmed directly, not assumed.
No real market data could be fetched to run this cross-check in this
environment.

**Every result in this document is from `src/analytics/synthetic_fixtures
.generate_synthetic_ohlcv`** - a seeded geometric random walk with positive
drift, clearly labeled `"data_provenance": "synthetic_fixture"` in every
returned comparison dict. It exists only to exercise the strategy/backtester/
VectorBT code paths end-to-end deterministically; it is not evidence of
real-world strategy performance, and `backtester_crosscheck.run_crosscheck`
requires its caller to say which kind of data was used so this distinction
can never be silently lost downstream (the dashboard, the final report).
Re-running this cross-check against real history is on the roadmap for a
network environment that can actually reach a market-data provider.

## Method

1. Run `src/backtester.py`'s real `_run_strategy_backtest` for one
   (symbol, strategy) pair - real trade-selection and sizing logic, not a
   stand-in.
2. Take that exact trade list (`entry_date`, `entry_price`, `exit_date`,
   `exit_price`, `shares`) and feed it to `vbt.Portfolio.from_signals` via a
   price series that is overridden, at each entry/exit timestamp only, with
   the exact fill price - this is what makes the two engines' cash flows
   byte-identical rather than approximately similar (verified: see "Fill
   price override" below).
3. Compare `total_return_pct`, `max_drawdown_pct`, `sharpe_ratio`.

## Finding #1 (real, root-caused, fixed): Sharpe annualization basis

First run, fed identical trades, Sharpe did **not** agree
(`Trend Following`: ours `1.01` vs VectorBT's default `1.21` - a ~20%
divergence on byte-identical cash flows). Root-caused, not assumed: VectorBT's
`Portfolio.sharpe_ratio()` defaults to `year_freq="365 days"` (calendar days).
`src/backtester.py`'s own `_compute_stats` annualizes with `sqrt(252)` (US
trading days/year - the correct convention for the US equities this project
trades). Verified directly: forcing `year_freq="252 days"` on the same
VectorBT portfolio reproduced our own Sharpe almost exactly (`1.0037` vs our
`1.01`).

**Fix applied** (not just documented): `tools/oss_quant_runner.py`'s
`_run_vectorbt_trade_replay` now takes a `year_freq_days` request field
(default `252`), and `backtester_crosscheck._trade_replay_payload` sends
`252` explicitly. This is a real correction to make the comparison
apples-to-apples, not a tolerance fudge.

## Finding #2 (real, root-caused, left as a documented residual): entry/exit-day marking

Even after fixing the annualization basis, Sharpe still showed a small
residual gap (observed up to ~0.02 Sharpe units on a 4-trade sample).
Root-caused by comparing raw per-bar return series from both engines: our
backtester marks a trade's entry day's unrealized P&L using that day's
**real close** price, while the replay necessarily overrides that same day's
"price" with the **fill price** (the entry/exit price) to get the realized
P&L right - so the two engines value that one specific bar per trade
slightly differently. This is a structural artifact of replaying discrete
fills through a continuous-price engine, not a bug in either backtester, and
it shrinks as trade count grows relative to the sample. `AGREEMENT_TOLERANCE`
in `backtester_crosscheck.py` is therefore looser for `sharpe_ratio` (`0.03`)
than for `total_return_pct`/`max_drawdown_pct` (`0.01`) - those two are
path-independent sums of realized P&L and agree to floating-point rounding
(observed diffs <0.0001 percentage points across every strategy tested).

## Results (synthetic fixture, seed=42, 1-symbol, 1-year lookback)

| Strategy | Trades | Our return % | VBT return % | Our Sharpe | VBT Sharpe (252d) | Our max DD % | VBT max DD % | Agree? |
|---|---|---|---|---|---|---|---|---|
| Trend Following | 14 | 2.46 | 2.458 | 1.01 | 1.004 | -1.46 | -1.458 | Yes |
| Momentum Breakout | 4 | -2.03 | -2.030 | -1.08 | -1.060 | -2.98 | -2.979 | Yes |
| Mean Reversion | 5 | 1.24 | 1.242 | 0.49 | 0.488 | -2.28 | -2.281 | Yes |

All three agree within the documented tolerances. No in-sample performance
claim is made or implied by this table - it validates **arithmetic agreement
between two engines**, not that any of these strategies is profitable
out-of-sample.

## What this does and does not establish

- **Does establish**: `src/backtester.py`'s total-return, drawdown, and
  (once annualization is matched) Sharpe computations agree with an
  independently-written, widely-used backtesting library's statistics
  engine, given the same trades. This is real evidence against a class of
  bug (e.g. an off-by-one in the equity curve, a wrong annualization
  constant, a drawdown-sign error) in our own stats code.
- **Does not establish**: that any strategy is profitable, that VectorBT's
  signal-generation or parameter-sweep tooling has been adopted (it has not
  - signal generation remains entirely this project's own code), or anything
  about real-market performance (all data here is a synthetic fixture).

## Parameter sweeps (the other half of Phase 3's VectorBT ask)

Not yet built as of this document. `vbt.MA.run(...).ma_crossed_above/below()`
was verified runnable in the audit phase (`docs/platform/
OSS_INTEGRATION_AUDIT.md`) and the `vectorbt_benchmark` runner task already
wraps a generic MA-crossover sweep, but wiring a real parameter sweep over
this project's OWN strategies' tunable thresholds (not a generic MA
crossover) is follow-up work, tracked on the roadmap in the final sprint
report.
