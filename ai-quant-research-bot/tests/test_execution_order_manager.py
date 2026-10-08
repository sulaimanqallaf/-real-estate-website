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


def test_submit_entry_maps_broker_order_rejected_to_state_rejected(manager, broker, monkeypatch):
    """The bug fix: a broker-side rejection raised from submit_order()
    (e.g. IBKR's percentage-constraint rejection) must become
    STATE_REJECTED with the real reason persisted - never a silent
    STATE_ACKNOWLEDGED."""
    from src.execution.broker import BrokerOrderRejected

    def boom(intent):
        raise BrokerOrderRejected("TWS rejected order 1000 (AMD): [201] percentage constraint")

    monkeypatch.setattr(broker, "submit_order", boom)
    managed = manager.submit_entry(make_intent())
    assert managed.state == order_state.STATE_REJECTED
    assert "percentage constraint" in managed.rejection_reason
    assert managed.entry_broker_order_id is None


def test_submit_entry_maps_other_broker_exceptions_to_state_error(manager, broker, monkeypatch):
    """A connection failure/timeout (anything that is NOT an explicit
    broker rejection) must become STATE_ERROR, not STATE_REJECTED and
    never STATE_ACKNOWLEDGED."""

    def boom(intent):
        raise ConnectionError("simulated: TWS connection lost mid-submit")

    monkeypatch.setattr(broker, "submit_order", boom)
    managed = manager.submit_entry(make_intent())
    assert managed.state == order_state.STATE_ERROR
    assert "connection lost" in managed.rejection_reason
    assert managed.entry_broker_order_id is None


def test_submit_entry_never_reaches_acknowledged_without_a_broker_order(manager, broker, monkeypatch):
    """No false ACKNOWLEDGED (requirement #10): submit_entry() must never
    report ACKNOWLEDGED unless broker.submit_order() actually returned a
    BrokerOrder - this proves it for both failure classes at once."""
    from src.execution.broker import BrokerOrderRejected

    for exc in (BrokerOrderRejected("rejected"), RuntimeError("error")):
        def boom(intent, _exc=exc):
            raise _exc

        monkeypatch.setattr(broker, "submit_order", boom)
        managed = manager.submit_entry(make_intent())
        assert managed.state != order_state.STATE_ACKNOWLEDGED


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


def test_restore_from_journal_rows_rebuilds_a_full_managed_order_for_continued_monitoring(tmp_path, broker):
    """The actual restart-recovery gap this closes: a process that only
    rehydrates the duplicate-prevention index (the old behavior) can
    detect "don't resubmit this" but can never again poll fills or sync
    protection for an order a DIFFERENT (now-dead) process submitted -
    all_managed() would stay empty forever. restore_from_journal_rows()
    must rebuild the real ManagedOrder, with every broker id/fill/intent
    field intact, so a restarted position_monitor can resume managing it."""
    journal_path = tmp_path / "executions.jsonl"
    journal = order_manager.ExecutionJournal(journal_path)
    manager1 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    intent = make_intent()
    managed = manager1.submit_entry(intent)
    broker.simulate_fill(managed.entry_broker_order_id, shares=4, price=100.5)
    manager1.poll_entry_fill(intent.intent_id)
    assert managed.state == order_state.STATE_PARTIALLY_FILLED

    manager2 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    manager2.restore_from_journal_rows(journal.read_all())

    rebuilt = manager2.get(intent.intent_id)
    assert rebuilt is not None
    assert rebuilt.state == order_state.STATE_PARTIALLY_FILLED
    assert rebuilt.entry_broker_order_id == managed.entry_broker_order_id
    assert rebuilt.stop_broker_order_id == managed.stop_broker_order_id
    assert rebuilt.filled_quantity == 4
    assert rebuilt.avg_fill_price == 100.5
    assert rebuilt.intent.ticker == intent.ticker
    assert rebuilt.intent.stop_loss == intent.stop_loss
    assert rebuilt.intent.target_price == intent.target_price

    # And the rebuilt order is actually usable, not just inspectable - the
    # restarted process can keep polling it as if it had submitted it itself.
    broker.simulate_fill(rebuilt.entry_broker_order_id, shares=6, price=100.7)
    manager2.poll_entry_fill(intent.intent_id)
    assert rebuilt.state == order_state.STATE_EXIT_PENDING  # FILLED immediately followed by protection sync
    assert broker._orders[rebuilt.stop_broker_order_id].quantity == 10


