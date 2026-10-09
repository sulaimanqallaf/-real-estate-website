# Phase 5: LumiBot + LEAN IBKR connectivity — reference-only review

Per the sprint instruction: *"examine LumiBot and LEAN as IBKR Paper
connectivity references; do NOT replace the existing order manager without
proof of better correctness/safety; validate reconnection/partial fills/
duplicate-order prevention/recovery in OFFLINE tests only; do NOT enable any
paper or live order submission."*

**No LumiBot or LEAN code is used, copied, or installed anywhere in this
project.** LumiBot is GPL-3.0 (copyleft risk, same reasoning as
`docs/platform/OSS_INTEGRATION_AUDIT.md`'s verdict on it); LEAN's IBKR
brokerage is C#, a different runtime entirely. Both were read directly from
their real public source (fetched from GitHub) purely to inform this
comparison — a "clean room" read for architecture ideas, not a source for
code.

## Method

Fetched the real source of:
- `lumibot/brokers/interactive_brokers.py` (Lumiwealth/lumibot, master)
- `QuantConnect.InteractiveBrokersBrokerage/InteractiveBrokersBrokerage.cs`
  (QuantConnect/Lean.Brokerages.InteractiveBrokers, master — LEAN split its
  IB brokerage into its own repo; the main `Lean` repo no longer has it)

and read each for exactly five things: connection-loss detection,
reconnection logic, duplicate-order prevention, partial-fill handling, and
state recovery after a reconnect — then compared each against this
project's own `src/execution/ibkr_client.py`, `reconciliation.py`,
`circuit_breaker.py`, and `process_lock.py`.

## What LumiBot actually does (weaker than this project in every category below)

- **Connection loss**: no `connectionClosed`-equivalent callback at all.
  Liveness is checked lazily via `self.ib.isConnected()`, and only from
  `_get_balances_at_broker` — order submission and streaming never check it.
- **Reconnection**: `_reconnect_if_not_connected` rebuilds the client object
  once (`self.ib = None` then `start_ib()`), sleeps a fixed 5 seconds, and
  stops — no retry loop, no backoff.
- **Duplicate-order prevention**: order ids come from a simple incrementing
  counter; nothing stops the SAME logical order from being submitted twice.
  `on_status_event`'s own duplicate-status list "grows without bound."
- **Partial fills**: handled reasonably — `on_trade_event` compares
  cumulative filled quantity to the order's total and classifies
  `PARTIALLY_FILLED`/`FILLED` accordingly.
- **Recovery after reconnect**: none. Open orders/positions are not
  re-fetched after the client is rebuilt; `nextOrderId()` busy-waits with no
  timeout, which "could block indefinitely after a reconnect."

## What LEAN actually does (meaningfully more mature than this project in one specific area)

- **Connection loss**: subscribes to `_client.ConnectionClosed` (the real
  `connectionClosed` event) AND runs an active `RunHeartBeatThread()` that
  periodically calls `reqCurrentTime()` — catching a dead-but-not-cleanly-
  closed socket that a passive callback alone would miss.
- **Reconnection**: `Connect()` retries up to 7 times with a 15-second wait
  between attempts (bounded, not infinite); a failed heartbeat triggers
  `Disconnect()` then `Connect()` again.
- **Duplicate-order prevention**: no explicit idempotency key either —
  relies on sequential order ids (`GetNextId()`/`_nextValidId`) and
  `PlaceOrder` returning `false` when disconnected (fails closed on
  submission, same spirit as this project's `_require_connected()` guards).
  Not meaningfully stronger than this project here.
- **Partial fills**: real logic for combo/bracket orders specifically —
  `_pendingGroupOrdersForFilling` holds fills until a bracket group is ready,
  with a 30-second timeout monitor; executions are matched to commission
  reports by execution id (the same idea this project's
  `executions()`/`commissionReport()` dedup-by-`execId` already does).
- **Recovery after reconnect**: re-downloads account data
  (`DownloadAccount()`), re-fetches open orders (`GetOpenOrdersInternal`),
  and explicitly **rebuilds OCA/bracket relationships**
  (`SetContingencies(...)`) — the one thing this project does NOT redo after
  a reconnect (see "Scope not changed" below for why that's lower-priority
  here specifically).

## The real gap found and fixed

**Connection-loss detection was silently broken, not just absent.**
`IBKRClient`'s own class docstring described a `CONNECTED -> DEGRADED
(heartbeat missed) -> DISCONNECTED` state machine — but nothing in the
actual code ever set `_state` to `DEGRADED`, and `_IBWrapper` never
overrode `connectionClosed()` either. A real TWS/Gateway disconnect (a
network blip, TWS's own nightly restart) would leave `_state` wrongly stuck
at `CONNECTED` forever, since nothing re-evaluates it asynchronously. That
silently defeats `circuit_breaker.check_broker_connection()`, the one check
`position_monitor.run_one_tick()` relies on every cycle to freeze new
entries while disconnected (see `circuit_breaker.py`'s
`BREAKER_BROKER_DISCONNECTED`) — exactly the kind of mismatch between
documented and actual behavior this sprint's auditing discipline exists to
catch.

**Fix applied** (small, targeted, offline-tested — not a LumiBot/LEAN port):
`_IBWrapper.connectionClosed()` is now overridden and calls a new
`IBKRClient._on_connection_closed()` method, which transitions `_state` to
`CONNECTION_DISCONNECTED` (never overriding a `CONNECTION_HALTED` hard
breaker) and clears the cached verified account. This is a pure, ibapi-free
method precisely so it can be unit-tested without a socket —
`tests/test_execution_ibkr_client.py` adds four tests: the transition
itself, that it's idempotent, that it never clears `HALTED`, and (the one
that matters most) that `circuit_breaker.check_broker_connection()` actually
trips once it fires — tying the fix directly to the safety mechanism it
exists to feed. 1216 tests pass, zero regressions. **No order submission
path changed; `order_manager.py` was not touched.**

## What was deliberately NOT built, and why

- **LEAN's active heartbeat thread + bounded auto-reconnect loop.** This is
  real, non-trivial behavior (a background thread, timer-based health
  checks, a retry/backoff policy) whose correctness fundamentally depends
  on live socket/timing behavior — exactly what "validate... in OFFLINE
  tests only" cannot actually prove. Building it without the ability to
  verify it against a real TWS session would mean shipping unverified
  behavior in the most safety-critical part of this codebase. The passive
  `connectionClosed()` fix above has the opposite risk profile: it only
  ever makes the system **fail closed more reliably** (detect a drop that
  was previously invisible), never adds a new path that submits or retries
  anything. If a user wants the stronger heartbeat-based detection later,
  it's a good candidate for its own reviewed milestone, broker-verified on
  a real Mac TWS session per `docs/platform/BLOCKERS.md` item 1 — not a
  drive-by addition here.
- **Rebuilding OCA/bracket linkage after a reconnect (LEAN's
  `SetContingencies`).** This project's OCA groups are enforced
  server-side at TWS itself (`_build_ibkr_order`'s `ocaGroup`/`ocaType`) —
  they are NOT a client-side data structure that a reconnecting client
  needs to recreate; TWS keeps enforcing the group across a client
  disconnect/reconnect on its own. `reconciliation.py`'s existing
  broker-vs-local comparison (`DISCREPANCY_QUANTITY_MISMATCH`,
  `DISCREPANCY_UNKNOWN_ORDER`, `DISCREPANCY_FILL_MISMATCH`) already covers
  what a resync after reconnect would need to catch for THIS project's
  specific design — a real difference from LEAN's architecture, not an
  oversight.
- **A stronger duplicate-order idempotency key than sequential order ids.**
  Neither reference project has one either (both rely on sequential ids +
  failing closed when disconnected). This project's actual strongest
  duplicate-submission defense is `process_lock.py`'s OS-level singleton
  lock, which prevents the entire class of "two processes race to submit"
  that neither LumiBot's nor LEAN's order-id-level logic addresses at all —
  a genuine advantage of this project's architecture worth noting, not a
  gap.

## Conclusion

No replacement of `order_manager.py`, `reconciliation.py`, or
`circuit_breaker.py` — none of the three categories above cleared the bar
of "proof this project's correctness/safety is actually worse." The one
real, evidenced gap (connection-loss detection silently not working) is
fixed with a minimal, offline-tested, fail-closed-only change. Paper/live
order submission remains exactly as gated as before this review.
