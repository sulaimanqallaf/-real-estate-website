"""Long-running execution monitoring process (Phase 7 Part Q).

`python -m src.execution.position_monitor` is a SEPARATE process from
`python -m src.main` - the daily research/opportunity-generation run stays a
once-a-day batch job (see `src/main.py`'s own module docstring, unchanged
since Version 1); this module is the one place a persistent loop belongs.
It owns: IBKR connection health, polling open orders/fills, syncing stop/
target protection, reconciliation, circuit-breaker status, and exit
notifications - it does not generate new candidates or run the research
pipeline itself.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import circuit_breaker, learning_feedback, order_manager, reconciliation
from .broker import Broker, CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, CONNECTION_HALTED


def run_one_tick(
    broker: Broker,
    manager: order_manager.OrderManager,
    config: dict[str, Any],
    local_open_trades: list[dict[str, Any]],
    logger: logging.Logger,
) -> dict[str, Any]:
    """One monitoring cycle: check connection health, poll fills for every
    managed order still in flight, run reconciliation, and evaluate circuit
    breakers. Returns a structured tick summary - never raises; every
    sub-step is isolated so one failing ticker's poll never stops the rest."""
    from ..utils import safe_run

    state = broker.connection_state()
    if state != CONNECTION_CONNECTED:
        logger.warning("Position monitor: broker connection_state is '%s' - freezing new entries, preserving known state.", state)
        return {"connection_state": state, "new_entries_allowed": False}

    for managed in manager.all_managed():
        if managed.state in ("FILLED", "PARTIALLY_FILLED", "ACKNOWLEDGED", "EXIT_PENDING"):
            safe_run(logger, f"poll fill for {managed.intent.ticker}", lambda m=managed: manager.poll_entry_fill(m.intent.intent_id))

    closed_trades = safe_run(logger, "check exit fills", lambda: learning_feedback.check_exit_fills(manager, config, logger)) or []

    local_open_orders = [
        {"broker_order_id": m.entry_broker_order_id, "filled_quantity": m.filled_quantity}
        for m in manager.all_managed()
        if m.entry_broker_order_id and m.state not in ("CANCELLED", "REJECTED", "CLOSED", "ERROR")
    ]
    report = reconciliation.reconcile(broker, local_open_trades, local_open_orders)
    if not report.ok:
        logger.error("Position monitor: reconciliation discrepancies found:\n%s", report.summary())

    breaker_result = circuit_breaker.check_all(
        config,
        account=safe_run(logger, "account_summary", broker.account_summary),
        connection_state=state,
        reconciliation_ok=report.ok,
        current_open_positions=len(broker.positions()),
    )

    return {
        "connection_state": state,
        "reconciliation": report,
        "breakers": breaker_result,
        "new_entries_allowed": not breaker_result.blocked,
        "closed_trades": closed_trades,
    }


def run_forever(
    broker: Broker,
    manager: order_manager.OrderManager,
    config: dict[str, Any],
    logger: logging.Logger,
    poll_interval_seconds: int = 15,
    local_open_trades_fn: Any = None,
    token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """The actual persistent loop - never called from tests (see
    `run_one_tick` for the testable unit). `local_open_trades_fn` is a
    zero-arg callable returning the current OPEN rows from
    paper_trades.csv, re-read every tick so a concurrently-running
    approval_listener's writes are always picked up fresh. `token`/
    `chat_id`, when given, send the same exit notification format
    `paper_trade_tracker.py` already uses for a simulated close - Part V's
    "auto execution must not be silent" applies just as much to a
    broker-executed exit."""
    from ..utils import safe_run

    logger.info("Position monitor starting (poll interval %ss).", poll_interval_seconds)
    while True:
        local_open_trades = local_open_trades_fn() if local_open_trades_fn else []
        tick = run_one_tick(broker, manager, config, local_open_trades, logger)
        if token and chat_id:
            from .. import paper_trade_tracker, telegram_bot

            for trade in tick.get("closed_trades", []):
                notice = paper_trade_tracker.format_exit_notification(trade)
                safe_run(logger, f"{trade['ticker']} broker exit notification", lambda n=notice: telegram_bot.send_telegram_message(token, chat_id, n, logger))
        time.sleep(poll_interval_seconds)


def main() -> int:
    from ..utils import load_config, setup_logging
    from .broker import FakeBroker

    config = load_config(None)
    logger = setup_logging(config, log_filename="position_monitor.log")

    mode = config.get("execution", {}).get("mode", "DRY_RUN")
    if mode != "IBKR_PAPER":
        logger.info("execution.mode is '%s', not IBKR_PAPER - position_monitor has nothing to connect to. Exiting.", mode)
        return 0

    from .ibkr_client import IBKRClient

    broker = IBKRClient()
    try:
        broker.connect()
    except Exception as exc:  # noqa: BLE001
        logger.error("Position monitor could not connect to IBKR Paper: %s", exc)
        return 1

    import os

    manager = order_manager.OrderManager(broker, config, order_manager.ExecutionJournal(config.get("execution", {}).get("journal_path", "data/journal/executions.jsonl")))
    run_forever(broker, manager, config, logger, token=os.environ.get("TELEGRAM_BOT_TOKEN"), chat_id=os.environ.get("TELEGRAM_CHAT_ID"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
