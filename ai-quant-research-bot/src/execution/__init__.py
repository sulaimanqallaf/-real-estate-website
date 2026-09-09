"""Autonomous IBKR PAPER Trading Execution Layer (Phase 7).

**This package is PAPER-ONLY.** There is no code path anywhere in this
package that can connect to, or place an order against, a live IBKR
account - see `ibkr_client.verify_paper_account()` for the enforcement
point, and README "Quant / ML Intelligence Layer... Execution Layer" for
the full design and every hard constraint.

Modules:
- `broker.py`: the `Broker` Protocol every implementation satisfies, plus
  `FakeBroker` (used throughout this package's test suite - no real IBKR
  connection is ever exercised by an automated test).
- `ibkr_client.py`: the real `Broker` implementation over the official
  `ibapi` TWS API, imported lazily so nothing else in this codebase ever
  requires it to be installed.
- `order_state.py`: the immutable `OrderIntent` + its validation, and the
  order lifecycle state constants.
- `order_manager.py`: submission, fill-aware bracket protection (entry
  first, exits attached only after a confirmed fill), partial-fill
  handling, idempotency, and the append-only `ExecutionJournal`.
- `execution_policy.py`: classifies an already-fully-gated candidate into
  AUTO_EXECUTE / REQUIRE_APPROVAL / WATCH_ONLY / REJECT - both
  `autonomous_paper.enabled` and `auto_execute.enabled` default to `false`.
- `circuit_breaker.py`: the account-level Risk Governor, kill switches, and
  the persistent manual halt file.
- `reconciliation.py`: compares local journal state against the broker's
  own positions/orders/executions; any material discrepancy blocks new
  entries until resolved.
- `pretrade_checks.py`: trading-hours, event-risk, and slippage checks run
  immediately before any submission.
- `approval_bridge.py`: re-checks every gate that can legitimately have
  gone stale between a Telegram approval and execution time, then routes
  into `order_manager.py`; also the AUTO_EXECUTE path used when both
  `autonomous_paper.enabled` and `auto_execute.enabled` are true.
- `telegram_commands.py`: the `/status`, `/positions`, `/orders`,
  `/performance`, `/halt`, `/resume` command center, wired into
  `approval_listener.py`.
- `learning_feedback.py`: feeds a broker-paper trade's ACTUAL fill/exit/
  commission into `paper_trades.csv` the moment its stop or target leg
  fills - never a bar-simulation guess, and never triggers ML retraining.
- `position_monitor.py`: the separate, long-running monitoring process
  (`python -m src.execution.position_monitor`) - `src.main` stays a
  once-a-day batch job and never loops forever.
"""
