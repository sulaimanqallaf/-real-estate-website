"""The real `Broker` implementation, talking to a locally-running TWS or IB
Gateway over the official `ibapi` TWS API (Phase 7 Part B).

**`ibapi` is imported LAZILY** - only inside `IBKRClient.connect()`, never
at module import time. Two reasons: (1) the whole rest of this codebase
(every test, `DRY_RUN` mode, the daily research run) must work with zero
IBKR dependency installed at all, exactly like the SEC/FRED providers in
earlier phases degrade cleanly with no credentials configured; (2) `ibapi`'s
PyPI package ships an old-style `setup.py` that fails to build on some
platforms (confirmed in this project's own CI/dev sandbox - a Debian-patched
`setuptools`/`distutils` incompatibility, not a Windows/macOS-specific
issue). On a typical developer machine (including the Mac this is meant to
run on) `pip install ibapi` installs cleanly; where it doesn't, IBKR's own
TWS API download (Downloads page -> "TWS API" -> the bundled
`IBJts/source/pythonclient` directory) can be installed directly with
`pip install .` from that directory instead. See README "IBKR PAPER
connection" for both install paths.

**This module NEVER connects to this session's own network** - there is no
TWS/IB Gateway reachable from this sandbox, and this code is not exercised
against a live socket anywhere in this repository's test suite. Every test
in `tests/test_execution_*.py` uses `broker.FakeBroker` instead. The only
place `IBKRClient` is meant to actually run is on the user's own machine,
against their own local TWS Paper session, which they start and log into
themselves (see README) - this module never needs, stores, or transmits an
IBKR username or password.

**Connection, account verification, READ-ONLY portfolio state, and order
submission/modification/cancellation are all wired for real** (`connect()`,
`account_summary()`, `positions()`, `open_orders()`, `get_order()`,
`submit_order()`, `replace_order()`, `cancel_order()`) and confirmed
working against a real TWS Paper session for everything through
read-only state (see README "IBKR Paper read-only portfolio readiness").
`submit_order()`/`replace_order()`/`cancel_order()` are new, carefully
written against the documented `ibapi` contract, but - like every real
network call in this module - exercised only against a real TWS from the
user's own machine, never from this repository's test suite. `positions()`
uses `reqPositions()`; `open_orders()`/`get_order()` use
`reqAllOpenOrders()` (every open order TWS knows about for this login,
not only ones placed through this API session - deliberately broad, so
Part J's reconciliation can actually detect an "unknown" order the system
didn't create). `replace_order()`/`cancel_order()` reuse the SAME
`placeOrder`/`cancelOrder` calls IBKR itself documents as the "modify an
existing order" and "cancel" conventions - there is no separate "modify"
endpoint. **`executions()` remains `NotImplementedError`** - fill
quantity/price is already available through `get_order()`'s `orderStatus`
data (the actual mechanism `order_manager.poll_entry_fill()` uses), so
`executions()` is only needed for a commission figure in a journal note,
not for anything safety-relevant; it's deferred rather than adding a
fourth untested `ibapi` callback chain for a cosmetic detail.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from . import order_state
from .broker import (
    ACCOUNT_MODE_LIVE,
    ACCOUNT_MODE_PAPER,
    ACCOUNT_MODE_UNKNOWN,
    CONNECTION_CONNECTED,
    CONNECTION_CONNECTING,
    CONNECTION_DEGRADED,
    CONNECTION_DISCONNECTED,
    CONNECTION_HALTED,
    AccountSummary,
    BrokerExecution,
    BrokerOrder,
    BrokerPosition,
)

# This codebase's internal order-type vocabulary (order_state.py) vs.
# IBKR's own order-type strings - translated at the one point that
# actually talks to ibapi, so nothing upstream needs to know IBKR's
# conventions.
_ORDER_TYPE_TO_IBKR = {order_state.ORDER_TYPE_LIMIT: "LMT", order_state.ORDER_TYPE_STOP: "STP"}

# Long US stocks/ETFs only (Part A/E hard constraint) - every order this
# client ever places uses exactly this contract shape; there is no
# parameter anywhere that can change secType to an option/future/forex
# contract or route anywhere other than SMART.
_STOCK_EXCHANGE = "SMART"
_STOCK_CURRENCY = "USD"
_STOCK_SEC_TYPE = "STK"

# IBKR's own documented default ports - never guessed, always explicit.
DEFAULT_TWS_PAPER_PORT = 7497
DEFAULT_TWS_LIVE_PORT = 7496
DEFAULT_GATEWAY_PAPER_PORT = 4002
DEFAULT_GATEWAY_LIVE_PORT = 4001

# Ports IBKR documents as LIVE - if a configured port matches one of these,
# verify_paper_account() refuses regardless of what the account query says,
# since a misconfigured port is itself a live-trading risk this system must
# never silently tolerate.
_KNOWN_LIVE_PORTS = {DEFAULT_TWS_LIVE_PORT, DEFAULT_GATEWAY_LIVE_PORT}


class AccountModeError(Exception):
    """Raised by `verify_paper_account()` - LIVE_ACCOUNT_BLOCKED or
    ACCOUNT_MODE_UNVERIFIED. There is no override flag for this in Phase 7;
    catching and ignoring this exception anywhere would defeat the entire
    point of this module."""


class IBKRConnectionError(Exception):
    pass


def verify_paper_account(account_id: str | None, reported_mode: str | None, configured_port: int, expected_mode: str = ACCOUNT_MODE_PAPER) -> AccountSummary:
    """The mandatory account-mode gate (Part B). Raises `AccountModeError`
    for anything except an unambiguous PAPER account on a non-live port -
    LIVE, UNKNOWN, ambiguous, or missing all fail closed. There is
    deliberately no parameter here that can force this to pass."""
    if expected_mode != ACCOUNT_MODE_PAPER:
        raise AccountModeError("ACCOUNT_MODE_UNVERIFIED: this codebase has no supported expected_mode other than PAPER.")

    if configured_port in _KNOWN_LIVE_PORTS:
        raise AccountModeError(f"LIVE_ACCOUNT_BLOCKED: configured port {configured_port} is a documented IBKR LIVE port.")

    if not account_id:
        raise AccountModeError("ACCOUNT_MODE_UNVERIFIED: no account_id was returned by the broker.")

    if reported_mode is None or reported_mode == ACCOUNT_MODE_UNKNOWN:
        raise AccountModeError(f"ACCOUNT_MODE_UNVERIFIED: broker did not report an unambiguous account mode for account {account_id}.")

    if reported_mode == ACCOUNT_MODE_LIVE:
        raise AccountModeError(f"LIVE_ACCOUNT_BLOCKED: account {account_id} reports as LIVE.")

    if reported_mode != ACCOUNT_MODE_PAPER:
        raise AccountModeError(f"ACCOUNT_MODE_UNVERIFIED: account {account_id} reported an unrecognized mode '{reported_mode}'.")

    return AccountSummary(account_id=account_id, account_mode=ACCOUNT_MODE_PAPER, net_liquidation=None, available_funds=None, buying_power=None)


def classify_account_id(account_id: str) -> str:
    """IBKR paper account IDs are conventionally prefixed 'DU' (Demo/paper
    User); live accounts are 'U' followed by digits. This is a
    best-effort, DOCUMENTED heuristic used only as one signal among several
    - `verify_paper_account` above is the actual enforcement point, and
    this heuristic alone never promotes an ambiguous account to PAPER. It
    exists so a client can proactively surface "this looks like a live
    account ID" before even calling the broker's account-summary endpoint."""
    if account_id.startswith("DU"):
        return ACCOUNT_MODE_PAPER
    if account_id.startswith("DF"):
        return ACCOUNT_MODE_PAPER  # IBKR "friends and family" / paper-linked variants
    if account_id.startswith("U"):
        return ACCOUNT_MODE_LIVE
    return ACCOUNT_MODE_UNKNOWN


