# Platform Progress Checklist

Status legend: `[x]` done and tested · `[~]` partially done / needs work ·
`[ ]` not started · `[blocked]` cannot be completed from this environment
(see `BLOCKERS.md`). Updated as each milestone lands on
`claude/platform-integration-sprint1`. This file is the single source of
truth for "what's actually done" — update it in the SAME commit as the code
it describes, never after the fact from memory.

## A. Market Data Platform

- [x] US equity/ETF daily OHLCV ingestion, incremental caching, retries via
  `safe_run()`, per-symbol failure isolation — `data_collector.py` (existing).
- [x] Exchange calendar (holidays, early closes) — `execution/market_calendar.py`
  (existing, prior sprint, `pandas_market_calendars`-backed).
- [x] Data provenance/freshness/timestamps — `ProviderResult.available_at`/
  `freshness` (existing, `data_providers/base.py`), `data_collector.bar_freshness()`
  (existing, prior sprint — the DST-bug-fixed version).
- [x] Provider abstraction ready for a licensed live feed — `data_providers/base.py`'s
  `Provider` protocol was already generic; **this sprint** adds the
  forex/crypto extension stubs on the same contract (see below).
- [x] **This sprint**: configurable, screened symbol universe
  (`src/universe.py`) — expandable from 15 to 100+ symbols via
  `config.universe`, liquidity/price screening, delisted-symbol safeguard.
- [x] **This sprint**: forex/crypto provider interface stubs
  (`data_providers/{forex,crypto}_provider.py`) — prove the extension point
  exists; both honestly return `STATUS_UNAVAILABLE` (no license/feed).
- [blocked] Real licensed live-quote feed — needs a paid data vendor account
  and API key; see `BLOCKERS.md`.
- [blocked] Real forex/crypto feed — same reason.
- [ ] Corporate-action (splits/dividends) auto-adjustment pipeline beyond
  yfinance's own `auto_adjust` — not implemented; documented as a known gap,
  not attempted this sprint (needs a real corporate-actions data source to do
  correctly, not just a TODO stub).

## B. AI Strategy Lab

- [x] Trend following, momentum breakout, mean reversion (incl. aggressive
  mode), VWAP/skew-map strategies — `strategies/*.py` (existing).
- [x] Regime-based selection — `market_regime.py` + `portfolio_risk.py`'s
  regime-aware sizing (existing).
- [x] Realistic commissions/slippage/position sizing in backtests —
  `backtester.py` (existing — audited this sprint, confirmed real, not a
  stub: `_run_strategy_backtest()` applies per-trade commission and slippage
  before computing P&L).
- [x] Walk-forward / chronological out-of-sample splitting, no-look-ahead —
  `ml/splits.py` (`chronological_split()`, `walk_forward_folds()`, existing).
- [x] Training/test contamination prevention — `ml/audit.py` (existing,
  flags class imbalance / constant columns / leakage-shaped features).
- [~] Survivorship-bias handling — delisted symbols ARE now safeguarded
  against in the universe loader (this sprint), but historical survivorship
  bias in yfinance's own dataset (it doesn't serve truly delisted tickers) is
  a data-source limitation, not something code can fully correct — documented
  in `universe.py`'s docstring, not silently ignored.

## C. Continuous Learning Engine

**Audit finding: this deliverable was already ~85% built before this
sprint** (built across this same long-running session's earlier phases, not
newly discovered — recorded here for an accurate platform-wide picture):

- [x] Structured decision ledger (predictions, features-at-decision-time,
  strategy/regime/model_id, outcome once known) — `ml/decision_ledger.py`
  (existing, SQLite, queryable).
- [x] Agent recommendations/disagreements — `intelligence/memory.py` +
  `tradingagents_adapter`'s own cache (existing).
- [x] Paper orders/fills/P&L — `order_manager.ExecutionJournal`,
  `paper_trades.csv`, `paper_trade_tracker.py` (existing).
