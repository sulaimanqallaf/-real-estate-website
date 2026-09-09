"""Broker abstraction (Phase 7 Part A). The rest of this codebase depends on
`Broker`, never on IBKR-specific calls - `ibkr_client.IBKRClient` and
`FakeBroker` (below, used throughout the test suite) are the only two
implementations, and both satisfy this exact same interface.

Every data shape returned by a `Broker` is a plain, JSON-serializable dict
or dataclass - never an `ibapi`-specific object - so nothing downstream
(order_manager.py, reconciliation.py, the journal) needs to know which
broker produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

ACCOUNT_MODE_PAPER = "PAPER"
ACCOUNT_MODE_LIVE = "LIVE"
ACCOUNT_MODE_UNKNOWN = "UNKNOWN"

CONNECTION_CONNECTING = "CONNECTING"
CONNECTION_CONNECTED = "CONNECTED"
CONNECTION_DEGRADED = "DEGRADED"
CONNECTION_DISCONNECTED = "DISCONNECTED"
CONNECTION_HALTED = "HALTED"


@dataclass(frozen=True)
class AccountSummary:
    account_id: str
    account_mode: str  # ACCOUNT_MODE_PAPER | ACCOUNT_MODE_LIVE | ACCOUNT_MODE_UNKNOWN
    net_liquidation: float | None
    available_funds: float | None
    buying_power: float | None
    currency: str = "USD"


@dataclass(frozen=True)
class BrokerPosition:
    ticker: str
    quantity: float
    avg_cost: float | None


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    perm_id: str | None
    ticker: str
    side: str  # "BUY" | "SELL"
    order_type: str
    quantity: float
    limit_price: float | None
    status: str  # broker-native status string
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    avg_fill_price: float | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class BrokerExecution:
    execution_id: str
    broker_order_id: str
    ticker: str
    side: str
    shares: float
    price: float
    commission: float | None
    timestamp: datetime


class Broker(Protocol):
    """Every method here must be safe to call repeatedly and must never
    silently upgrade "I don't know" into a guess - see
    `ibkr_client.IBKRClient.verify_paper_account` for the concrete
    fail-closed contract every implementation is expected to honor."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def connection_state(self) -> str: ...

    def account_summary(self) -> AccountSummary: ...

    def positions(self) -> list[BrokerPosition]: ...

    def open_orders(self) -> list[BrokerOrder]: ...

    def get_order(self, broker_order_id: str) -> BrokerOrder | None: ...

    def submit_order(self, intent: Any) -> BrokerOrder: ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def replace_order(self, broker_order_id: str, **changes: Any) -> BrokerOrder: ...

    def executions(self) -> list[BrokerExecution]: ...