def _with_account_values(summary: AccountSummary, account_values: dict[str, str]) -> AccountSummary:
    """Fills in net_liquidation/available_funds/buying_power from TWS's own
    `reqAccountSummary` response - `verify_paper_account()` itself never
    needs these (account mode is decided from id/port alone), but
    `account_summary()`'s callers (sizing, circuit breakers) do."""

    def _float(tag: str) -> float | None:
        raw = account_values.get(tag)
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    return AccountSummary(
        account_id=summary.account_id,
        account_mode=summary.account_mode,
        net_liquidation=_float("NetLiquidation"),
        available_funds=_float("AvailableFunds"),
        buying_power=_float("BuyingPower"),
    )


def _order_from_raw(entry: dict[str, Any] | None) -> BrokerOrder:
    """Builds a `BrokerOrder` from the wrapper's accumulated openOrder/
    orderStatus fields. Pure dict -> dataclass mapping - no `ibapi` types
    involved - so it's directly unit-testable without `ibapi` installed.

    Note on "Rejected": IBKR reports an order rejection through the
    `error()` callback (a specific error code), not through orderStatus's
    `status` field becoming the literal string "Rejected" - so a rejected
    order read back through this path will show whatever terminal status
    TWS actually reported (commonly "Cancelled" or "Inactive"), not
    "Rejected" specifically. True rejection detection for an order THIS
    client submitted is handled when `submit_order()` is wired, not here."""
    return BrokerOrder(
        broker_order_id=entry["broker_order_id"],
        perm_id=entry.get("perm_id"),
        ticker=entry.get("ticker") or "",
        side=entry.get("side") or "",
        order_type=entry.get("order_type") or "",
        quantity=float(entry.get("quantity") or 0.0),
        limit_price=entry.get("limit_price"),
        status=entry.get("status") or "Unknown",
        filled_quantity=float(entry.get("filled_quantity") or 0.0),
        remaining_quantity=float(entry.get("remaining_quantity") or 0.0),
        avg_fill_price=entry.get("avg_fill_price"),
        parent_id=entry.get("parent_id"),
    )


