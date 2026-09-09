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
"""

from __future__ import annotations

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

    def __init__(self, config: IBKRConfig | None = None):
        self.config = config or IBKRConfig.from_env()
        self._state = CONNECTION_DISCONNECTED
        self._app = None
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
            account_id, reported_mode = self._fetch_account_identity()
            self._verified_account = verify_paper_account(account_id, reported_mode, self.config.port, self.config.expected_account_mode)
            self._state = CONNECTION_CONNECTED
        except AccountModeError:
            self._state = CONNECTION_HALTED
            self.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001 - any other connection failure degrades, never crashes the caller
            self._state = CONNECTION_DISCONNECTED
            raise IBKRConnectionError(str(exc)) from exc

    def _build_app(self) -> Any:
        """Lazy `ibapi` import - see module docstring. Only reached when a
        real connection is actually attempted."""
        from ibapi.client import EClient  # noqa: F401
        from ibapi.wrapper import EWrapper  # noqa: F401

        raise NotImplementedError(
            "IBKRClient's real EClient/EWrapper wiring is intentionally not exercised in this "
            "environment (no TWS/IB Gateway is reachable here) - implement the concrete "
            "EWrapper callbacks (nextValidId, accountSummary, position, openOrder, "
            "orderStatus, execDetails, error) against your local TWS Paper session before "
            "using this class outside of tests. See README 'IBKR PAPER connection'."
        )

    def _fetch_account_identity(self) -> tuple[str | None, str | None]:
        raise NotImplementedError("Populated by the concrete EWrapper callbacks - see _build_app().")

    def disconnect(self) -> None:
        if self._app is not None:
            try:
                self._app.disconnect()
            except Exception:  # noqa: BLE001
                pass
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