class FakeBroker:
    """An in-memory `Broker` for tests (Part A: "Add a FakeBroker for
    tests"). Deterministic, synchronous, no network - lets every other
    module in `src/execution/` be tested without IBKR/TWS running anywhere.
    `account_mode` defaults to PAPER but can be set to LIVE/UNKNOWN in a test
    to exercise the fail-closed account-verification path."""

    def __init__(self, account_mode: str = ACCOUNT_MODE_PAPER, account_id: str = "FAKE123", net_liquidation: float = 100_000.0):
        self.account_mode = account_mode
        self.account_id = account_id
        self.net_liquidation = net_liquidation
        self._state = CONNECTION_DISCONNECTED
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._executions: list[BrokerExecution] = []
        self._next_order_id = 1000
        self.submitted_intents: list[Any] = []  # test introspection: every intent ever submitted, in order

    def connect(self) -> None:
        self._state = CONNECTION_CONNECTED

    def disconnect(self) -> None:
        self._state = CONNECTION_DISCONNECTED

    def connection_state(self) -> str:
        return self._state

    def account_summary(self) -> AccountSummary:
        return AccountSummary(
            account_id=self.account_id, account_mode=self.account_mode,
            net_liquidation=self.net_liquidation, available_funds=self.net_liquidation,
            buying_power=self.net_liquidation * 2,
        )

    def positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    def open_orders(self) -> list[BrokerOrder]:
        return [o for o in self._orders.values() if o.status not in ("Filled", "Cancelled", "Rejected")]

    def get_order(self, broker_order_id: str) -> BrokerOrder | None:
        """Looks up ONE order regardless of terminal state - unlike
        `open_orders()`, this is how `order_manager.poll_entry_fill()`
        detects a broker rejection (a rejected order is, by definition,
        never present in `open_orders()`, so that method alone can never
        surface a rejection)."""
        return self._orders.get(broker_order_id)

    def submit_order(self, intent: Any) -> BrokerOrder:
        self.submitted_intents.append(intent)
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        order = BrokerOrder(
            broker_order_id=order_id, perm_id=order_id, ticker=intent.ticker, side=intent.side,
            order_type=intent.order_type, quantity=intent.quantity, limit_price=intent.entry_price,
            status="Submitted", filled_quantity=0.0, remaining_quantity=intent.quantity,
        )
        self._orders[order_id] = order
        return order

    def cancel_order(self, broker_order_id: str) -> bool:
        order = self._orders.get(broker_order_id)
        if order is None:
            return False
        self._orders[broker_order_id] = _replace(order, status="Cancelled")
        return True

    def replace_order(self, broker_order_id: str, **changes: Any) -> BrokerOrder:
        order = self._orders[broker_order_id]
        updated = _replace(order, **changes)
        self._orders[broker_order_id] = updated
        return updated

    def executions(self) -> list[BrokerExecution]:
        return list(self._executions)

    # --- test-only helpers: simulate broker-side events ----------------------------

    def simulate_fill(self, broker_order_id: str, shares: float, price: float, commission: float = 1.0) -> None:
        """Test helper: simulate a (possibly partial) fill arriving from the
        broker. Never called by production code."""
        order = self._orders[broker_order_id]
        new_filled = order.filled_quantity + shares
        new_remaining = max(0.0, order.quantity - new_filled)
        status = "Filled" if new_remaining <= 0 else "PartiallyFilled" if new_filled > 0 else order.status
        self._orders[broker_order_id] = _replace(order, filled_quantity=new_filled, remaining_quantity=new_remaining, status=status, avg_fill_price=price)
        self._executions.append(
            BrokerExecution(
                execution_id=f"exec-{len(self._executions) + 1}", broker_order_id=broker_order_id, ticker=order.ticker,
                side=order.side, shares=shares, price=price, commission=commission, timestamp=datetime.now(),
            )
        )
        if order.side == "BUY":
            existing = self._positions.get(order.ticker)
            new_qty = (existing.quantity if existing else 0.0) + shares
            self._positions[order.ticker] = BrokerPosition(order.ticker, new_qty, price)
        else:
            existing = self._positions.get(order.ticker)
            if existing:
                new_qty = existing.quantity - shares
                if new_qty <= 0:
                    self._positions.pop(order.ticker, None)
                else:
                    self._positions[order.ticker] = BrokerPosition(order.ticker, new_qty, existing.avg_cost)

    def simulate_reject(self, broker_order_id: str, reason: str = "Simulated rejection") -> None:
        order = self._orders[broker_order_id]
        self._orders[broker_order_id] = _replace(order, status="Rejected")

    def inject_unknown_position(self, ticker: str, quantity: float, avg_cost: float = 100.0) -> None:
        """Test helper: simulate a broker position local state never created -
        exercises reconciliation.py's discrepancy detection."""
        self._positions[ticker] = BrokerPosition(ticker, quantity, avg_cost)


def _replace(order: BrokerOrder, **changes: Any) -> BrokerOrder:
    data = {
        "broker_order_id": order.broker_order_id, "perm_id": order.perm_id, "ticker": order.ticker,
        "side": order.side, "order_type": order.order_type, "quantity": order.quantity,
        "limit_price": order.limit_price, "status": order.status, "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity, "avg_fill_price": order.avg_fill_price, "parent_id": order.parent_id,
    }
    data.update(changes)
    return BrokerOrder(**data)
