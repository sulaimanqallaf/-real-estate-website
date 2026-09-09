"""Phase 7 Part Z - Connection + Orders (basic) + Safety, exercised against
`FakeBroker` (the only Broker implementation any automated test in this repo
ever talks to - see src/execution/ibkr_client.py's module docstring for why
IBKRClient itself is never exercised against a real socket here).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution.broker import (
    ACCOUNT_MODE_LIVE,
    ACCOUNT_MODE_PAPER,
    CONNECTION_CONNECTED,
    CONNECTION_DISCONNECTED,
    FakeBroker,
)
from src.execution import order_state


def make_intent(**overrides):
    base = dict(
        intent_id="intent_1", ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=order_state.datetime.now(order_state.timezone.utc),
        account_mode_at_creation="PAPER",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


# --- Connection ----------------------------------------------------------------------


def test_fake_broker_starts_disconnected():
    broker = FakeBroker()
    assert broker.connection_state() == CONNECTION_DISCONNECTED


def test_fake_broker_connect_disconnect():
    broker = FakeBroker()
    broker.connect()
    assert broker.connection_state() == CONNECTION_CONNECTED
    broker.disconnect()
    assert broker.connection_state() == CONNECTION_DISCONNECTED


def test_fake_broker_account_summary_reflects_configured_mode():
    broker = FakeBroker(account_mode=ACCOUNT_MODE_LIVE, account_id="U555")
    summary = broker.account_summary()
    assert summary.account_mode == ACCOUNT_MODE_LIVE
    assert summary.account_id == "U555"


# --- Orders --------------------------------------------------------------------------


def test_submit_order_assigns_sequential_ids_and_records_intent():
    broker = FakeBroker()
    broker.connect()
    intent1 = make_intent(intent_id="intent_1")
    intent2 = make_intent(intent_id="intent_2", ticker="MSFT")
    order1 = broker.submit_order(intent1)
    order2 = broker.submit_order(intent2)
    assert order1.broker_order_id != order2.broker_order_id
    assert broker.submitted_intents == [intent1, intent2]


def test_open_orders_excludes_terminal_states():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent())
    assert order in broker.open_orders()
    broker.cancel_order(order.broker_order_id)
    assert order.broker_order_id not in {o.broker_order_id for o in broker.open_orders()}


def test_simulate_fill_full_updates_position_and_status():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent(quantity=10))
    broker.simulate_fill(order.broker_order_id, shares=10, price=101.0)
    updated = broker._orders[order.broker_order_id]
    assert updated.status == "Filled"
    assert updated.filled_quantity == 10
    positions = {p.ticker: p for p in broker.positions()}
    assert positions["AMD"].quantity == 10


def test_simulate_fill_partial_leaves_status_partially_filled():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent(quantity=10))
    broker.simulate_fill(order.broker_order_id, shares=4, price=101.0)
    updated = broker._orders[order.broker_order_id]
    assert updated.status == "PartiallyFilled"
    assert updated.filled_quantity == 4
    assert updated.remaining_quantity == 6


def test_simulate_reject_marks_order_rejected_and_excludes_from_open_orders():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent())
    broker.simulate_reject(order.broker_order_id)
    assert broker._orders[order.broker_order_id].status == "Rejected"
    assert order.broker_order_id not in {o.broker_order_id for o in broker.open_orders()}


def test_cancel_order_returns_false_for_unknown_id():
    broker = FakeBroker()
    broker.connect()
    assert broker.cancel_order("does-not-exist") is False


def test_replace_order_updates_quantity_without_new_order_id():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent(quantity=10))
    replaced = broker.replace_order(order.broker_order_id, quantity=4)
    assert replaced.broker_order_id == order.broker_order_id
    assert replaced.quantity == 4


# --- Safety: no short, no options, no margin, long-only, no live -------------------


def test_order_intent_side_is_always_buy_for_a_new_entry():
    intent = make_intent()
    assert intent.side == order_state.SIDE_BUY


def test_validate_intent_rejects_a_sell_side_entry_no_short_possible():
    intent = make_intent(side=order_state.SIDE_SELL)
    errors = order_state.validate_intent(intent, config={})
    assert any("long-only" in e for e in errors)


def test_validate_intent_rejects_option_looking_ticker_no_options_possible():
    # An OCC-style option symbol is far longer than a plain equity ticker and
    # is not purely alnum once its full contract spec is included; even the
    # coarse length/alnum guard in order_state.py refuses it.
    intent = make_intent(ticker="AAPL240119C00150000")
    errors = order_state.validate_intent(intent, config={})
    assert any("does not look like a plain equity" in e for e in errors)


def test_order_intent_dataclass_has_no_margin_or_leverage_field():
    # There is deliberately no "margin", "leverage", or "use_margin" field
    # anywhere on OrderIntent - margin usage is impossible to even express.
    field_names = set(order_state.OrderIntent.__dataclass_fields__.keys())
    assert not (field_names & {"margin", "leverage", "use_margin", "buying_power_multiplier"})


def test_validate_intent_rejects_quantity_not_positive():
    intent = make_intent(quantity=0)
    assert any("quantity must be > 0" in e for e in errors_of(intent))


def test_validate_intent_rejects_inconsistent_stop_target():
    intent = make_intent(stop_loss=105.0, target_price=90.0)  # inverted for a long
    errors = order_state.validate_intent(intent, config={})
    assert any("internally consistent" in e for e in errors)


def test_validate_intent_rejects_non_paper_account_mode_at_creation():
    intent = make_intent(account_mode_at_creation=ACCOUNT_MODE_LIVE)
    errors = order_state.validate_intent(intent, config={})
    assert any("not PAPER" in e for e in errors)


def test_validate_intent_enforces_risk_per_trade_limit():
    intent = make_intent(risk_amount=500.0)
    config = {"execution_risk": {"max_risk_per_trade_pct": 0.005}, "risk": {"account_equity": 10_000}}
    errors = order_state.validate_intent(intent, config)
    assert any("exceeds max_risk_per_trade_pct" in e for e in errors)


def test_validate_intent_passes_for_a_clean_paper_long_intent():
    intent = make_intent()
    config = {"execution_risk": {"max_risk_per_trade_pct": 0.05}, "risk": {"account_equity": 10_000}}
    assert order_state.validate_intent(intent, config) == []


def errors_of(intent):
    return order_state.validate_intent(intent, config={})
