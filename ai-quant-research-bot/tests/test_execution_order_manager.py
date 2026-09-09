"""Phase 7 Part Z - Orders (validation, bracket construction, partial fill
handling, broker rejection, cancel flow, fill flow), Idempotency (repeated
run no duplicate, restart no duplicate, broker/local reconciliation via
restore_from_journal_rows), and Journal (state transitions persisted, no
secrets logged).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import order_manager, order_state
from src.execution.broker import FakeBroker


def make_intent(**overrides):
    from datetime import datetime, timezone

    base = dict(
        intent_id=order_state.new_intent_id(), ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
        trade_id="AMD_2026-09-09_aaaaaaaa",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


PERMISSIVE_CONFIG = {"execution_risk": {"max_risk_per_trade_pct": 0.05}, "risk": {"account_equity": 10_000}}


@pytest.fixture
def broker():
    b = FakeBroker()
    b.connect()
    return b


@pytest.fixture
def manager(broker):
    return order_manager.OrderManager(broker, PERMISSIVE_CONFIG)


# --- Orders --------------------------------------------------------------------------


def test_submit_entry_invalid_intent_never_reaches_broker(manager, broker):
    bad_intent = make_intent(quantity=-1)
    managed = manager.submit_entry(bad_intent)
    assert managed.state == order_state.STATE_ERROR
    assert broker.submitted_intents == []


def test_submit_entry_valid_intent_reaches_broker_and_acknowledges(manager, broker):
    managed = manager.submit_entry(make_intent())
    assert managed.state == order_state.STATE_ACKNOWLEDGED
    assert managed.entry_broker_order_id is not None
    assert len(broker.submitted_intents) == 1


def test_broker_rejection_is_reflected_via_poll_entry_fill(manager, broker):
    managed = manager.submit_entry(make_intent())
    broker.simulate_reject(managed.entry_broker_order_id)
    updated = manager.poll_entry_fill(managed.intent.intent_id)
    assert updated.state == order_state.STATE_REJECTED
    assert updated.rejection_reason


def test_cancel_entry_moves_to_cancelled(manager, broker):
    managed = manager.submit_entry(make_intent())
    ok = manager.cancel_entry(managed.intent.intent_id)
    assert ok is True
    assert manager.get(managed.intent.intent_id).state == order_state.STATE_CANCELLED


def test_cancel_entry_returns_false_when_never_submitted_to_broker(manager, broker):
    intent = make_intent()
    managed = order_manager.ManagedOrder(intent=intent, state=order_state.STATE_CREATED)
    manager._managed[intent.intent_id] = managed  # never went through submit_entry - no broker order id yet
    assert manager.cancel_entry(intent.intent_id) is False


# --- Fill flow / partial fills (Part G/H) --------------------------------------------


def test_full_fill_moves_to_filled_and_places_both_exit_legs_at_full_quantity(manager, broker):
    managed = manager.submit_entry(make_intent(quantity=10))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.5)
    updated = manager.poll_entry_fill(managed.intent.intent_id)
    assert updated.state == order_state.STATE_EXIT_PENDING
    assert updated.filled_quantity == 10
    stop_order = broker._orders[updated.stop_broker_order_id]
    target_order = broker._orders[updated.target_broker_order_id]
    assert stop_order.quantity == 10
    assert target_order.quantity == 10
    assert stop_order.side == order_state.SIDE_SELL
    assert target_order.side == order_state.SIDE_SELL


def test_partial_fill_protects_only_filled_shares(manager, broker):
    """Part H: requested 10, filled 4 -> protect only 4, never the full 10."""
    managed = manager.submit_entry(make_intent(quantity=10))
    broker.simulate_fill(managed.entry_broker_order_id, shares=4, price=100.5)
    updated = manager.poll_entry_fill(managed.intent.intent_id)
    assert updated.state == order_state.STATE_PARTIALLY_FILLED
    assert updated.filled_quantity == 4
    stop_order = broker._orders[updated.stop_broker_order_id]
    target_order = broker._orders[updated.target_broker_order_id]
    assert stop_order.quantity == 4
    assert target_order.quantity == 4


def test_partial_fill_then_remaining_fill_adjusts_protection_upward(manager, broker):
    managed = manager.submit_entry(make_intent(quantity=10))
    broker.simulate_fill(managed.entry_broker_order_id, shares=4, price=100.5)
    manager.poll_entry_fill(managed.intent.intent_id)
    broker.simulate_fill(managed.entry_broker_order_id, shares=6, price=100.7)
    updated = manager.poll_entry_fill(managed.intent.intent_id)
    assert updated.state == order_state.STATE_EXIT_PENDING
    assert updated.filled_quantity == 10
    stop_order = broker._orders[updated.stop_broker_order_id]
    target_order = broker._orders[updated.target_broker_order_id]
    assert stop_order.quantity == 10
    assert target_order.quantity == 10
    # replace_order was used, not a second brand-new stop/target order pair.
    assert len([o for o in broker._orders.values() if o.side == order_state.SIDE_SELL]) == 2


# --- Idempotency (Part I) -------------------------------------------------------------


def test_is_duplicate_true_for_same_trade_id_still_active(manager, broker):
    intent1 = make_intent(trade_id="AMD_2026-09-09_aaaaaaaa")
    manager.submit_entry(intent1)
    intent2 = make_intent(intent_id=order_state.new_intent_id(), trade_id="AMD_2026-09-09_aaaaaaaa")
    assert manager.is_duplicate(intent2) is True


def test_is_duplicate_false_once_original_reached_a_terminal_state(manager, broker):
    intent1 = make_intent(trade_id="AMD_2026-09-09_aaaaaaaa")
    managed1 = manager.submit_entry(intent1)
    manager.cancel_entry(managed1.intent.intent_id)
    intent2 = make_intent(intent_id=order_state.new_intent_id(), trade_id="AMD_2026-09-09_aaaaaaaa")
    assert manager.is_duplicate(intent2) is False


def test_is_duplicate_true_for_matching_ticker_strategy_entry_stop_even_without_trade_id(manager, broker):
    intent1 = make_intent(trade_id=None)
    manager.submit_entry(intent1)
    intent2 = make_intent(intent_id=order_state.new_intent_id(), trade_id=None)
    assert manager.is_duplicate(intent2) is True


def test_submit_entry_refuses_a_duplicate_intent(manager, broker):
    intent1 = make_intent()
    manager.submit_entry(intent1)
    intent2 = make_intent(intent_id=order_state.new_intent_id())
    managed2 = manager.submit_entry(intent2)
    assert managed2.state == order_state.STATE_ERROR
    assert "Duplicate" in managed2.rejection_reason
    assert len(broker.submitted_intents) == 1  # the duplicate never reached the broker


def test_restart_no_duplicate_restore_from_journal_rows_blocks_resend(tmp_path, broker):
    journal_path = tmp_path / "executions.jsonl"
    journal = order_manager.ExecutionJournal(journal_path)
    manager1 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    intent = make_intent()
    manager1.submit_entry(intent)

    # Simulate a fresh process: a brand-new OrderManager with empty in-memory
    # state, rehydrated only from the persisted journal.
    manager2 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    manager2.restore_from_journal_rows(journal.read_all())

    same_trade_again = make_intent(intent_id=order_state.new_intent_id())
    assert manager2.is_duplicate(same_trade_again) is True
    managed_again = manager2.submit_entry(same_trade_again)
    assert managed_again.state == order_state.STATE_ERROR
    assert len(broker.submitted_intents) == 1  # still just the one order from before "restart"


# --- Journal (Part U) -----------------------------------------------------------------


def test_journal_persists_every_state_transition(tmp_path, manager, broker):
    journal_path = tmp_path / "executions.jsonl"
    manager.journal = order_manager.ExecutionJournal(journal_path)
    managed = manager.submit_entry(make_intent())
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.5)
    manager.poll_entry_fill(managed.intent.intent_id)

    rows = manager.journal.read_all()
    event_types = [r["type"] for r in rows]
    assert "submitting" in event_types
    assert "acknowledged" in event_types
    assert "fill" in event_types
    assert "protection_synced" in event_types


def test_journal_never_contains_credential_like_fields(tmp_path, manager, broker):
    journal_path = tmp_path / "executions.jsonl"
    manager.journal = order_manager.ExecutionJournal(journal_path)
    manager.submit_entry(make_intent())
    raw_text = journal_path.read_text().lower()
    for forbidden in ("password", "secret", "token", "api_key"):
        assert forbidden not in raw_text


def test_journal_read_all_returns_empty_list_for_nonexistent_file(tmp_path):
    journal = order_manager.ExecutionJournal(tmp_path / "does_not_exist.jsonl")
    assert journal.read_all() == []
