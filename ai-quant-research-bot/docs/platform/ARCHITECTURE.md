# AI Quant Trading Platform — Architecture Map

This is the as-built map of the system as of the `claude/platform-integration-sprint1`
branch, written from a direct repository audit (not from memory of what was
*intended*). Every "EXISTING" claim below was verified by reading the actual
source file listed; every "NEW (this sprint)" item was added on this branch.
See `PROGRESS_CHECKLIST.md` for the deliverable-by-deliverable status and
`BLOCKERS.md` for what cannot be verified from this environment.

## 1. Repository layout

```
ai-quant-research-bot/
  src/                      the bot itself (research, ML, execution, Telegram)
  config/settings.yaml      the ONE config file - universe, strategies, risk,
                             execution mode, TradingAgents, scheduling
  data/                     journals, caches, logs (gitignored, never touched
                             by this sprint - see "Non-interference" below)
  tests/                    69 test files, pytest
  docs/platform/            <- this sprint's planning/architecture docs
  dashboard/
    backend/                FastAPI, read-only, WebSocket event stream
    frontend/                React + TypeScript + PixiJS
  deploy/                   NEW (this sprint) - Dockerfiles/compose, not deployed
  mac_launcher/              NEW (this sprint) - double-click launcher script
```

## 2. The bot's own architecture (EXISTING, audited this sprint)

### 2.1 Research pipeline (`src/main.py` orchestrates all of this, in order)

1. **Data ingestion** — `data_collector.py` (yfinance daily bars, cached to
   `data/raw/`), `data_providers/` (macro/FRED, SEC 13F/Form 4, options flow,
   market) — each returns a `ProviderResult` (`data_providers/base.py`) with an
   explicit `status` (`OK`/`UNAVAILABLE`/`ERROR`) and `available_at` point-in-
   time field — never a fabricated "neutral" value for missing data.
2. **Indicators + strategies** — `indicators.py`,
   `strategies/{trend_following,momentum_breakout,mean_reversion,skew_map}.py`.
3. **Signal scoring + regime** — `signal_scorer.py`, `market_regime.py`.
4. **Portfolio risk** — `portfolio_risk.py` (sector exposure caps, regime-aware
   sizing), `risk_manager.py`.
5. **Big Money / institutional context** — `big_money.py` +
   `data_providers/sec_provider.py` (13F/Form 4, point-in-time correct).
6. **Quant/ML layer** — `quant_agent.py`, `ml/{features,labels,predictor,
   model_registry,trainer,retrain_scheduler,calibration,drift,audit,splits,
   validator,weekly_report,model_events}.py`. This is a full offline-training +
   champion/challenger promotion system with drift detection, calibration, and
   chronological/walk-forward splitting that refuses look-ahead contamination
   — see `PROGRESS_CHECKLIST.md` section C for what this means for the
   "Continuous Learning Engine" deliverable (**already substantially built**,
   not a gap).
7. **Execution layer (Phase 7)** — see section 2.2.
8. **Multi-agent research (shadow mode)** — `intelligence/{analysts,debate,
   research_manager,risk_reviewer,reflection,pipeline,memory,schemas}.py`
   (deterministic, in-process multi-agent debate) PLUS
   `intelligence/tradingagents_adapter.py` (the real upstream
   TauricResearch/TradingAgents package, isolated in its own subprocess/venv,
   spend-capped, cached). Both are **shadow-mode only** — read `pipeline.py`'s
   module docstring for the hard invariant list; neither can influence
   `execution_decision`.
9. **Reporting** — `report_writer.py`, `telegram_bot.py`, Telegram approval
   buttons (`paper_trades.py`, `approval_listener.py`).

### 2.2 Execution layer (`src/execution/`) — the IBKR Paper stack