- [x] Rejected trades / no-trade decisions — `decision_ledger` rows with
  `decision != AUTO_EXECUTE` and `outcome_status IS NULL` (existing — see
  that module's own docstring for why the absence IS the signal).
- [x] Calibration — `ml/calibration.py` (`fit_calibration`/`apply_calibration`,
  existing).
- [x] Drift monitoring — `ml/drift.py` (feature/prediction/calibration/
  hit-rate/missing-data drift, existing).
- [x] Champion/challenger evaluation + promotion gate — `ml/model_registry.py`
  (`decide_promotion()`, existing) + `ml/retrain_scheduler.py` (existing,
  weekly, never auto-promotes past the gate).
- [x] Hard invariant already enforced: retraining only ever produces a
  CHALLENGER; promotion requires `decide_promotion()`'s gate; nothing in this
  codebase can loosen a risk limit or deploy an unreviewed model — confirmed
  by reading `retrain_scheduler.py` and `model_registry.py` end to end this
  sprint, not assumed.
- [ ] Performance ATTRIBUTION report (by strategy × regime × ticker, beyond
  `strategy_memory.py`'s existing regime-conditioned edge tracking) — a
  genuine small gap; not attempted this sprint (lower priority than the
  deliverables with zero prior coverage).

## D. IBKR Paper Execution

**Audit finding: also already ~90% built.** See `ARCHITECTURE.md` section
2.2 for the file-by-file audit. Checklist against the user's exact required
list:

- [x] IBKR Paper account verification — `ibkr_client.verify_paper_account()`.
- [x] Explicit PAPER environment checks — same + `AccountModeError`.
- [x] Order idempotency — `order_manager.DuplicateIntentError`.
- [x] Position/open-order reconciliation — `reconciliation.py`.
- [x] Partial fills and rejected orders — `order_manager.ManagedOrder` fill
  tracking + `BrokerOrderRejected`.
- [x] Trading-hours validation — `pretrade_checks.check_trading_hours()`.
- [x] Fresh-quote requirement — `pretrade_checks.check_slippage()`.
- [x] Max position/exposure limits — `portfolio_risk.py` + circuit breaker's
  `max_open_positions`.
- [x] Daily loss and drawdown gates — `circuit_breaker.py`'s
  `BREAKER_DAILY_LOSS_LIMIT`/`BREAKER_WEEKLY_LOSS_LIMIT`/`BREAKER_MAX_DRAWDOWN`.
- [x] Restart recovery — `ExecutionJournal` replay + `position_monitor.py`.
- [x] Emergency kill switch — `circuit_breaker.halt()`/`resume()` (manual,
  independent of reconciliation-failure state — confirmed this sprint that
  `resume()` cannot clear a reconciliation failure, by design).
- [x] Auditable order state machine — `order_manager.ManagedOrder` + journal.
- [x] **This sprint**: a written "unattended paper-mode readiness" audit
  (`docs/platform/UNATTENDED_PAPER_READINESS.md`) — cross-checks every item
  above against the user's list explicitly and names the ONE thing that is
  NOT code-completable from this sandbox (a real IBKR TWS/Gateway paper
  session) rather than leaving that gap implicit.
- [blocked] Verifying any of the above against a REAL IBKR TWS/Gateway
  session — no broker session is reachable from this sandbox; `FakeBroker`
  tests prove the logic, not the real connection. See `BLOCKERS.md`.

## E. AI Agents

- [x] 8-agent architecture with real backend events — `intelligence/*.py` +
  `dashboard/` (existing).
- [x] Deterministic execution/safety logic fully independent of LLM opinions
  — confirmed via the existing `test_intelligence_tradingagents_safety.py`
  guardrail (grep/AST-based, existing).
- [x] **This sprint**: RAG research knowledge library
  (`intelligence/knowledge_library.py`) — offline BM25-style retrieval over
  user-supplied legal documents, citations, untrusted-content isolation
  (retrieved text is always a data field, never parsed as instructions).
- [blocked] Actually indexing real copyrighted books/papers — the user must
  supply files they have the legal right to use; this sprint ships the
  ingestion/retrieval engine and a synthetic fixture corpus for tests, not
  any real copyrighted content.

## F. Animated AI Office

- [x] Phase 1 MVP: 8 agents, zones, event-driven movement (existing).
- [x] **This sprint**: procedural walk-cycle animation (leg/arm swing while
  moving, idle bob while still), drawn desks/monitors/meeting-room/trading-
  floor backdrop, speech bubbles on event, debate-room visual distinction.
- [ ] True illustrated/sprite-sheet character art — no art assets are
  available in this environment; the procedural upgrade above is the honest
  ceiling without a human artist or a licensed asset pack. Documented as a
  known limitation, not glossed over.

## G. Professional Command Center

- [x] Health/run-status/spend/paper-P&L panels — Phase 1 MVP (existing).
- [x] **This sprint**: positions/open-orders/fills/trade-history panel.
- [x] **This sprint**: risk/drawdown panel (circuit breaker state + limits).
- [x] **This sprint**: strategy backtest performance panel.
- [x] **This sprint**: learning/challenger experiments panel.
- [x] **This sprint**: market scanner panel (universe + latest scores).
- [x] No secrets in the browser — extends the existing structural guardrail
  test to every new endpoint.

## H. 24/7 Deployment

- [x] **This sprint**: Dockerfiles for dashboard backend/frontend +
  `docker-compose.yml`, `docs/platform/DEPLOYMENT.md` (process supervision,
  secrets, health checks, logging, single-active-executor guarantee, broker
  reconnect strategy).
- [ ] Containerizing the bot's OWN scheduled jobs (main.py/after_close.py/
  position_monitor.py) — documented as a plan in `DEPLOYMENT.md`, not
  containerized this sprint (the user's existing Mac launchd setup is working
  and explicitly must not be disrupted; containerizing it is a separable,
  reviewable step for when cloud deployment is actually authorized).
- [blocked] Any actual cloud deployment — explicitly not authorized by the
  user this sprint; nothing was deployed anywhere.

## I. No-Terminal User Experience

- [x] **This sprint**: `mac_launcher/AI Quant Dashboard.command` — double-
  click launcher, dependency check, start/stop dashboard services, opens
  browser, local status page, read-only launchd status check.
- [blocked] Signed/notarized `.app` bundle — no Apple Developer signing
  identity is available in this environment; the `.command` launcher is the
  honest unsigned alternative, documented as such (macOS Gatekeeper will warn
  on first run — the launcher's own README explains the one-time
  right-click-Open workaround).
- [ ] Mobile/remote browser interface — out of scope for this sprint (needs
  the H deployment + real auth, which needs cloud authorization first).
