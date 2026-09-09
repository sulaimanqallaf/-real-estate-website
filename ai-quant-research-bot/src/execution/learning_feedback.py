"""Phase 7 Part V: feeds ACTUAL broker-paper execution results back into
`paper_trades.csv` (and, through it, `performance_tracker.py`/`strategy_
memory.py`) the moment a stop or target exit leg actually fills - never
simulated, never invented, and never `paper_trade_tracker.py`'s daily-bar
guess for a trade this system actually executed at a broker.

**Never auto-retrains.** This module's only job is to make sure a real
outcome gets durably recorded once. Feeding that record into an actual ML
retraining run stays a fully separate, deliberately manual step (see
`src/ml/registry.py` and its CLI) - nothing here ever triggers one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .. import paper_trades
from . import order_state
from .order_manager import OrderManager


def _commission_for_order(broker: Any, broker_order_id: str) -> float:
    return sum((e.commission or 0.0) for e in broker.executions() if e.broker_order_id == broker_order_id)


def check_exit_fills(manager: OrderManager, config: dict[str, Any], logger: logging.Logger) -> list[dict[str, Any]]:
    """Poll every EXIT_PENDING managed order for a stop/target fill.

    On the first leg to fill: cancel the now-orphaned sibling leg (this
    phase submits stop/target as two independent orders rather than an
    IBKR OCA/bracket group - see `order_manager.py`'s module docstring for
    why - so nothing else would ever cancel it once the position is
    closed), record the trade's ACTUAL fill/exit/commission into
    `paper_trades.csv`, and mark the managed order CLOSED.

    Returns the trades that closed during this call in the same shape
    `paper_trade_tracker.check_open_trades()` returns, so `main.py`'s
    existing exit-notification formatting can be reused unchanged for a
    broker-executed close, not just a simulated one."""
    closed_trades: list[dict[str, Any]] = []

    for managed in manager.all_managed():
        if managed.state != order_state.STATE_EXIT_PENDING:
            continue
        if not managed.intent.trade_id:
            continue

        stop_order = manager.broker.get_order(managed.stop_broker_order_id) if managed.stop_broker_order_id else None
        target_order = manager.broker.get_order(managed.target_broker_order_id) if managed.target_broker_order_id else None

        filled_leg = None
        exit_reason = None
        status = None
        sibling_order_id = None
        if stop_order is not None and stop_order.status == "Filled":
            filled_leg, exit_reason, status = stop_order, "Stop loss triggered", "STOPPED"
            sibling_order_id = managed.target_broker_order_id
        elif target_order is not None and target_order.status == "Filled":
            filled_leg, exit_reason, status = target_order, "Target price reached", "TARGET_HIT"
            sibling_order_id = managed.stop_broker_order_id

        if filled_leg is None:
            continue

        if sibling_order_id:
            manager.broker.cancel_order(sibling_order_id)

        commission = _commission_for_order(manager.broker, filled_leg.broker_order_id)
        exit_price = filled_leg.avg_fill_price if filled_leg.avg_fill_price is not None else filled_leg.limit_price
        closed = paper_trades.close_trade_with_actual_fill(
            trade_id=managed.intent.trade_id,
            exit_price=exit_price,
            exit_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            exit_reason=exit_reason,
            status=status,
            config=config,
            notes=f"Broker-executed exit (commission ${commission:.2f})" if commission else "Broker-executed exit",
        )
        manager.close_position(managed.intent.intent_id)
        if closed is not None:
            closed_trades.append(closed)
            logger.info(
                "Broker-paper trade closed: %s %s status=%s pnl_pct=%s",
                closed["ticker"], closed["trade_id"], closed["status"], closed.get("pnl_pct"),
            )

    return closed_trades
