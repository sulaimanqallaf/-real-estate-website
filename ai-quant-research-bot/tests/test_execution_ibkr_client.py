"""Phase 7 Part Z - Connection safety: paper accepted, live rejected, unknown
rejected, missing account identity fails closed, live PORT blocks regardless
of what the account reports, and there is no override flag anywhere in
`verify_paper_account`'s signature.
"""

import inspect
import sys
from pathlib import Path

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
            self.positions_end_event = _ImmediatelySetEvent()
            self.open_orders_end_event = _ImmediatelySetEvent()

    class _ImmediatelySetEvent:
        def clear(self):
            pass

        def wait(self, timeout):
            return True

    class _FakeApp:
        def __init__(self, wrapper):
            self._wrapper = wrapper
            self.positions_to_report: list[tuple[str, float, float]] = []

        def reqPositions(self):
            # Simulates the real EWrapper.position() callback having
            # already fired (synchronously, for test purposes) by the time
            # positionEnd()'s wait() returns.
            self._wrapper.raw_positions = list(self.positions_to_report)

        def cancelPositions(self):
            pass

        def reqAllOpenOrders(self):
            pass

    wrapper = _FakeWrapper()
    client._wrapper = wrapper
    client._app = _FakeApp(wrapper)
    return client


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
