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

**Connection + account verification are wired for real (`connect()`,
`account_summary()`); order placement/position/execution tracking are
NOT YET wired** (`positions()`, `open_orders()`, `get_order()`,
`submit_order()`, `cancel_order()`, `replace_order()`, `executions()` all
still raise `NotImplementedError`). This is a deliberate scope cut, not an
oversight: the connection/account-verification code below has never been
run against a real TWS socket from this environment (no IBKR is reachable
from this sandbox), so it is written carefully against the documented
`ibapi` callback contract but UNVERIFIED until exercised on a real machine
- expanding scope to order placement before that first real-world check
would mean writing untestable, safety-critical code two layers deep. Wire
the rest only after `python -m src.execution.ibkr_client` (see `main()`
below) has been confirmed working against your own local TWS Paper
session.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

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

    def __init__(self, config: IBKRConfig | None = None):
        self.config = config or IBKRConfig.from_env()
        self._state = CONNECTION_DISCONNECTED
        self._app = None
        self._wrapper = None
        self._network_thread: threading.Thread | None = None
        self._verified_account: AccountSummary | None = None

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
                self.managed_accounts: list[str] = []
                self.account_values: dict[str, str] = {}
                self.error_messages: list[tuple[int, str]] = []

            def nextValidId(self, orderId: int) -> None:  # noqa: N802 - ibapi's own callback name
                self.connected_event.set()

            def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
                self.managed_accounts = [a for a in accountsList.split(",") if a]

            def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
                self.account_values[tag] = value

            def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
                self.account_summary_event.set()

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

    def positions(self) -> list[BrokerPosition]:
        raise NotImplementedError("Populated by the concrete EWrapper 'position' callback.")

    def open_orders(self) -> list[BrokerOrder]:
        raise NotImplementedError("Populated by the concrete EWrapper 'openOrder'/'orderStatus' callbacks.")

    def get_order(self, broker_order_id: str) -> BrokerOrder | None:
        raise NotImplementedError("Populated by tracking every EWrapper 'orderStatus' callback by order id, regardless of terminal state.")

    def submit_order(self, intent: Any) -> BrokerOrder:
        if self._state != CONNECTION_CONNECTED:
            raise IBKRConnectionError(f"Refusing to submit an order while connection_state() is '{self._state}', not CONNECTED.")
        raise NotImplementedError("Populated by the concrete EClient 'placeOrder' call + order-id sequencing.")

    def cancel_order(self, broker_order_id: str) -> bool:
        raise NotImplementedError("Populated by the concrete EClient 'cancelOrder' call.")

    def replace_order(self, broker_order_id: str, **changes: Any) -> BrokerOrder:
        raise NotImplementedError("Populated by re-submitting 'placeOrder' with the same orderId (IBKR's documented modify semantics).")

    def executions(self) -> list[BrokerExecution]:
        raise NotImplementedError("Populated by the concrete EWrapper 'execDetails' callback.")


def main() -> int:
    """`python -m src.execution.ibkr_client` - the safest possible
    connection check (Part B/Y). Connects, verifies the account is PAPER,
    prints a safe summary (no secrets, no account id beyond what's needed
    to show PAPER/LIVE/UNKNOWN), disconnects. **Never places an order and
    never reads `execution.mode` from config.yaml** - running this command
    itself has no side effect on the rest of the system either way."""
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
        print("execution mode: DRY_RUN (unaffected by this check - this command never places an order)")
        print("live path available: no")
    finally:
        client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
