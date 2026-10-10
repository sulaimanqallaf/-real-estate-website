# Sprint 3: Reliability & Real Market Validation — Final Report

**Branch**: `claude/sprint3-reliability-real-data`
**Final commit**: `837887d06c5a54276b0cb76c0cd20462b1918aed`
**Base**: `claude/oss-quant-integration-sprint2` (preserved intact — this
branch only ever added commits on top of it)
**13 commits**, all five requested milestones complete.

This report answers exactly what the sprint kickoff asked for at
completion: *"the branch, final commit SHA, test results, real-data
validation evidence, remaining blockers and the minimum steps I
personally need to complete."* It consolidates
`docs/platform/SPRINT3_RELIABILITY.md` (Milestone 1's own detailed
tooling-decision doc) rather than repeating it.

## 1. What was built, milestone by milestone

### Milestone 1 — Reliability (R1-R5)
Tenacity retry/backoff on every outbound HTTP call (yfinance, SEC,
FRED, Alpaca); Hypothesis property-based fuzzing of the three
functions standing directly between a decision and a real broker order;
a crash-recovery watchdog heartbeat for `position_monitor.py`
(`python -m src.execution.watchdog`); real fault-injection tests
(Toxiproxy for genuine TCP resets, fakes/mocks for in-process
state-machine scenarios — stale data, disconnects, duplicate orders, DB
errors); a fail-closed audit that found and fixed three real "never
raises" contract violations (`decision_ledger.record_outcome`,
`position_monitor.run_one_tick`'s reconciliation path,
`circuit_breaker.is_halted`) plus a missing Telegram alert on
broker reconnect. Full detail: `docs/platform/SPRINT3_RELIABILITY.md`.

### Milestone 2 — Free Real Market Data (D1-D3)
- **D1**: `src/data_providers/alpaca_provider.py` — a real Alpaca Basic
  (free) plan integration, IEX feed hardcoded (`feed=sip` is not
  reachable from any code path in this module), REST contract verified
  against `alpaca-py`'s own SDK source (Alpaca's docs site is blocked
  in this sandbox), a `RateLimiter` enforcing the real 200 req/min
  ceiling.
- **D2**: `src/data_providers/alpaca_scanner.py` — batches that single
  provider call up to the 500-1,000 symbol/ETF target, one shared rate
  limiter across the whole run, caches to `data/raw_iex/`, a directory
  and naming convention deliberately separate from the yfinance cache.
- **D3**: `src/data_providers/iex_validation.py` — schema/OHLC/
  spacing/cross-timeframe (1m vs 5m vs 15m) validation logic, plus
  explicit tests and documentation of the IEX-vs-consolidated
  separation.

### Milestone 3 — Real Strategy Validation (V1-V3)
- **V1**: realistic transaction costs (IBKR-shaped commission) and
  adverse slippage, baked into `src/backtester.py`'s fill prices and a
  new `Trade.commission` field — backward-compatible by construction
  (a config without `transaction_costs` gets exactly zero cost).
- **V2**: `src/analytics/walk_forward.py` — splits real history into
  non-overlapping folds and re-runs the same production backtest
  engine on each independently, honestly scoped (these strategies have
  no fitted parameters, so this validates *consistency*, not
  "optimization that didn't overfit").
- **V3**: `src/analytics/strategy_validation_report.py` — consolidates
  V1+V2 into one report per strategy with a rule-based **evidence
  verdict** (`INSUFFICIENT_EVIDENCE` / `INCONSISTENT_ACROSS_FOLDS` /
  `CONSISTENT_IN_BACKTEST`, every threshold named at module level) —
  the direct implementation of "do not claim profitability without
  sufficient evidence."

### Milestone 4 — IBKR Paper Readiness (I1)
`src/execution/connectivity_preflight.py` — one guided command running
four checks in order: paper-account identity, (interactive) reconnect
handling, order reconciliation, and the manual kill switch. Never
calls `submit_order`/`cancel_order`/`replace_order`, never touches
`autonomous_paper.enabled`/`auto_execute.enabled` (both stay `false`).

### Milestone 5 — Automation and Dashboard (A1)
Closed a real, found-not-assumed gap: the dashboard backend already
carried `position_monitor_heartbeat` (from Milestone 1) but the
frontend never rendered it. `HealthPanel.tsx` now shows heartbeat
status, yfinance cache staleness, and a new Alpaca/IEX cache-freshness
check (`alpaca_scanner.iex_cache_freshness_report()`). The Mac
launcher's `Dashboard Status.command` now runs
`python -m src.execution.run_health` itself and prints the full
reliability summary — no Terminal typing required. The existing Mac
`launchd` research scheduler was never touched (read-only
`launchctl list` check only, as before).

## 2. Test results