@dataclass
class IBKRConfig:
    host: str
    port: int
    client_id: int
    account_id: str | None
    expected_account_mode: str = ACCOUNT_MODE_PAPER

    @classmethod
    def from_env(cls) -> "IBKRConfig":
        import os

        return cls(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", DEFAULT_TWS_PAPER_PORT)),
            client_id=int(os.environ.get("IBKR_CLIENT_ID", "1")),
            account_id=os.environ.get("IBKR_ACCOUNT_ID") or None,
            expected_account_mode=os.environ.get("IBKR_EXPECTED_ACCOUNT_MODE", ACCOUNT_MODE_PAPER),
        )


class IBKRClient:
    """Real `Broker` implementation. Every public method matches
    `broker.Broker`'s Protocol exactly, so `order_manager.py` and
    everything above it never needs to know whether it's talking to this
    or to `FakeBroker`.

    Connection health states (Part C): CONNECTING -> CONNECTED, or
    DEGRADED (heartbeat missed, reconnecting) -> DISCONNECTED (gave up) ->
    HALTED (a hard breaker fired, e.g. LIVE_ACCOUNT_BLOCKED - deliberately
    NOT auto-reconnected). No order is ever submitted while `state()` is
    anything other than CONNECTED.
    """

    # Seconds to wait for TWS's connection handshake (nextValidId) and for
    # its account-summary stream to finish (accountSummaryEnd) before
    # treating either as failed. TWS normally answers both in well under a
    # second over localhost; a long wait here would only delay correctly
    # detecting a dead/unreachable TWS, never help a real one connect.
    _HANDSHAKE_TIMEOUT_SECONDS = 10
    _ACCOUNT_SUMMARY_TIMEOUT_SECONDS = 10
    _POSITIONS_TIMEOUT_SECONDS = 10
    _OPEN_ORDERS_TIMEOUT_SECONDS = 10

    def __init__(self, config: IBKRConfig | None = None):
        self.config = config or IBKRConfig.from_env()
        self._state = CONNECTION_DISCONNECTED
        self._app = None
        self._wrapper = None
        self._network_thread: threading.Thread | None = None
        self._verified_account: AccountSummary | None = None
        self._next_order_id: int | None = None

    def connection_state(self) -> str:
        return self._state

    def connect(self) -> None:
        """Connects, then IMMEDIATELY verifies the account is PAPER before
        `_state` is ever set to CONNECTED - a caller that only checks
        `connection_state() == CONNECTED` can never observe a connected-but-
        unverified state."""
        self._state = CONNECTION_CONNECTING
        try:
            self._app = self._build_app()
            self._app.connect(self.config.host, self.config.port, self.config.client_id)

            self._network_thread = threading.Thread(target=self._app.run, daemon=True, name="ibkr-client-network")
            self._network_thread.start()

            if not self._wrapper.connected_event.wait(self._HANDSHAKE_TIMEOUT_SECONDS):
                raise IBKRConnectionError(
                    f"No response from TWS/Gateway at {self.config.host}:{self.config.port} within "
                    f"{self._HANDSHAKE_TIMEOUT_SECONDS}s (nextValidId never received) - confirm TWS is "
                    "running, API access is enabled (Global Configuration -> API -> Settings), and the "
                    "socket port matches."
                )
            if self._wrapper.error_messages:
                code, text = self._wrapper.error_messages[0]
                raise IBKRConnectionError(f"TWS reported error {code} during connect: {text}")

            account_id, reported_mode = self._fetch_account_identity()
            verified = verify_paper_account(account_id, reported_mode, self.config.port, self.config.expected_account_mode)
            self._verified_account = _with_account_values(verified, self._wrapper.account_values)
            self._next_order_id = self._wrapper.next_order_id
            self._state = CONNECTION_CONNECTED
        except AccountModeError:
            self._state = CONNECTION_HALTED
            self.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001 - any other connection failure degrades, never crashes the caller
            self._state = CONNECTION_DISCONNECTED
            self.disconnect()
            raise IBKRConnectionError(str(exc)) from exc

    def _build_app(self) -> Any:
        """Lazy `ibapi` import - see module docstring. `_IBWrapper`/`_IBApp`
        are defined inside this method (not at module level) so importing
        `ibkr_client.py` itself never requires `ibapi` to be installed -
        only actually calling `connect()` does."""
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper

        class _IBWrapper(EWrapper):
            def __init__(self) -> None:
                EWrapper.__init__(self)
                self.connected_event = threading.Event()
                self.account_summary_event = threading.Event()
                self.positions_end_event = threading.Event()
                self.open_orders_end_event = threading.Event()
                self.managed_accounts: list[str] = []
                self.account_values: dict[str, str] = {}
                self.error_messages: list[tuple[int, str]] = []
                self.raw_positions: list[tuple[str, float, float]] = []
                # Keyed by str(orderId); accumulated from BOTH openOrder (ticker/
                # side/type/quantity/limit price) and orderStatus (live fill
                # progress) since neither callback alone carries every field
                # open_orders()/get_order() need - see module docstring.
                self.raw_orders: dict[str, dict[str, Any]] = {}
                self.next_order_id: int | None = None

            def nextValidId(self, orderId: int) -> None:  # noqa: N802 - ibapi's own callback name
                self.next_order_id = orderId
                self.connected_event.set()

            def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
                self.managed_accounts = [a for a in accountsList.split(",") if a]

            def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
                self.account_values[tag] = value

            def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
                self.account_summary_event.set()

            def position(self, account: str, contract: Any, position: float, avgCost: float) -> None:  # noqa: N802
                self.raw_positions.append((contract.symbol, float(position), float(avgCost)))

            def positionEnd(self) -> None:  # noqa: N802
                self.positions_end_event.set()

            def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:  # noqa: N802
                key = str(orderId)
                entry = self.raw_orders.setdefault(key, {})
                entry.update(
                    {
                        "broker_order_id": key,
                        "perm_id": str(order.permId) if getattr(order, "permId", None) else entry.get("perm_id"),
                        "ticker": contract.symbol,
                        "side": order.action,
                        "order_type": order.orderType,
                        "quantity": float(order.totalQuantity),
                        "limit_price": float(order.lmtPrice) if getattr(order, "lmtPrice", None) else None,
                        "parent_id": str(order.parentId) if getattr(order, "parentId", None) else entry.get("parent_id"),
                        # openOrder's own orderState carries a status too, but
                        # orderStatus is the live-update channel - never let an
                        # older openOrder snapshot overwrite a status orderStatus
                        # already reported more recently.
                        "status": entry.get("status") or getattr(orderState, "status", None) or "Unknown",
                        "filled_quantity": entry.get("filled_quantity", 0.0),
                        "remaining_quantity": entry.get("remaining_quantity", float(order.totalQuantity)),
                        "avg_fill_price": entry.get("avg_fill_price"),
                    }
                )

            def orderStatus(  # noqa: N802
                self, orderId: int, status: str, filled: float, remaining: float, avgFillPrice: float,
                permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float,
            ) -> None:
                key = str(orderId)
                entry = self.raw_orders.setdefault(key, {"broker_order_id": key, "ticker": None, "side": None, "order_type": None, "quantity": float(filled) + float(remaining), "limit_price": None, "parent_id": None})
                entry.update(
                    {
                        "status": status,
                        "filled_quantity": float(filled),
                        "remaining_quantity": float(remaining),
                        "avg_fill_price": float(avgFillPrice) if avgFillPrice else entry.get("avg_fill_price"),
                        "perm_id": str(permId) if permId else entry.get("perm_id"),
                        "parent_id": str(parentId) if parentId else entry.get("parent_id"),
                    }
                )

            def openOrderEnd(self) -> None:  # noqa: N802
                self.open_orders_end_event.set()

            def error(self, reqId, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802
                # ibapi reports plenty of benign informational "errors" (e.g.
                # market-data farm connection notices) through this same
                # callback - only codes below 1000 are real request/connection
                # errors worth surfacing to connect()'s caller.
                if errorCode < 1000:
                    self.error_messages.append((errorCode, errorString))

        class _IBApp(EClient):
            def __init__(self, wrapper: "_IBWrapper") -> None:
                EClient.__init__(self, wrapper)

        wrapper = _IBWrapper()
        self._wrapper = wrapper
        return _IBApp(wrapper)

    def _fetch_account_identity(self) -> tuple[str | None, str | None]:
        """Account id comes from TWS's own `managedAccounts` callback (sent
        automatically right after the connection handshake - no explicit
        request needed). Reported mode is derived from that id via
        `classify_account_id()`: the TWS API has no separate "is this
        account paper or live" field - the DU/DF-vs-U id prefix IS the
        documented signal IBKR itself expects client applications to use."""
        account_id = self._wrapper.managed_accounts[0] if self._wrapper.managed_accounts else None
        if not account_id:
            return None, None

        self._app.reqAccountSummary(9001, "All", "NetLiquidation,AvailableFunds,BuyingPower")
        self._wrapper.account_summary_event.wait(self._ACCOUNT_SUMMARY_TIMEOUT_SECONDS)
        try:
            self._app.cancelAccountSummary(9001)
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            pass

        return account_id, classify_account_id(account_id)

    def disconnect(self) -> None:
        if self._app is not None:
            try:
                self._app.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if self._network_thread is not None and self._network_thread.is_alive():
            self._network_thread.join(timeout=5)
        self._state = CONNECTION_DISCONNECTED
        self._verified_account = None

    def account_summary(self) -> AccountSummary:
        if self._verified_account is None:
            raise AccountModeError("ACCOUNT_MODE_UNVERIFIED: no verified account - connect() must succeed first.")
        return self._verified_account

    def _require_connected(self, action: str) -> None:
        if self._state != CONNECTION_CONNECTED:
            raise IBKRConnectionError(f"Cannot {action} while connection_state() is '{self._state}', not CONNECTED.")

    def positions(self) -> list[BrokerPosition]:
        self._require_connected("read positions")
        self._wrapper.raw_positions = []
        self._wrapper.positions_end_event.clear()
        self._app.reqPositions()
        if not self._wrapper.positions_end_event.wait(self._POSITIONS_TIMEOUT_SECONDS):
            raise IBKRConnectionError(f"Timed out waiting for TWS to finish reporting positions (positionEnd never received within {self._POSITIONS_TIMEOUT_SECONDS}s).")
        try:
            self._app.cancelPositions()
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            pass
        return [BrokerPosition(ticker=ticker, quantity=qty, avg_cost=avg_cost) for ticker, qty, avg_cost in self._wrapper.raw_positions]

    def _refresh_open_orders(self) -> None:
        """Re-queries TWS for every currently open order (`reqAllOpenOrders`
        - scoped to this login, not just this API session's own client id)
        and waits for `openOrderEnd` before returning. `open_orders()` and
        `get_order()` both call this: correctness over polling efficiency
        is the right tradeoff for this phase's read-only scope, since
        nothing yet submits orders through this client to poll repeatedly
        at volume (see module docstring)."""
        self._require_connected("read open orders")
        self._wrapper.open_orders_end_event.clear()
        self._app.reqAllOpenOrders()
        if not self._wrapper.open_orders_end_event.wait(self._OPEN_ORDERS_TIMEOUT_SECONDS):
            raise IBKRConnectionError(f"Timed out waiting for TWS to finish reporting open orders (openOrderEnd never received within {self._OPEN_ORDERS_TIMEOUT_SECONDS}s).")

    def open_orders(self) -> list[BrokerOrder]:
        self._refresh_open_orders()
        return [_order_from_raw(entry) for entry in self._wrapper.raw_orders.values() if entry.get("status") not in ("Filled", "Cancelled", "Rejected")]

    def get_order(self, broker_order_id: str) -> BrokerOrder | None:
        self._refresh_open_orders()
        entry = self._wrapper.raw_orders.get(broker_order_id)
        return _order_from_raw(entry) if entry is not None else None

    def _reserve_order_id(self) -> int:
        """IBKR requires a strictly increasing, never-reused order id per
        client id for every NEW order - `nextValidId()`'s value is only
        the first one; every order placed after that must use the next
        integer, tracked locally (ibapi does not do this bookkeeping for
        you)."""
        if self._next_order_id is None:
            raise IBKRConnectionError("No order id available - connect() must succeed (and report nextValidId) before any order can be submitted.")
        order_id = self._next_order_id
        self._next_order_id += 1
        return order_id

    def _build_stock_contract(self, ticker: str) -> Any:
        from ibapi.contract import Contract

        contract = Contract()
        contract.symbol = ticker
        contract.secType = _STOCK_SEC_TYPE
        contract.exchange = _STOCK_EXCHANGE
        contract.currency = _STOCK_CURRENCY
        return contract

    def _build_ibkr_order(self, side: str, order_type: str, quantity: float, price: float) -> Any:
        from ibapi.order import Order

        order = Order()
        order.action = side  # "BUY" or "SELL" only - enforced upstream by order_state.validate_intent()
        order.orderType = _ORDER_TYPE_TO_IBKR.get(order_type, order_type)
        order.totalQuantity = quantity
        order.tif = "DAY"
        order.outsideRth = False  # Part N: regular trading hours only, never pre/after-market
        order.transmit = True
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        if order.orderType == "LMT":
            order.lmtPrice = price
        elif order.orderType == "STP":
            order.auxPrice = price
        return order

    def submit_order(self, intent: Any) -> BrokerOrder:
        """Places exactly one order - the entry, or (from `order_manager.
        _sync_protection`) a protective STOP or target LIMIT exit leg.
        `intent.entry_price` is, for every `OrderIntent` this codebase
        constructs (including an exit leg - see `order_manager.
        _exit_intent()`), simply "the price this specific order should
        use"; `intent.order_type` says whether that's a limit or a stop
        price. Returns an immediate, locally-built snapshot (status
        "Submitted") - the real status/fill comes later from `get_order()`,
        exactly like `FakeBroker.submit_order()` already behaves."""
        self._require_connected("submit an order")

        order_id = self._reserve_order_id()
        contract = self._build_stock_contract(intent.ticker)
        order = self._build_ibkr_order(intent.side, intent.order_type, intent.quantity, intent.entry_price)
        self._app.placeOrder(order_id, contract, order)

        return BrokerOrder(
            broker_order_id=str(order_id), perm_id=None, ticker=intent.ticker, side=intent.side,
            order_type=intent.order_type, quantity=intent.quantity,
            limit_price=intent.entry_price if intent.order_type == order_state.ORDER_TYPE_LIMIT else None,
            status="Submitted", filled_quantity=0.0, remaining_quantity=intent.quantity,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_connected("cancel an order")
        order_id = int(broker_order_id)
        try:
            from ibapi.order_cancel import OrderCancel

            self._app.cancelOrder(order_id, OrderCancel())
        except ImportError:
            # Older ibapi versions take a manual-cancel-reason string
            # instead of an OrderCancel object - support both rather than
            # pinning a minimum ibapi version.
            self._app.cancelOrder(order_id, "")
        return True

    def replace_order(self, broker_order_id: str, **changes: Any) -> BrokerOrder:
        """IBKR has no separate "modify" call - re-submitting `placeOrder`
        with the SAME order id against an order TWS still has open is its
        documented modify convention. Used by `order_manager._sync_
        protection()` to resize an existing stop/target when a partial
        fill's protected quantity grows."""
        self._require_connected("replace an order")
        entry = self._wrapper.raw_orders.get(broker_order_id)
        if entry is None:
            raise IBKRConnectionError(f"Cannot replace unknown order {broker_order_id} - no prior record of it from this session.")

        quantity = changes.get("quantity", entry.get("quantity"))
        price = changes.get("limit_price", entry.get("limit_price"))
        contract = self._build_stock_contract(entry.get("ticker") or "")
        order = self._build_ibkr_order(entry.get("side") or order_state.SIDE_BUY, entry.get("order_type") or order_state.ORDER_TYPE_LIMIT, quantity, price)

        self._app.placeOrder(int(broker_order_id), contract, order)
        return _order_from_raw({**entry, "quantity": quantity, "limit_price": price, "remaining_quantity": quantity})

    def executions(self) -> list[BrokerExecution]:
        raise NotImplementedError("Populated by the concrete EWrapper 'execDetails' callback.")


def main() -> int:
    """`python -m src.execution.ibkr_client` - the safest possible
    connection check (Part B/Y). Connects, verifies the account is PAPER,
    prints a safe summary (no secrets, no account id beyond what's needed
    to show PAPER/LIVE/UNKNOWN), disconnects. **Never places an order and
    never reads `execution.mode` from config.yaml** - running this command
    itself has no side effect on the rest of the system either way."""
    from ..utils import load_env

    load_env()
    config = IBKRConfig.from_env()
    client = IBKRClient(config)

    print(f"Connecting to {config.host}:{config.port} (client id {config.client_id})...")
    try:
        client.connect()
    except AccountModeError as exc:
        print("connected: no")
        print("account type: BLOCKED")
        print(f"reason: {exc}")
        print("execution mode: DRY_RUN (unaffected by this check)")
        print("live path available: no")
        return 1
    except IBKRConnectionError as exc:
        print("connected: no")
        print(f"reason: {exc}")
        print("account type: unknown")
        print("execution mode: DRY_RUN (unaffected by this check)")
        print("live path available: no")
        return 1

    try:
        account = client.account_summary()
        print("connected: yes")
        print(f"account type: {account.account_mode.lower()}")
        print(f"account id prefix: {account.account_id[:2]}**" if account.account_id else "account id prefix: n/a")
        print(f"net liquidation: {account.net_liquidation}")
        print(f"available funds: {account.available_funds}")
        print("execution mode: DRY_RUN (unaffected by this check - this command never places an order)")
        print("live path available: no")

        print("\npositions (read-only):")
        positions = client.positions()
        if not positions:
            print("  none")
        for p in positions:
            print(f"  {p.ticker}: {p.quantity} shares @ avg cost {p.avg_cost}")

        print("\nopen orders (read-only):")
        orders = client.open_orders()
        if not orders:
            print("  none")
        for o in orders:
            print(f"  {o.ticker} {o.side} {o.quantity} {o.order_type} - status {o.status} (filled {o.filled_quantity}/{o.quantity})")
    finally:
        client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
