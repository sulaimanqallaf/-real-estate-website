"""Phase 7 Part Z - Connection safety: paper accepted, live rejected, unknown
rejected, missing account identity fails closed, live PORT blocks regardless
of what the account reports, and there is no override flag anywhere in
`verify_paper_account`'s signature.
"""

import inspect
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import ibkr_client
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, ACCOUNT_MODE_UNKNOWN


def test_paper_account_on_paper_port_is_accepted():
    summary = ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=7497)
    assert summary.account_mode == ACCOUNT_MODE_PAPER
    assert summary.account_id == "DU123456"


def test_live_account_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="LIVE_ACCOUNT_BLOCKED"):
        ibkr_client.verify_paper_account("U123456", ACCOUNT_MODE_LIVE, configured_port=7497)


def test_unknown_account_mode_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_UNKNOWN, configured_port=7497)


def test_missing_reported_mode_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", None, configured_port=7497)


def test_missing_account_id_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account(None, ACCOUNT_MODE_PAPER, configured_port=7497)


def test_empty_string_account_id_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("", ACCOUNT_MODE_PAPER, configured_port=7497)


def test_unrecognized_reported_mode_string_is_rejected():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", "SOMETHING_ELSE", configured_port=7497)


@pytest.mark.parametrize("live_port", [ibkr_client.DEFAULT_TWS_LIVE_PORT, ibkr_client.DEFAULT_GATEWAY_LIVE_PORT])
def test_live_port_blocks_even_if_account_claims_paper(live_port):
    """Defense-in-depth (Part B): a misconfigured port is itself a live-
    trading risk - it must block regardless of what the account query
    would have said, because a real IBKR session bound to a live port IS
    a live-trading session."""
    with pytest.raises(ibkr_client.AccountModeError, match="LIVE_ACCOUNT_BLOCKED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=live_port)


@pytest.mark.parametrize("paper_port", [ibkr_client.DEFAULT_TWS_PAPER_PORT, ibkr_client.DEFAULT_GATEWAY_PAPER_PORT])
def test_documented_paper_ports_are_not_blocked_by_port_check(paper_port):
    summary = ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=paper_port)
    assert summary.account_mode == ACCOUNT_MODE_PAPER


def test_non_paper_expected_mode_is_rejected_there_is_no_supported_alternative():
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        ibkr_client.verify_paper_account("DU123456", ACCOUNT_MODE_PAPER, configured_port=7497, expected_mode=ACCOUNT_MODE_LIVE)


def test_verify_paper_account_signature_has_no_override_parameter():
    """Programmatically prove there is no force/override/allow_live
    parameter anywhere on this function - the one thing Part B explicitly
    forbids."""
    params = set(inspect.signature(ibkr_client.verify_paper_account).parameters.keys())
    forbidden = {"force", "override", "allow_live", "skip_check", "bypass"}
    assert not (params & forbidden)


def test_classify_account_id_heuristic_du_prefix_is_paper():
    assert ibkr_client.classify_account_id("DU123456") == ACCOUNT_MODE_PAPER


def test_classify_account_id_heuristic_df_prefix_is_paper():
    assert ibkr_client.classify_account_id("DF987654") == ACCOUNT_MODE_PAPER


def test_classify_account_id_heuristic_u_prefix_is_live():
    assert ibkr_client.classify_account_id("U123456") == ACCOUNT_MODE_LIVE


def test_classify_account_id_heuristic_unrecognized_prefix_is_unknown():
    assert ibkr_client.classify_account_id("XYZ123") == ACCOUNT_MODE_UNKNOWN