| Point in the sprint | Full suite result |
|---|---|
| Start of Sprint 3 (before any change) | 1264 passed, 1 skipped |
| End of Sprint 3 (this report, HEAD `837887d`) | **1395 passed, 1 skipped, 0 failures** |

**131 net new/changed tests**, verified passing after *every single
commit* in this sprint, not just once at the end — each commit message
above records its own sub-total. 13 new test files; 7 existing files
extended. Dashboard backend: 47 passed (same `pytest` run). Dashboard
frontend: 21 passed (`vitest`), plus a clean `tsc -b` type-check — both
re-verified for this report.

## 3. Real-data validation evidence — read this section literally

**This sandbox's egress proxy blocks every financial-data-vendor host
this project touches** — confirmed directly with `curl`, not assumed:
`data.alpaca.markets` (403), `query1.finance.yahoo.com` (403),
`docs.alpaca.markets` (EGRESS_BLOCKED), plus the crypto-exchange hosts
blocked in earlier sprints. This is a hard environment limitation, not
a code gap, and it means:

- **No strategy in this codebase has been run against real market
  data from this session.** Every backtest/walk-forward/validation-
  report test (`tests/test_backtester_transaction_costs.py`,
  `tests/test_walk_forward.py`, `tests/test_strategy_validation_report.py`)
  exercises the REAL strategy/indicator/risk_manager/cost-model logic
  end-to-end — that logic is genuinely correct and tested — but only
  ever against `src/analytics/synthetic_fixtures.py`'s deterministic
  synthetic price series, never real history.
- **No Alpaca/IEX bar has been validated against the real feed either**
  — `src/data_providers/iex_validation.py`'s 28 tests prove the
  validator catches bad data correctly, on hand-built fixtures, not on
  a real Alpaca response.
- Nothing in this report claims otherwise. `docs/platform/BLOCKERS.md`
  items 8 and 9 (new this sprint) spell out exactly what each command
  will print once you run it with real network access, and the
  **evidence-gated verdict in V3's report is designed for exactly this
  moment** — it will not say `CONSISTENT_IN_BACKTEST` unless the real
  evidence genuinely clears its named thresholds.

This is the honest state of "real-data validation" at the end of this
sprint: the validation MACHINERY is real, tested, and ready; the
actual real-data RESULT does not exist yet because this sandbox cannot
produce it.

## 4. Remaining blockers

All nine items in `docs/platform/BLOCKERS.md`, in the order they'd most
likely unblock further work:

1. Real IBKR TWS/Gateway paper session — run
   `python -m src.execution.connectivity_preflight` (new this sprint).
2. Licensed live/delayed market data feed (unchanged from prior
   sprints — Alpaca/IEX this sprint is free/delayed-appropriate, not a
   substitute for a paid real-time feed).
3. Forex feed/broker support; crypto broker support (unchanged).
4. Real copyrighted research for the RAG knowledge library (unchanged).
5. Apple Developer signing identity for the Mac launcher (unchanged).
6. Cloud account for 24/7 deployment (unchanged — nothing deployed
   this sprint, per your explicit instruction).
7. Your own $500 real-money authorization (unchanged — nothing in this
   sprint moves this forward or needs it).
8. **New**: real-data validation of the Alpaca/IEX feed (Task D3).
9. **New**: a real-data run of the backtest/walk-forward/validation
   report (Tasks V1-V3).

Nothing was purchased, no cloud resource was deployed, no credential
was exposed, your running Mac bot was never touched, and real-money
trading remains impossible by construction (`VALID_EXECUTION_MODES`
has no LIVE value) — every one of your explicit constraints held for
all 13 commits.

## 5. Minimum steps you personally need to complete

On a machine with real outbound network access (your Mac is sufficient
— none of this needs this sandbox):

1. **See real strategy evidence**: run
   `python -m src.analytics.strategy_validation_report` (no credentials
   needed — yfinance is free). Read its verdict per strategy literally.
2. **Get free real intraday data flowing**: sign up for Alpaca's free
   Basic plan (no card required), set `ALPACA_API_KEY_ID`/
   `ALPACA_API_SECRET_KEY` in `.env`, then run
   `python -m src.data_providers.iex_validation` to see whether the
   real feed passes its own schema/cross-timeframe checks.
3. **Verify broker connectivity for real**: start TWS/IB Gateway in
   Paper mode, then run
   `python -m src.execution.connectivity_preflight` (do the manual
   reconnect step — stop/restart TWS when it asks — to actually
   exercise that check rather than getting an honest `SKIPPED`).
4. **Nothing else is required to review this sprint** — `Dashboard
   Status.command` now shows you the full reliability picture (and a
   new Alpaca/IEX freshness line) without opening Terminal at all.

Autonomous order submission remains OFF
(`autonomous_paper.enabled`/`auto_execute.enabled` are still `false` in
`config/settings.yaml`) and stays that way until you explicitly decide
otherwise, having reviewed the evidence step 1-3 above produce for
real.