def test_poll_entry_fill_recovers_a_filled_but_unprotected_order(manager, broker):
    """Scenario D (process dies after fill but before protection): a
    ManagedOrder can be fully filled with NEITHER protective leg placed
    yet - e.g. the process died between _apply_fill()'s journal row and
    _sync_protection() actually submitting the stop/target, or this is a
    rebuilt order from a journal row recorded at exactly that gap. A fill
    that never changes again (the position is already fully filled) must
    still eventually get protected - waiting for a NEW fill would never
    trigger it."""
    managed = manager.submit_entry(make_intent(quantity=10))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)

    # Simulate exactly the gap: the fill is already reflected in filled_quantity,
    # but no protection has been placed - as if _sync_protection() never ran.
    managed.filled_quantity = 10
    managed.state = order_state.STATE_FILLED
    assert managed.stop_broker_order_id is None
    assert managed.target_broker_order_id is None

    manager.poll_entry_fill(managed.intent.intent_id)

    assert managed.stop_broker_order_id is not None
    assert managed.target_broker_order_id is not None
    assert broker._orders[managed.stop_broker_order_id].quantity == 10
    assert managed.state == order_state.STATE_EXIT_PENDING


def test_restore_from_journal_rows_does_not_rebuild_terminal_orders(tmp_path, broker):
    """A cancelled/rejected/closed/errored order has nothing left to
    monitor - rebuilding it into _managed would be pure memory bloat for a
    long-running process, and a stale entry an unrelated duplicate check
    could trip over."""
    journal_path = tmp_path / "executions.jsonl"
    journal = order_manager.ExecutionJournal(journal_path)
    manager1 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    intent = make_intent()
    managed = manager1.submit_entry(intent)
    manager1.cancel_entry(intent.intent_id)
    assert managed.state == order_state.STATE_CANCELLED

    manager2 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    manager2.restore_from_journal_rows(journal.read_all())

    assert manager2.get(intent.intent_id) is None
    assert manager2.all_managed() == []


def test_restore_from_journal_rows_tolerates_a_row_with_no_intent_payload(tmp_path, broker):
    """An older journal written before intents were persisted in full (or
    any other row missing the 'intent' key) must not crash restore - the
    duplicate-prevention index still works, there's just nothing to
    rebuild for that row."""
    journal_path = tmp_path / "executions.jsonl"
    journal = order_manager.ExecutionJournal(journal_path)
    manager1 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    intent = make_intent()
    manager1.submit_entry(intent)

    rows = journal.read_all()
    for row in rows:
        row.pop("intent", None)
    journal_path.write_text("\n".join(__import__("json").dumps(r, default=str) for r in rows) + "\n")

    manager2 = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, journal)
    manager2.restore_from_journal_rows(journal.read_all())  # must not raise

    assert manager2.get(intent.intent_id) is None
    assert manager2.is_duplicate(make_intent(intent_id=order_state.new_intent_id())) is True


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


def test_journal_persists_the_full_intent_for_restart_recovery(tmp_path, manager, broker):
    journal_path = tmp_path / "executions.jsonl"
    manager.journal = order_manager.ExecutionJournal(journal_path)
    intent = make_intent()
    manager.submit_entry(intent)

    rows = manager.journal.read_all()
    acknowledged_row = next(r for r in rows if r["type"] == "acknowledged")
    assert acknowledged_row["intent"]["ticker"] == intent.ticker
    assert acknowledged_row["intent"]["stop_loss"] == intent.stop_loss
    assert acknowledged_row["intent"]["target_price"] == intent.target_price
    assert isinstance(acknowledged_row["intent"]["created_at"], str)  # JSON-safe, not a raw datetime


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