def test_ibkr_config_from_env_defaults_to_paper_port_and_mode(monkeypatch):
    monkeypatch.delenv("IBKR_HOST", raising=False)
    monkeypatch.delenv("IBKR_PORT", raising=False)
    monkeypatch.delenv("IBKR_CLIENT_ID", raising=False)
    monkeypatch.delenv("IBKR_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("IBKR_EXPECTED_ACCOUNT_MODE", raising=False)
    config = ibkr_client.IBKRConfig.from_env()
    assert config.host == "127.0.0.1"
    assert config.port == ibkr_client.DEFAULT_TWS_PAPER_PORT
    assert config.expected_account_mode == ACCOUNT_MODE_PAPER


def test_ibkr_client_never_reaches_connected_without_verification(monkeypatch):
    """connect() must raise and never set _state to CONNECTED when the
    connection attempt itself fails. Mocks `_build_app()` directly rather
    than relying on `ibapi` being unimportable or no TWS being reachable -
    those are both true in the CI sandbox this suite was first written in,
    but neither holds on a developer machine with `ibapi` installed and a
    real TWS/Gateway listening (there, `_build_app()` succeeds and a real
    socket connect is attempted, which can legitimately succeed or fail
    depending on what's actually running - not something a unit test
    should depend on either way)."""
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    monkeypatch.setattr(client, "_build_app", lambda: (_ for _ in ()).throw(RuntimeError("simulated: no TWS/Gateway reachable")))
    with pytest.raises(ibkr_client.IBKRConnectionError):
        client.connect()
    assert client.connection_state() != "CONNECTED"


def test_ibkr_client_connect_failure_after_build_app_also_fails_closed(monkeypatch):
    """Same invariant, but the failure happens one step later - after
    `_build_app()` succeeds and the socket `connect()` call itself raises
    (e.g. TWS refuses the client id, or the port is simply closed)."""
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))

    class _FakeApp:
        def connect(self, host, port, client_id):
            raise ConnectionRefusedError("simulated: TWS refused the connection")

        def disconnect(self):
            pass

    monkeypatch.setattr(client, "_build_app", lambda: _FakeApp())
    with pytest.raises(ibkr_client.IBKRConnectionError):
        client.connect()
    assert client.connection_state() != "CONNECTED"


def test_ibkr_client_account_summary_raises_before_any_connect():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.AccountModeError, match="ACCOUNT_MODE_UNVERIFIED"):
        client.account_summary()


def test_ibkr_client_submit_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError):
        client.submit_order(object())


def test_with_account_values_parses_numeric_tags():
    base = ibkr_client.AccountSummary(account_id="DU1", account_mode=ACCOUNT_MODE_PAPER, net_liquidation=None, available_funds=None, buying_power=None)
    enriched = ibkr_client._with_account_values(base, {"NetLiquidation": "100000.5", "AvailableFunds": "50000", "BuyingPower": "200000"})
    assert enriched.net_liquidation == 100000.5
    assert enriched.available_funds == 50000.0
    assert enriched.buying_power == 200000.0
    assert enriched.account_id == "DU1"
    assert enriched.account_mode == ACCOUNT_MODE_PAPER


def test_with_account_values_tolerates_missing_or_unparseable_tags():
    base = ibkr_client.AccountSummary(account_id="DU1", account_mode=ACCOUNT_MODE_PAPER, net_liquidation=None, available_funds=None, buying_power=None)
    enriched = ibkr_client._with_account_values(base, {"NetLiquidation": "not-a-number"})
    assert enriched.net_liquidation is None
    assert enriched.available_funds is None


def test_main_cli_reports_connection_failure_safely_no_order_placed(monkeypatch, capsys):
    def boom(self):
        raise ibkr_client.IBKRConnectionError("No response from TWS/Gateway at 127.0.0.1:7497 within 10s")

    monkeypatch.setattr(ibkr_client.IBKRClient, "connect", boom)
    exit_code = ibkr_client.main()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "connected: no" in out
    assert "live path available: no" in out


def test_main_cli_reports_account_mode_block_safely(monkeypatch, capsys):
    def boom(self):
        raise ibkr_client.AccountModeError("LIVE_ACCOUNT_BLOCKED: account U123456 reports as LIVE.")

    monkeypatch.setattr(ibkr_client.IBKRClient, "connect", boom)
    exit_code = ibkr_client.main()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "account type: BLOCKED" in out
    assert "live path available: no" in out
    assert "U123456" not in out.split("reason:")[0]  # never printed before the explicit reason line


