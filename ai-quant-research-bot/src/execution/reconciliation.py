"""Broker reconciliation (Phase 7 Part J). Compares local (journal) state
against the broker's own positions/open orders/executions at startup and
after every reconnect. Never silently resolves a dangerous discrepancy -
`reconcile()` returns a structured report; any material discrepancy means
the caller (main entry point / position_monitor.py) must treat the system
as `RECONCILIATION_REQUIRED` and block new entries (enforced via
`circuit_breaker.check_reconciliation`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .broker import Broker

DISCREPANCY_LOCAL_OPEN_BROKER_MISSING = "LOCAL_OPEN_BROKER_MISSING"
DISCREPANCY_BROKER_POSITION_LOCAL_MISSING = "BROKER_POSITION_LOCAL_MISSING"
DISCREPANCY_UNKNOWN_ORDER = "UNKNOWN_ORDER"
DISCREPANCY_QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
DISCREPANCY_FILL_MISMATCH = "FILL_MISMATCH"


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    ticker: str | None
    detail: str


@dataclass(frozen=True)
class ReconciliationReport:
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.discrepancies) == 0

    def summary(self) -> str:
        if self.ok:
            return "Reconciliation clean: local state matches the broker."
        lines = [f"{len(self.discrepancies)} discrepancy(ies) found:"]
        lines.extend(f"  [{d.kind}] {d.ticker or '-'}: {d.detail}" for d in self.discrepancies)
        return "\n".join(lines)


def reconcile(broker: Broker, local_open_trades: list[dict[str, Any]], local_open_orders: list[dict[str, Any]]) -> ReconciliationReport:
    """`local_open_trades`: rows from paper_trades.csv (or the execution
    journal) with `status == "OPEN"`, each carrying `ticker` and
    `position_size`. `local_open_orders`: rows with a `broker_order_id`
    this system believes is still live at the broker."""
    discrepancies: list[Discrepancy] = []

    broker_positions = {p.ticker: p for p in broker.positions()}
    broker_orders = {o.broker_order_id: o for o in broker.open_orders()}

    local_tickers = {t["ticker"] for t in local_open_trades}
    local_position_size = {}
    for t in local_open_trades:
        local_position_size[t["ticker"]] = local_position_size.get(t["ticker"], 0.0) + float(t["position_size"])

    for ticker, local_qty in local_position_size.items():
        broker_pos = broker_positions.get(ticker)
        if broker_pos is None:
            discrepancies.append(Discrepancy(DISCREPANCY_LOCAL_OPEN_BROKER_MISSING, ticker, f"local {local_qty} shares open, broker shows no position"))
        elif abs(broker_pos.quantity - local_qty) > 1e-6:
            discrepancies.append(Discrepancy(DISCREPANCY_QUANTITY_MISMATCH, ticker, f"local {local_qty} shares vs. broker {broker_pos.quantity} shares"))

    for ticker, broker_pos in broker_positions.items():
        if ticker not in local_tickers:
            discrepancies.append(Discrepancy(DISCREPANCY_BROKER_POSITION_LOCAL_MISSING, ticker, f"broker shows {broker_pos.quantity} shares with no matching local OPEN trade"))

    local_order_ids = {o["broker_order_id"] for o in local_open_orders if o.get("broker_order_id")}
    for order_id in broker_orders:
        if order_id not in local_order_ids:
            discrepancies.append(Discrepancy(DISCREPANCY_UNKNOWN_ORDER, broker_orders[order_id].ticker, f"broker order {order_id} has no matching local record"))

    for local_order in local_open_orders:
        order_id = local_order.get("broker_order_id")
        if not order_id:
            continue
        broker_order = broker_orders.get(order_id)
        if broker_order is None:
            continue  # already closed at the broker - fill/close reconciliation, not a discrepancy by itself
        local_filled = local_order.get("filled_quantity", 0.0)
        if abs(local_filled - broker_order.filled_quantity) > 1e-6:
            discrepancies.append(
                Discrepancy(DISCREPANCY_FILL_MISMATCH, broker_order.ticker, f"local filled {local_filled} vs. broker filled {broker_order.filled_quantity} for order {order_id}")
            )

    return ReconciliationReport(discrepancies=discrepancies)
