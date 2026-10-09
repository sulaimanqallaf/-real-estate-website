# Unattended IBKR PAPER Execution — Readiness Audit

Deliverable D's full required checklist, audited against the actual
source this sprint (file + function named for every claim — nothing here
is asserted from memory). Run `python -m src.execution.unattended_readiness`
any time for a live, read-only pre-flight summary of the checks that ARE
machine-checkable from outside a real broker session.

| Requirement | Implementation | Verified how |
|---|---|---|
| IBKR Paper account verification | `execution/ibkr_client.py:verify_paper_account()` | Read function body; raises `AccountModeError` if the reported account isn't PAPER |
| Explicit PAPER environment checks | Same + `classify_account_id()` | Read function body |
| Order idempotency | `execution/order_manager.py:DuplicateIntentError` | Read `OrderManager`'s intent-id dedup path |
| Position/open-order reconciliation | `execution/reconciliation.py:reconcile()` | Read function; compares broker vs local state, returns a `ReconciliationReport` |
| Partial fills and rejected orders | `order_manager.ManagedOrder` fill tracking + `broker.BrokerOrderRejected` | Read dataclass fields and the fill-accumulation path |
| Trading-hours validation | `pretrade_checks.py:check_trading_hours()`/`is_within_trading_hours()` | Read function body |
| Fresh quote requirement | `pretrade_checks.py:check_slippage()` | Compares intent price against a freshly-fetched current price before allowing entry |
| Max position/exposure limits | `circuit_breaker.py`'s `BREAKER_MAX_OPEN_POSITIONS` + `portfolio_risk.py` sector caps | Read `DEFAULT_RISK_LIMITS` and `effective_execution_risk_limits()` |
| Daily loss and drawdown gates | `circuit_breaker.py`'s `BREAKER_DAILY_LOSS_LIMIT`/`BREAKER_WEEKLY_LOSS_LIMIT`/`BREAKER_MAX_DRAWDOWN` | Read `check_daily_loss()`/`check_weekly_loss()`/`check_drawdown()` |
| Restart recovery | `order_manager.ExecutionJournal` (append-only JSONL replay) + `position_monitor.py`'s startup rebuild | Read `OrderManager.__init__`'s journal-replay path and `position_monitor.run_forever()`'s startup comment block |
| Emergency kill switch | `circuit_breaker.halt()`/`resume()`, reachable via Telegram `/halt`/`/resume` (`execution/telegram_commands.py`) | Read both modules; confirmed `/resume` cannot clear a reconciliation failure (by design, per that module's own docstring) |
| Auditable order state machine | `order_manager.ManagedOrder` + journal rows | Read the state transitions the dataclass models |

**Verdict: deliverable D's deterministic, broker-agnostic core was
already complete before this sprint.** This sprint's actual contribution
to D is `src/execution/unattended_readiness.py` — a single, read-only
pre-flight summary tool that composes the checks above into one
human-readable report, specifically for the moment before flipping
`autonomous_paper.enabled`/`auto_execute.enabled` to `true`, or for a
periodic sanity check afterward. It adds no new enforcement — every
check it reports already runs for real on every trading decision.

## What this audit does NOT and cannot verify

Everything above was verified by reading the code and by the existing
`FakeBroker`-based test suite (`tests/test_execution_*.py`, 69 files).
None of it was verified against a REAL IBKR TWS/Gateway session, because
no such session is reachable from this sandbox. Concretely, still
unverified in the real world:

- Real connection/reconnect timing and error codes from `ibapi` itself.
- Real fill latency and partial-fill sequencing under real market
  conditions.
- Real commission reporting format from a live paper account.
- Whether `verify_paper_account()`'s account-ID heuristic
  (`classify_account_id()`) correctly classifies YOUR specific paper
  account ID format — IBKR account ID conventions can vary.

**Action for the user**: run `python -m src.execution.unattended_readiness`
on your Mac with `execution.mode: IBKR_PAPER` and TWS/Gateway running in
Paper mode, then exercise the existing documented rollout sequence (main
README's "Rollout" section, unchanged by this sprint) before trusting any
of the above against real fills.