def test_main_cli_reports_paper_connection_success_never_places_order(monkeypatch, capsys):
    from src.execution.broker import CONNECTION_CONNECTED

    def fake_connect(self):
        self._verified_account = ibkr_client.AccountSummary(account_id="DU1234567", account_mode=ACCOUNT_MODE_PAPER, net_liquidation=100000.0, available_funds=100000.0, buying_power=200000.0)
        self._state = CONNECTION_CONNECTED

    monkeypatch.setattr(ibkr_client.IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(ibkr_client.IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(ibkr_client.IBKRClient, "positions", lambda self: [])
    monkeypatch.setattr(ibkr_client.IBKRClient, "open_orders", lambda self: [])
    exit_code = ibkr_client.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "connected: yes" in out
    assert "account type: paper" in out
    assert "live path available: no" in out
    assert "DU1234567" not in out  # only a 2-char prefix is ever printed, never the full account id


def test_main_cli_prints_positions_and_open_orders_read_only(monkeypatch, capsys):
    from src.execution.broker import CONNECTION_CONNECTED, BrokerOrder, BrokerPosition

    def fake_connect(self):
        self._verified_account = ibkr_client.AccountSummary(account_id="DU1234567", account_mode=ACCOUNT_MODE_PAPER, net_liquidation=100000.0, available_funds=100000.0, buying_power=200000.0)
        self._state = CONNECTION_CONNECTED

    monkeypatch.setattr(ibkr_client.IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(ibkr_client.IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(ibkr_client.IBKRClient, "positions", lambda self: [BrokerPosition(ticker="AMD", quantity=10.0, avg_cost=100.0)])
    monkeypatch.setattr(
        ibkr_client.IBKRClient, "open_orders",
        lambda self: [BrokerOrder(broker_order_id="1", perm_id="1", ticker="MSFT", side="BUY", order_type="LIMIT", quantity=5.0, limit_price=300.0, status="Submitted", filled_quantity=0.0, remaining_quantity=5.0)],
    )
    exit_code = ibkr_client.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "AMD" in out
    assert "MSFT" in out
    assert "Submitted" in out


# --- read-only portfolio state: positions/open_orders/get_order --------------------


def test_order_from_raw_maps_every_field():
    order = ibkr_client._order_from_raw(
        {
            "broker_order_id": "7", "perm_id": "123", "ticker": "AMD", "side": "BUY", "order_type": "LMT",
            "quantity": 10.0, "limit_price": 100.0, "status": "Submitted", "filled_quantity": 3.0,
            "remaining_quantity": 7.0, "avg_fill_price": 99.5, "parent_id": None,
        }
    )
    assert order.broker_order_id == "7"
    assert order.ticker == "AMD"
    assert order.side == "BUY"
    assert order.quantity == 10.0
    assert order.filled_quantity == 3.0
    assert order.remaining_quantity == 7.0
    assert order.status == "Submitted"


def test_order_from_raw_tolerates_missing_optional_fields():
    order = ibkr_client._order_from_raw({"broker_order_id": "7"})
    assert order.ticker == ""
    assert order.side == ""
    assert order.quantity == 0.0
    assert order.status == "Unknown"
    assert order.avg_fill_price is None


def _connected_client_with_fake_app():
    from src.execution.broker import CONNECTION_CONNECTED

    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    client._state = CONNECTION_CONNECTED

    class _FakeWrapper:
        def __init__(self):
            self.raw_positions = []
            self.raw_orders = {}
            self.order_ack_events: dict[str, Any] = {}
            self.order_errors: dict[str, tuple[int, str]] = {}
            self.raw_executions: dict[str, Any] = {}
            self.positions_end_event = _ImmediatelySetEvent()
            self.open_orders_end_event = _ImmediatelySetEvent()
            self.exec_details_end_event = _ImmediatelySetEvent()

    class _ImmediatelySetEvent:
        def clear(self):
            pass

        def set(self):
            pass

        def wait(self, timeout):
            return True

    class _FakeApp:
        def __init__(self, wrapper):
            self._wrapper = wrapper
            self.positions_to_report: list[tuple[str, float, float]] = []
            self.placed_orders: list[tuple[int, Any, Any]] = []
            self.cancelled_orders: list[tuple[int, Any]] = []
            # Test-configurable simulated broker response to the NEXT
            # placeOrder() call - mirrors the real openOrder/orderStatus/
            # error() callbacks that submit_order() now waits on. Default
            # mimics an immediate, ordinary acceptance so every pre-existing
            # test here (written before the ack-wait fix) keeps passing
            # unmodified.
            self.order_status_to_simulate: str | None = "Submitted"  # None = simulate no callback at all (acknowledgement timeout)
            self.order_error_to_simulate: tuple[int, str] | None = None  # (code, text) = simulate an order-specific error() callback instead
            # Test-configurable: executions() to simulate reporting via
            # reqExecutions() - each a dict with execution_id/broker_order_id/
            # ticker/side/shares/price/commission/timestamp keys (commission
            # may be omitted to simulate a never-arriving commissionReport()).
            self.executions_to_report: list[dict[str, Any]] = []

        def reqExecutions(self, reqId, exec_filter):
            for execution in self.executions_to_report:
                entry = self._wrapper.raw_executions.setdefault(execution["execution_id"], {"commission": None})
                entry.update({k: v for k, v in execution.items() if k != "commission"})
                if "commission" in execution:
                    entry["commission"] = execution["commission"]
            self._wrapper.exec_details_end_event.set()

        def reqPositions(self):
            # Simulates the real EWrapper.position() callback having
            # already fired (synchronously, for test purposes) by the time
            # positionEnd()'s wait() returns.
            self._wrapper.raw_positions = list(self.positions_to_report)

        def cancelPositions(self):
            pass

        def reqAllOpenOrders(self):
            pass

        def placeOrder(self, order_id, contract, order):
            self.placed_orders.append((order_id, contract, order))
            key = str(order_id)

            if self.order_error_to_simulate is not None:
                self._wrapper.order_errors[key] = self.order_error_to_simulate
                ack_event = self._wrapper.order_ack_events.get(key)
                if ack_event is not None:
                    ack_event.set()
                return

            if self.order_status_to_simulate is None:
                return  # no callback fires at all - submit_order() must time out

            entry = self._wrapper.raw_orders.setdefault(key, {"broker_order_id": key, "parent_id": None})
            entry.update(
                {
                    "ticker": contract.symbol,
                    "side": order.action,
                    "order_type": order.orderType,
                    "quantity": float(order.totalQuantity),
                    "limit_price": getattr(order, "lmtPrice", None),
                    "status": self.order_status_to_simulate,
                    "filled_quantity": 0.0,
                    "remaining_quantity": float(order.totalQuantity),
                    "avg_fill_price": None,
                }
            )
            ack_event = self._wrapper.order_ack_events.get(key)
            if ack_event is not None:
                ack_event.set()

        def cancelOrder(self, order_id, order_cancel):
            self.cancelled_orders.append((order_id, order_cancel))

    wrapper = _FakeWrapper()
    client._wrapper = wrapper
    client._app = _FakeApp(wrapper)
    client._next_order_id = 1000
    return client


def _install_fake_ibapi_order_types(monkeypatch):
    """`ibapi` is not installed in this sandbox (see module docstring) -
    `submit_order()`/`replace_order()`/`cancel_order()` do
    `from ibapi.contract import Contract` / `from ibapi.order import
    Order` / `from ibapi.order_cancel import OrderCancel` internally.
    Installing minimal fake modules into sys.modules lets these tests
    exercise the REAL field-mapping code (what gets set on a Contract/
    Order) rather than mocking submit_order() itself into a no-op -
    ibapi's own Contract/Order are themselves just plain attribute-bag
    classes with no validation, so a bare class is a faithful stand-in."""
    import types

    class _FakeContract:
        pass

    class _FakeOrder:
        pass

    class _FakeOrderCancel:
        pass

    class _FakeExecutionFilter:
        pass

    fake_ibapi = types.ModuleType("ibapi")
    fake_contract_mod = types.ModuleType("ibapi.contract")
    fake_contract_mod.Contract = _FakeContract
    fake_order_mod = types.ModuleType("ibapi.order")
    fake_order_mod.Order = _FakeOrder
    fake_order_cancel_mod = types.ModuleType("ibapi.order_cancel")
    fake_order_cancel_mod.OrderCancel = _FakeOrderCancel
    fake_execution_mod = types.ModuleType("ibapi.execution")
    fake_execution_mod.ExecutionFilter = _FakeExecutionFilter

    monkeypatch.setitem(sys.modules, "ibapi", fake_ibapi)
    monkeypatch.setitem(sys.modules, "ibapi.contract", fake_contract_mod)
    monkeypatch.setitem(sys.modules, "ibapi.order", fake_order_mod)
    monkeypatch.setitem(sys.modules, "ibapi.order_cancel", fake_order_cancel_mod)
    monkeypatch.setitem(sys.modules, "ibapi.execution", fake_execution_mod)


def test_positions_returns_empty_list_when_broker_reports_none():
    client = _connected_client_with_fake_app()
    assert client.positions() == []


def test_positions_maps_broker_reported_positions():
    client = _connected_client_with_fake_app()
    client._app.positions_to_report = [("AMD", 10.0, 100.0), ("MSFT", -5.0, 300.0)]
    positions = client.positions()
    assert len(positions) == 2
    assert positions[0].ticker == "AMD"
    assert positions[0].quantity == 10.0
    assert positions[0].avg_cost == 100.0


def test_positions_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.positions()


def test_open_orders_excludes_terminal_statuses():
    client = _connected_client_with_fake_app()
    client._wrapper.raw_orders = {
        "1": {"broker_order_id": "1", "ticker": "AMD", "side": "BUY", "order_type": "LMT", "quantity": 10.0, "limit_price": 100.0, "status": "Submitted", "filled_quantity": 0.0, "remaining_quantity": 10.0, "avg_fill_price": None, "parent_id": None},
        "2": {"broker_order_id": "2", "ticker": "MSFT", "side": "BUY", "order_type": "LMT", "quantity": 5.0, "limit_price": 300.0, "status": "Filled", "filled_quantity": 5.0, "remaining_quantity": 0.0, "avg_fill_price": 300.0, "parent_id": None},
    }
    orders = client.open_orders()
    assert len(orders) == 1
    assert orders[0].ticker == "AMD"


def test_open_orders_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.open_orders()


def test_get_order_returns_matching_order_regardless_of_terminal_state():
    client = _connected_client_with_fake_app()
    client._wrapper.raw_orders = {
        "2": {"broker_order_id": "2", "ticker": "MSFT", "side": "BUY", "order_type": "LMT", "quantity": 5.0, "limit_price": 300.0, "status": "Filled", "filled_quantity": 5.0, "remaining_quantity": 0.0, "avg_fill_price": 300.0, "parent_id": None},
    }
    order = client.get_order("2")
    assert order is not None
    assert order.status == "Filled"


def test_get_order_returns_none_for_unknown_id():
    client = _connected_client_with_fake_app()
    assert client.get_order("does-not-exist") is None


def test_get_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.get_order("1")


def test_positions_raises_on_timeout():
    client = _connected_client_with_fake_app()

    class _NeverSetEvent:
        def clear(self):
            pass

        def wait(self, timeout):
            return False

    client._wrapper.positions_end_event = _NeverSetEvent()
    with pytest.raises(ibkr_client.IBKRConnectionError, match="Timed out"):
        client.positions()


def test_open_orders_raises_on_timeout():
    client = _connected_client_with_fake_app()

    class _NeverSetEvent:
        def clear(self):
            pass

        def wait(self, timeout):
            return False

    client._wrapper.open_orders_end_event = _NeverSetEvent()
    with pytest.raises(ibkr_client.IBKRConnectionError, match="Timed out"):
        client.open_orders()


# --- order submission/modification/cancellation --------------------------------------


def _make_intent(**overrides):
    from datetime import datetime, timezone

    from src.execution import order_state

    base = dict(
        intent_id=order_state.new_intent_id(), ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


def test_submit_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.submit_order(_make_intent())


def test_submit_order_raises_without_a_reserved_order_id():
    """connect() always sets _next_order_id from nextValidId before
    reaching CONNECTED - this proves submit_order() itself still refuses
    to guess an order id if that invariant were ever violated."""
    from src.execution.broker import CONNECTION_CONNECTED

    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    client._state = CONNECTION_CONNECTED
    client._app = object()  # never reached - _reserve_order_id() raises first
    with pytest.raises(ibkr_client.IBKRConnectionError, match="No order id available"):
        client.submit_order(_make_intent())


def test_submit_order_places_a_limit_buy_with_correct_fields(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    intent = _make_intent(ticker="AMD", side="BUY", quantity=10, order_type="LIMIT", entry_price=100.0)
    result = client.submit_order(intent)

    assert len(client._app.placed_orders) == 1
    order_id, contract, order = client._app.placed_orders[0]
    assert order_id == 1000  # the reserved next_order_id
    assert contract.symbol == "AMD"
    assert contract.secType == "STK"
    assert contract.exchange == "SMART"
    assert contract.currency == "USD"
    assert order.action == "BUY"
    assert order.orderType == "LMT"
    assert order.totalQuantity == 10
    assert order.lmtPrice == 100.0
    assert order.transmit is True
    assert order.outsideRth is False

    assert result.broker_order_id == "1000"
    assert result.status == "Submitted"
    assert result.ticker == "AMD"


def test_submit_order_places_a_stop_sell_with_aux_price(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    intent = _make_intent(ticker="AMD", side="SELL", quantity=10, order_type="STOP", entry_price=95.0)
    client.submit_order(intent)

    _, _, order = client._app.placed_orders[0]
    assert order.action == "SELL"
    assert order.orderType == "STP"
    assert order.auxPrice == 95.0
    assert not hasattr(order, "lmtPrice") or order.lmtPrice is None


def test_submit_order_sets_oca_group_and_type_from_intent_metadata(monkeypatch):
    """GitHub Issue #1 P0: a protective stop/target leg's OrderIntent.
    metadata carries oca_group/oca_type (set by order_manager._exit_
    intent()) - submit_order() must set them on the real ibapi Order so
    TWS enforces the linkage server-side."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    intent = _make_intent(side="SELL", order_type="STOP", entry_price=95.0, metadata={"oca_group": "oca_abc123", "oca_type": 1})
    client.submit_order(intent)

    _, _, order = client._app.placed_orders[0]
    assert order.ocaGroup == "oca_abc123"
    assert order.ocaType == 1


def test_submit_order_leaves_oca_fields_unset_for_an_entry_order_with_no_metadata(monkeypatch):
    """A plain entry order (no oca_group in metadata) must never get a
    fabricated OCA linkage - absence of metadata means absence of the
    ocaGroup attribute on the built Order, not an empty-string group."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    intent = _make_intent(side="BUY", order_type="LIMIT", entry_price=100.0)
    client.submit_order(intent)

    _, _, order = client._app.placed_orders[0]
    assert not hasattr(order, "ocaGroup")


def test_replace_order_preserves_the_oca_group_from_the_raw_snapshot(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._wrapper.raw_orders["1000"] = {
        "broker_order_id": "1000", "ticker": "AMD", "side": "SELL", "order_type": "STOP",
        "quantity": 4.0, "limit_price": 95.0, "status": "Submitted",
        "filled_quantity": 0.0, "remaining_quantity": 4.0, "avg_fill_price": None, "parent_id": None,
        "oca_group": "oca_abc123", "oca_type": 1,
    }

    client.replace_order("1000", quantity=10)

    _, _, order = client._app.placed_orders[0]
    assert order.ocaGroup == "oca_abc123"
    assert order.ocaType == 1


def test_open_order_callback_preserves_oca_group_reported_back_by_tws(monkeypatch):
    """Exercises the REAL `_IBWrapper.openOrder()` callback (built via the
    real, lazily-imported `_build_app()`, not the `_FakeWrapper` test
    double used elsewhere in this file) to prove it actually records
    whatever ocaGroup/ocaType TWS echoes back on an order."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))

    class _FakeEClient:
        def __init__(self, wrapper):
            pass

    monkeypatch.setitem(sys.modules, "ibapi.client", type(sys)("ibapi.client"))
    sys.modules["ibapi.client"].EClient = _FakeEClient
    monkeypatch.setitem(sys.modules, "ibapi.wrapper", type(sys)("ibapi.wrapper"))

    class _FakeEWrapper:
        def __init__(self):
            pass

    sys.modules["ibapi.wrapper"].EWrapper = _FakeEWrapper

    client._build_app()
    wrapper = client._wrapper

    class _FakeOrderState:
        status = "Submitted"

    class _FakeOrderWithOca:
        permId = None
        action = "SELL"
        orderType = "STP"
        totalQuantity = 10.0
        lmtPrice = None
        parentId = None
        ocaGroup = "oca_xyz"
        ocaType = 1

    class _FakeContract:
        symbol = "AMD"

    wrapper.openOrder(2000, _FakeContract(), _FakeOrderWithOca(), _FakeOrderState())
    assert wrapper.raw_orders["2000"]["oca_group"] == "oca_xyz"
    assert wrapper.raw_orders["2000"]["oca_type"] == 1


def test_submit_order_never_uses_margin_or_options_fields(monkeypatch):
    """There is no code path here that sets a margin/leverage multiplier
    or an options-specific contract field (strike/right/expiry) - this
    test documents that the Contract/Order built are always the plain
    long-stock shape, nothing else."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client.submit_order(_make_intent())

    _, contract, order = client._app.placed_orders[0]
    for forbidden in ("strike", "right", "lastTradeDateOrContractMonth", "multiplier"):
        assert not hasattr(contract, forbidden) or getattr(contract, forbidden) in (None, "", 0)
    for forbidden in ("cashQty", "marginFlexible"):
        assert not hasattr(order, forbidden)


def test_order_ids_increment_sequentially_across_submissions(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    first = client.submit_order(_make_intent(ticker="AMD"))
    second = client.submit_order(_make_intent(ticker="MSFT"))
    assert first.broker_order_id == "1000"
    assert second.broker_order_id == "1001"
    assert len(client._app.placed_orders) == 2


# --- submit_order() waits for real broker acknowledgement (bug fix) -----------------


def test_submit_order_accepts_preSubmitted_status(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_status_to_simulate = "PreSubmitted"

    result = client.submit_order(_make_intent())
    assert result.status == "PreSubmitted"
    assert result.broker_order_id == "1000"


def test_submit_order_accepts_immediate_filled_status(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_status_to_simulate = "Filled"

    result = client.submit_order(_make_intent())
    assert result.status == "Filled"


def test_submit_order_raises_broker_order_rejected_on_explicit_error(monkeypatch):
    """An IBKR order-specific error() callback (e.g. the percentage-
    constraint warning from the user's real bug report) must raise
    BrokerOrderRejected, never return a fabricated 'Submitted' order."""
    from src.execution.broker import BrokerOrderRejected

    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_error_to_simulate = (201, "Order rejected - would exceed percentage constraint")

    with pytest.raises(BrokerOrderRejected, match="percentage constraint"):
        client.submit_order(_make_intent())


def test_submit_order_raises_broker_order_rejected_on_inactive_status(monkeypatch):
    from src.execution.broker import BrokerOrderRejected

    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_status_to_simulate = "Inactive"

    with pytest.raises(BrokerOrderRejected, match="Inactive"):
        client.submit_order(_make_intent())


def test_submit_order_raises_broker_order_rejected_on_cancelled_status(monkeypatch):
    from src.execution.broker import BrokerOrderRejected

    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_status_to_simulate = "Cancelled"

    with pytest.raises(BrokerOrderRejected, match="Cancelled"):
        client.submit_order(_make_intent())


def test_submit_order_times_out_when_no_acknowledgement_arrives(monkeypatch):
    """Fail closed (requirement #9): if TWS never calls back at all,
    submit_order() must raise rather than silently reporting success."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._ORDER_ACK_TIMEOUT_SECONDS = 0.05
    client._app.order_status_to_simulate = None  # simulate no callback firing at all

    with pytest.raises(ibkr_client.IBKRConnectionError, match="Timed out waiting for TWS to acknowledge"):
        client.submit_order(_make_intent())


def test_submit_order_fails_closed_on_unrecognized_status(monkeypatch):
    """An ambiguous status that is neither a known-accepted nor a known-
    rejected one must never be treated as success."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_status_to_simulate = "PendingSubmit"  # not in either accepted or rejected set

    with pytest.raises(ibkr_client.IBKRConnectionError, match="unrecognized status"):
        client.submit_order(_make_intent())


def test_submit_order_never_returns_a_fabricated_order_on_rejection(monkeypatch):
    """The core bug: submit_order() must never report ACKNOWLEDGED/
    Submitted just because placeOrder() was called - this proves no
    BrokerOrder is returned at all on a rejection path (an exception, not
    a return value, is the only way OrderManager can observe a rejection)."""
    from src.execution.broker import BrokerOrderRejected

    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.order_error_to_simulate = (202, "Order rejected")

    try:
        client.submit_order(_make_intent())
        assert False, "expected BrokerOrderRejected to be raised"
    except BrokerOrderRejected:
        pass


def test_submit_order_cleans_up_ack_tracking_after_success(monkeypatch):
    """order_ack_events/order_errors must not accumulate forever - each
    order's tracking entry is removed once submit_order() returns."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    client.submit_order(_make_intent())
    assert client._wrapper.order_ack_events == {}
    assert client._wrapper.order_errors == {}


def test_cancel_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.cancel_order("1000")


def test_cancel_order_calls_cancel_order_with_the_right_id(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    result = client.cancel_order("1000")
    assert result is True
    assert len(client._app.cancelled_orders) == 1
    assert client._app.cancelled_orders[0][0] == 1000


def test_replace_order_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.replace_order("1000", quantity=5)


def test_replace_order_raises_for_an_unknown_order_id(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    with pytest.raises(ibkr_client.IBKRConnectionError, match="unknown order"):
        client.replace_order("9999", quantity=5)


def test_replace_order_resubmits_with_the_same_order_id_and_new_quantity(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._wrapper.raw_orders["1000"] = {
        "broker_order_id": "1000", "ticker": "AMD", "side": "SELL", "order_type": "STOP",
        "quantity": 4.0, "limit_price": 95.0, "status": "Submitted",
        "filled_quantity": 0.0, "remaining_quantity": 4.0, "avg_fill_price": None, "parent_id": None,
    }

    updated = client.replace_order("1000", quantity=10)

    assert len(client._app.placed_orders) == 1
    order_id, contract, order = client._app.placed_orders[0]
    assert order_id == 1000  # same id - a replace, not a new order
    assert contract.symbol == "AMD"
    assert order.action == "SELL"
    assert order.orderType == "STP"
    assert order.totalQuantity == 10
    assert order.auxPrice == 95.0
    assert updated.quantity == 10


# --- executions() / commission (GitHub Issue #1 P0) ---------------------------------


def test_executions_refuses_when_not_connected():
    client = ibkr_client.IBKRClient(ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id=None))
    with pytest.raises(ibkr_client.IBKRConnectionError, match="not CONNECTED"):
        client.executions()


def test_executions_maps_every_field_including_commission(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.executions_to_report = [
        {
            "execution_id": "ex1", "broker_order_id": "1000", "ticker": "AMD", "side": "BOT",
            "shares": 10.0, "price": 100.5, "commission": 1.25,
            "timestamp": ibkr_client.datetime(2026, 9, 9, 14, 30, tzinfo=ibkr_client.timezone.utc),
        },
    ]
    results = client.executions()
    assert len(results) == 1
    exe = results[0]
    assert exe.execution_id == "ex1"
    assert exe.broker_order_id == "1000"
    assert exe.ticker == "AMD"
    assert exe.shares == 10.0
    assert exe.price == 100.5
    assert exe.commission == 1.25


def test_executions_reports_commission_as_none_when_never_received(monkeypatch):
    """commissionReport() is a separate callback from execDetails() -
    IBKR does not guarantee it always arrives. A missing commission must
    be None ("unknown"), never a fabricated 0.0."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._app.executions_to_report = [
        {"execution_id": "ex2", "broker_order_id": "1001", "ticker": "MSFT", "side": "SLD", "shares": 5.0, "price": 300.0, "timestamp": ibkr_client.datetime.now(ibkr_client.timezone.utc)},
    ]
    results = client.executions()
    assert len(results) == 1
    assert results[0].commission is None


def test_executions_deduplicates_by_execution_id(monkeypatch):
    """ibapi can redeliver the same execId - execDetails()/commissionReport()
    merge into the SAME dict entry rather than appending, so a redelivery
    can never produce two BrokerExecutions for one real fill."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    # Simulate execDetails() firing twice for the same execId, as ibapi's
    # own at-least-once delivery can do - the SAME execution reported
    # twice by reqExecutions() must still merge into one entry, not two.
    now = ibkr_client.datetime.now(ibkr_client.timezone.utc)
    duplicate_execution = {
        "execution_id": "ex3", "broker_order_id": "1002", "ticker": "AMD",
        "side": "BOT", "shares": 10.0, "price": 100.0, "commission": 0.5, "timestamp": now,
    }
    client._app.executions_to_report = [duplicate_execution, dict(duplicate_execution)]

    results = client.executions()
    assert len(results) == 1
    assert results[0].execution_id == "ex3"


def test_executions_excludes_a_commission_only_entry_with_no_exec_details(monkeypatch):
    """A commissionReport() that arrives before (or without) its matching
    execDetails() leaves a bare {"commission": ...} placeholder - must
    never be reported as a BrokerExecution with fabricated ticker/side/
    shares/price fields."""
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()
    client._wrapper.raw_executions["ex4"] = {"commission": 2.0}
    results = client.executions()
    assert results == []


def test_executions_raises_on_timeout(monkeypatch):
    _install_fake_ibapi_order_types(monkeypatch)
    client = _connected_client_with_fake_app()

    class _NeverSetEvent:
        def clear(self):
            pass

        def set(self):
            pass

        def wait(self, timeout):
            return False

    client._wrapper.exec_details_end_event = _NeverSetEvent()
    with pytest.raises(ibkr_client.IBKRConnectionError, match="Timed out"):
        client.executions()


def test_parse_ibkr_execution_time_parses_the_documented_format():
    parsed = ibkr_client._parse_ibkr_execution_time("20260909  14:30:00")
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 9
    assert parsed.hour == 14
    assert parsed.minute == 30


def test_parse_ibkr_execution_time_falls_back_to_now_on_garbage_input():
    before = ibkr_client.datetime.now(ibkr_client.timezone.utc)
    parsed = ibkr_client._parse_ibkr_execution_time("not-a-real-timestamp")
    after = ibkr_client.datetime.now(ibkr_client.timezone.utc)
    assert before <= parsed <= after