| File | What it actually does (audited) |
|---|---|
| `broker.py` | `Broker` Protocol + `FakeBroker` (deterministic mock for tests) |
| `ibkr_client.py` | Real `ibapi`-based client (lazy import — never required to run the rest of the suite); `verify_paper_account()` hard-fails closed if the account isn't PAPER |
| `order_state.py` | `OrderIntent` + `validate_intent()` — equity/ETF only by construction |
| `order_manager.py` | `OrderManager` (order lifecycle state machine) + `ExecutionJournal` (append-only JSONL — the restart-recovery source of truth) + `DuplicateIntentError` (idempotency) |
| `pretrade_checks.py` | Trading-hours validation, slippage/fresh-quote check, event-risk check |
| `circuit_breaker.py` | Daily/weekly loss limits, max drawdown, max open positions, max new trades/day, data-staleness, broker-disconnect, reconciliation-failure, excessive-rejections, and a manual kill switch — 11 independently-tripping breakers |
| `reconciliation.py` | Compares local open trades/orders against the broker's own reported state |
| `learning_feedback.py` | Reconciles actual fills/commissions back into the decision ledger |
| `approval_bridge.py` | Telegram-approved or auto-executed trade -> real `OrderManager` call |
| `position_monitor.py` | The continuous (`KeepAlive` launchd) loop: fills, protection sync, reconciliation, circuit breakers, restart recovery |
| `process_lock.py` | OS-level singleton lock (flock) — used by every long-running service |
| `run_health.py`, `market_calendar.py`, `after_close.py` | Daily Reliability & Safe Automation + NYSE-calendar-aware scheduling (prior sprint) |

**This is close to feature-complete for deliverable D's deterministic core.**
This sprint's job on D was to audit it against the user's exact required list
and fill genuine gaps, not rebuild it — see `PROGRESS_CHECKLIST.md`.

### 2.3 Dashboard (`dashboard/`, prior sprint's Phase 1 MVP)

- `dashboard/backend/app/readonly.py` is the ONLY integration seam into the
  bot's code, structurally restricted (by `tests/test_readonly_safety.py`) to
  an allow-list of read-only `src.*` modules. No write path exists anywhere in
  the backend.
- `dashboard/backend/app/events.py` defines the `AgentEvent` schema; three
  modes (LIVE/REPLAY/DEMO) stream it over one `/ws` endpoint.
- `dashboard/frontend/` — React + TS + PixiJS scene with 8 agents on fixed
  desks, tweening to an event's target zone and flashing.

## 3. What this sprint adds (see each file's own docstring for detail)

- `docs/platform/*.md` — this plan.
- `src/universe.py` — configurable, screened symbol universe (deliverable A).
- `src/data_providers/forex_provider.py`, `crypto_provider.py` — extensibility
  interface stubs (deliverable A), honestly `UNAVAILABLE` by default.
- `src/intelligence/knowledge_library.py` — offline RAG retrieval over
  user-supplied legal documents (deliverable E).
- `dashboard/frontend/src/pixi/*` — procedural walk-cycle characters, office
  backdrop, speech bubbles (deliverable F).
- `dashboard/backend/app/` + frontend panels — positions/orders/fills,
  risk/drawdown, backtest performance, challenger experiments, market scanner
  (deliverable G).
- `deploy/` — Dockerfiles + compose, not deployed (deliverable H).
- `mac_launcher/` — double-click launcher (deliverable I).

## 4. Non-interference guarantee

Nothing in this sprint touches `config/settings.yaml`'s `execution.mode`,
`autonomous_paper.enabled`, `auto_execute.enabled`, or
`intelligence.tradingagents.enabled` defaults; nothing modifies the real
`~/Library/LaunchAgents/com.aiquantresearchbot.*.plist` files (those are
Mac-local, outside this repo); nothing reads `.env`/secrets outside the
existing, already-audited `redact_secrets()`-protected paths. Every new
module that touches bot data is read-only, verified the same way the
dashboard's `readonly.py` already is — by a structural test, not by
convention.
