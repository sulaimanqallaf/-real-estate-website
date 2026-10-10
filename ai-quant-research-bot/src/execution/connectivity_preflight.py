"""Guided IBKR Paper broker-connectivity validation workflow (Sprint 3,
"IBKR Paper Readiness" milestone, Task I1: "Prepare a guided broker-
connectivity validation workflow. Verify paper-account identity,
reconnect handling, order reconciliation and kill switches.").

**This never activates autonomous order submission.** Every step here
is read-only or local-state-only: nothing in this module calls
`submit_order()`, `cancel_order()`, or `replace_order()`, and nothing
here ever touches `autonomous_paper.enabled`/`autonomous_paper.
auto_execute.enabled` in `config/settings.yaml` (both stay `false` -
only a human, editing that file after reviewing this report, can
change that - see README's "Rollout" section). This module exists to
produce evidence FOR that human decision, never to make it.

Four independently-reported steps, in order:

1. `check_paper_identity` - re-verifies `account_summary().
   account_mode == PAPER`. `IBKRClient.connect()` itself already
   enforces this fail-closed (raises `AccountModeError` otherwise), so
   by the time a caller has a connected real client this is already
   guaranteed - this step exists as a second, independent check
   against the `Broker` protocol generically (never trust a single
   gate when a second one is this cheap), and so this checklist has
   something to show for "verify paper-account identity" explicitly.

2. `check_reconnect_handling` - the one INTERACTIVE step. There is no
   way to script "kill the user's real TWS/Gateway process" from
   here - that is the entire point: this prints instructions for a
   HUMAN to manually stop/restart their real session (or its network)
   while this polls `connection_state()`, and reports whether a real
   disconnect-then-reconnect was actually observed within the
   timeout. A caller who skips the manual step gets an honest
   `SKIPPED`, never a fabricated `PASS`.

3. `check_order_reconciliation` - calls `reconciliation.reconcile()`
   against whatever the broker reports right now. This is a
   standalone preflight with no `OrderManager` journal to compare
   against, so local state defaults to empty with that caveat stated
   explicitly in the result; a real discrepancy found (e.g. a
   pre-existing position on the paper account) is reported for the
   human's own review, never treated as this step failing - the
   MECHANISM working (completing without raising) is what this step
   verifies.

4. `check_kill_switch` - the only step that needs no broker at all:
   round-trips `circuit_breaker.halt()`/`resume()` and confirms
   `is_halted()`/`check_manual_kill_switch()` respond correctly at
   each state. Skips (never disturbs) a kill switch that is already
   genuinely tripped, and always restores the original state
   afterward even if an assertion mid-test fails.

Steps 1-3 need a REAL TWS/Gateway Paper session this sandbox has no
network path to (see `docs/platform/BLOCKERS.md` item 1) - run `python
-m src.execution.connectivity_preflight` on your own machine, against
your own paper account, per README's "TWS / IB Gateway connection"
section. From this sandbox, or with no session running, steps 1-3
report `BLOCKED` with the real connection error - never a fabricated
pass - while step 4 still runs for real, since it needs nothing this
sandbox lacks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import circuit_breaker, reconciliation
from .broker import ACCOUNT_MODE_PAPER, CONNECTION_CONNECTED, Broker

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"
STATUS_SKIPPED = "SKIPPED"

DEFAULT_RECONNECT_TIMEOUT_SECONDS = 120.0
DEFAULT_RECONNECT_POLL_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class PreflightStepResult:
    step: str
    status: str
    detail: str


@dataclass
class PreflightReport:
    steps: list[PreflightStepResult] = field(default_factory=list)

    @property
    def any_failed(self) -> bool:
        return any(s.status == STATUS_FAIL for s in self.steps)

    @property
    def any_blocked(self) -> bool:
        return any(s.status == STATUS_BLOCKED for s in self.steps)

    @property
    def any_skipped(self) -> bool:
        return any(s.status == STATUS_SKIPPED for s in self.steps)


def check_paper_identity(broker: Broker) -> PreflightStepResult:
    try:
        account = broker.account_summary()
    except Exception as exc:  # noqa: BLE001 - a broker call failing is a real result for this step, not this module's own bug
        return PreflightStepResult("paper_identity", STATUS_FAIL, f"account_summary() raised: {exc}")

    if account.account_mode != ACCOUNT_MODE_PAPER:
        return PreflightStepResult(
            "paper_identity", STATUS_FAIL, f"account_mode is {account.account_mode!r}, not PAPER - refusing to proceed"
        )
    prefix = f"{account.account_id[:2]}**" if account.account_id else "n/a"
    return PreflightStepResult(
        "paper_identity", STATUS_PASS, f"verified PAPER account (id prefix {prefix}, net liquidation {account.net_liquidation})"
    )


def check_reconnect_handling(
    broker: Broker,
    logger: logging.Logger,
    timeout_seconds: float = DEFAULT_RECONNECT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_RECONNECT_POLL_INTERVAL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
    print_fn: Callable[[str], None] = print,
) -> PreflightStepResult:
    print_fn("\n[reconnect_handling] Now manually STOP your TWS/Gateway (or disconnect its network).")
    print_fn(f"Waiting up to {timeout_seconds:.0f}s to observe a real disconnect, then a real reconnect...")

    start = clock_fn()
    observed_states = [broker.connection_state()]
    saw_disconnect = False

    while clock_fn() - start < timeout_seconds:
        sleep_fn(poll_interval_seconds)
        state = broker.connection_state()
        if state != observed_states[-1]:
            observed_states.append(state)
            print_fn(f"  connection_state -> {state}")
        if state != CONNECTION_CONNECTED:
            saw_disconnect = True
        if saw_disconnect and state == CONNECTION_CONNECTED and len(observed_states) >= 3:
            logger.info("Reconnect check: observed real disconnect/reconnect sequence: %s", observed_states)
            return PreflightStepResult("reconnect_handling", STATUS_PASS, f"observed a real disconnect and reconnect: {observed_states}")

    if not saw_disconnect:
        return PreflightStepResult(
            "reconnect_handling", STATUS_SKIPPED,
            f"no disconnect observed within {timeout_seconds:.0f}s - this step requires you to manually stop/restart "
            "TWS/Gateway; re-run and actually do so to exercise it",
        )
    return PreflightStepResult(
        "reconnect_handling", STATUS_FAIL, f"disconnected but never reconnected within {timeout_seconds:.0f}s: {observed_states}"
    )


def check_order_reconciliation(
    broker: Broker,
    logger: logging.Logger,
    local_open_trades: list[dict[str, Any]] | None = None,
    local_open_orders: list[dict[str, Any]] | None = None,
) -> PreflightStepResult:
    local_open_trades = local_open_trades if local_open_trades is not None else []
    local_open_orders = local_open_orders if local_open_orders is not None else []
    try:
        report = reconciliation.reconcile(broker, local_open_trades, local_open_orders)
    except Exception as exc:  # noqa: BLE001 - a broker call failing is a real result for this step
        return PreflightStepResult("order_reconciliation", STATUS_FAIL, f"reconcile() raised: {exc}")

    caveat = "compared against EMPTY local state (standalone preflight, no OrderManager journal here)"
    if report.discrepancies:
        return PreflightStepResult(
            "order_reconciliation", STATUS_PASS,
            f"mechanism works - {caveat}; {report.summary()} (expected if this paper account has pre-existing "
            "positions/orders - review the list above, this is not itself a failure of this step)",
        )
    return PreflightStepResult("order_reconciliation", STATUS_PASS, f"mechanism works - {caveat}; {report.summary()}")


def check_kill_switch(config: dict[str, Any], logger: logging.Logger) -> PreflightStepResult:
    already_halted, existing_reason = circuit_breaker.is_halted(config)
    if already_halted:
        return PreflightStepResult(
            "kill_switch", STATUS_SKIPPED,
            f"the kill switch is currently ACTIVE ({existing_reason!r}) - skipping the round-trip self-test so as not "
            "to disturb a real halt; resume it yourself first if you want this step exercised",
        )

    try:
        circuit_breaker.halt(config, reason="I1 connectivity preflight self-test")
        halted, _ = circuit_breaker.is_halted(config)
        if not halted:
            return PreflightStepResult("kill_switch", STATUS_FAIL, "halt() did not result in is_halted() reporting True")
        blocked_reason = circuit_breaker.check_manual_kill_switch(config)
        if blocked_reason is None:
            return PreflightStepResult("kill_switch", STATUS_FAIL, "check_manual_kill_switch() did not report a blocking reason while halted")
    finally:
        circuit_breaker.resume(config)

    still_halted, _ = circuit_breaker.is_halted(config)
    if still_halted:
        return PreflightStepResult("kill_switch", STATUS_FAIL, "resume() did not clear is_halted()")
    return PreflightStepResult(
        "kill_switch", STATUS_PASS, "halt()/resume() round-trip verified: is_halted() and check_manual_kill_switch() both responded correctly"
    )


def format_preflight_report_text(report: PreflightReport) -> str:
    lines = ["=" * 72, "IBKR PAPER CONNECTIVITY PREFLIGHT (Sprint 3, Task I1)", "=" * 72]
    for s in report.steps:
        lines.append(f"[{s.status}] {s.step}: {s.detail}")
    lines.append("")

    if report.any_failed:
        lines.append("RESULT: one or more checks FAILED. Do not proceed toward autonomous order submission until every check passes.")
    elif report.any_blocked:
        lines.append(
            "RESULT: some checks were BLOCKED (no real TWS/Gateway connection available from here) - "
            "re-run this on your own machine against your own Paper session (README 'TWS / IB Gateway connection')."
        )
    elif report.any_skipped:
        lines.append("RESULT: every runnable check passed; some were SKIPPED (see above) - re-run and complete the manual step(s) before relying on this.")
    else:
        lines.append("RESULT: every check passed.")

    lines.append(
        "Autonomous order submission remains OFF regardless of this result "
        "(autonomous_paper.enabled / autonomous_paper.auto_execute.enabled are false in config/settings.yaml) - "
        "only a human, after reviewing this report, may change that."
    )
    return "\n".join(lines)


def main() -> int:
    """`python -m src.execution.connectivity_preflight [--skip-reconnect-check]
    [--reconnect-timeout SECONDS]` - see module docstring. Never places,
    cancels, or modifies an order; never changes `execution.mode` or
    `autonomous_paper.*`."""
    import argparse

    from .ibkr_client import AccountModeError, IBKRClient, IBKRConfig, IBKRConnectionError
    from ..utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Guided IBKR Paper broker-connectivity validation workflow.")
    parser.add_argument("--skip-reconnect-check", action="store_true", help="Skip the interactive reconnect-handling step.")
    parser.add_argument("--reconnect-timeout", type=float, default=DEFAULT_RECONNECT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="connectivity_preflight.log")

    ibkr_config = IBKRConfig.from_env()
    client = IBKRClient(ibkr_config)

    print(f"Connecting to {ibkr_config.host}:{ibkr_config.port} (client id {ibkr_config.client_id})...")
    steps: list[PreflightStepResult] = []
    connected = False
    try:
        client.connect()
        connected = True
    except (AccountModeError, IBKRConnectionError) as exc:
        reason = f"could not establish a connection to verify this against: {exc}"
        steps.extend(PreflightStepResult(name, STATUS_BLOCKED, reason) for name in ("paper_identity", "reconnect_handling", "order_reconciliation"))

    if connected:
        try:
            steps.append(check_paper_identity(client))
            if args.skip_reconnect_check:
                steps.append(PreflightStepResult("reconnect_handling", STATUS_SKIPPED, "--skip-reconnect-check was passed"))
            else:
                steps.append(check_reconnect_handling(client, logger, timeout_seconds=args.reconnect_timeout))
            steps.append(check_order_reconciliation(client, logger))
        finally:
            client.disconnect()

    steps.append(check_kill_switch(config, logger))  # always runs - needs no broker

    report = PreflightReport(steps=steps)
    print()
    print(format_preflight_report_text(report))
    return 1 if report.any_failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
