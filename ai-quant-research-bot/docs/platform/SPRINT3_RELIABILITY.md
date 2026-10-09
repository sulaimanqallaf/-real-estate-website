# Sprint 3: Reliability milestone — findings and tooling decisions

Branch: `claude/sprint3-reliability-real-data`. This doc covers Tasks R1-R5
(retry/backoff, Hypothesis property tests, crash-recovery watchdog,
fault-injection tests, fail-closed audit + alert gaps) and is extended as
each task completes; the final sprint report consolidates this with
Milestones 2-5.

## Tool evaluation: Tenacity, Hypothesis, Toxiproxy

All three were evaluated for real, not assumed to be useful:

- **Tenacity** (`src/reliability.py`) - adopted. A shared retry/backoff
  decorator wired into every real direct-HTTP call site (yfinance, SEC,
  FRED). Retries only transient failures (connection errors, timeouts,
  5xx), reraising the original exception type on exhaustion so every
  existing error-handling path downstream is unchanged.
- **Hypothesis** (`tests/test_hypothesis_safety_invariants.py`) - adopted.
  Fuzzes `order_state.validate_intent()`, `risk_manager._size_position()`,
  and `circuit_breaker`'s threshold checks - the functions standing
  directly between a decision and a real broker order - across hundreds
  of generated adversarial inputs per run, not just hand-picked examples.
- **Toxiproxy** - evaluated directly, not just discussed. Confirmed the
  real `toxiproxy-server` binary downloads successfully from GitHub
  releases in this sandbox (financial-data vendor hosts are blocked by
  organization policy; GitHub release assets are not) and genuinely
  produces the exact `requests.exceptions.ConnectionError` our retry
  logic is written to catch when a `reset_peer` toxic is injected against
  a real local HTTP server proxied through it - verified with a manual
  curl/requests round-trip before writing any test. Used for exactly the
  two scenarios where a REAL broken TCP connection matters (
  `tests/test_fault_injection_network.py`: retry recovers once a real
  reset clears mid-retry-loop; retry exhausts and reraises when it
  doesn't) - optional (`scripts/setup_toxiproxy.sh`, downloads to
  `.bin/`, gitignored), skipping cleanly wherever the binary isn't
  installed, since it's an external Go binary, not a PyPI package.
  **Not used** for the other fault-injection scenarios (stale data,
  broker disconnects, duplicate orders, DB errors) because none of
  those involve a TCP connection at all - they are pure in-process
  state-machine edge cases, where a fake/mock is the correct tool, not a
  lesser-effort shortcut.

## Real findings fixed (not just documented)

1. **`decision_ledger.record_outcome()`'s "never raises" contract was
   false.** The docstring explicitly promised it; the code had no
   try/except at all, so a real `sqlite3.OperationalError` ("database is
   locked") would have propagated straight out. The one real call site
   (`learning_feedback.check_exit_fills()`) already wraps it in
   `safe_run()`, so production behavior was never actually unsafe - but
   the function didn't honor its own contract in isolation, a latent
   risk for any future direct caller. Fixed: wrapped in
   `except sqlite3.Error: return False` (the same `False` already used
   for "no matching row" - callers already treat both identically).
   Verified with a REAL transient lock (a second real SQLite connection
   holding an exclusive lock for 0.3s) recovering correctly, and a
   mocked persistent failure returning `False` rather than raising.
2. **`position_monitor.run_one_tick()`'s "never raises" contract had the
   same gap, one level up.** Its top-of-tick `connection_state()` check
   only proves the broker was reachable AT THAT MOMENT -
   `reconciliation.reconcile()` (which itself calls
   `broker.positions()`/`open_orders()`) and a second, separate
   `broker.positions()` call later in the same tick were both
   unguarded. A disconnect landing between the top check and either of
   those calls would have propagated out of `run_one_tick()` and
   crashed the whole process - not unsafe (launchd's `KeepAlive` would
   restart it, and `restore_from_journal_rows()` would recover state),
   but far more disruptive than necessary for what may be a momentary
   blip. Fixed: both calls are now wrapped in `safe_run()`; a failure
   is reported as a real `RECONCILIATION_CHECK_FAILED` discrepancy
   (new `reconciliation.DISCREPANCY_CHECK_FAILED`), which fails closed
   through the EXISTING `circuit_breaker.check_reconciliation()` path
   rather than crashing. Verified with a real mid-tick `FakeBroker`
   failure injected between the top check and `reconcile()`.

## Already-covered fault-injection scenarios (verified, not re-built)

Several of this milestone's scenarios already had thorough, real
coverage from earlier sprints - re-verified rather than duplicated:

- **Stale data**: `tests/test_execution_run_health.py` already covers
  the exact real DST-transition regression (mixed `-04:00`/`-05:00`
  offsets), unparseable timestamps, date-only formats, and genuinely
  stale data, each correctly sorted into `stale`/`check_failed`/`ok`.
- **Duplicate order submission**: `tests/test_execution_order_manager.py`
  and `tests/test_execution_synthetic_scenarios.py` already cover
  same-trade-id resubmission, matching-ticker/strategy/entry/stop
  resubmission without a trade_id, and the exact crash-then-restart
  resubmission scenario via `restore_from_journal_rows()`.
- **Process-level duplicate prevention**: `process_lock.py`'s singleton
  lock already has coverage across `test_approval_listener.py`,
  `test_execution_reconciliation_and_monitor.py`, and
  `test_main_scheduler_lock.py`.
