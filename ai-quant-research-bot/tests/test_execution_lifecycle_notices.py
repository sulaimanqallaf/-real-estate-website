"""Telegram lifecycle notices (partial/full fill, protection created/
resized, broker rejection, submission error, circuit-breaker activation,
reconciliation failure) - pure text formatting, plus the actual wiring
through OrderManager.on_event and position_monitor's run_forever that
decides WHEN a notice is sent and when it's deliberately NOT (to avoid
turning monitoring into spam).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import lifecycle_notices, order_manager, order_state
from src.execution.broker import FakeBroker

PERMISSIVE_CONFIG = {"execution_risk": {"max_risk_per_trade_pct": 0.05}, "risk": {"account_equity": 10_000}}


def make_intent(**overrides):
    from datetime import datetime, timezone

    base = dict(
        intent_id=order_state.new_intent_id(), ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


# --- pure formatting -----------------------------------------------------------------


def test_rejected_notice_names_the_ticker_and_reason():
    managed = order_manager.ManagedOrder(intent=make_intent(), state=order_state.STATE_REJECTED, rejection_reason="percentage constraint")
    text = lifecycle_notices.format_lifecycle_notice("rejected", managed, {})
    assert "AMD" in text
    assert "REJECTED" in text
    assert "percentage constraint" in text


def test_submission_error_notice_distinguishes_from_rejection():
    managed = order_manager.ManagedOrder(intent=make_intent(), state=order_state.STATE_ERROR, rejection_reason="connection lost")
    text = lifecycle_notices.format_lifecycle_notice("submission_error", managed, {})
    assert "ERROR" in text
    assert "connection lost" in text


def test_fill_notice_labels_partial_vs_full():
    intent = make_intent(quantity=10)
    managed = order_manager.ManagedOrder(intent=intent, filled_quantity=4, avg_fill_price=100.5)
    partial_text = lifecycle_notices.format_lifecycle_notice("fill", managed, {"newly_filled": 4})
    assert "PARTIAL FILL" in partial_text

    managed.filled_quantity = 10
    full_text = lifecycle_notices.format_lifecycle_notice("fill", managed, {"newly_filled": 6})
    assert "FULL FILL" in full_text


def test_protection_notice_distinguishes_created_vs_resized():
    managed = order_manager.ManagedOrder(intent=make_intent(), stop_broker_order_id="1", target_broker_order_id="2")
    created_text = lifecycle_notices.format_lifecycle_notice("protection_synced", managed, {"protected_quantity": 4, "_protection_just_created": True})
    assert "created" in created_text

    resized_text = lifecycle_notices.format_lifecycle_notice("protection_synced", managed, {"protected_quantity": 10, "_protection_just_created": False})
    assert "resized" in resized_text


@pytest.mark.parametrize("event", ["submitting", "acknowledged", "cancelled", "closed", "validation_failed", "duplicate_blocked"])
def test_events_with_their_own_dedicated_notice_elsewhere_return_none(event):
    managed = order_manager.ManagedOrder(intent=make_intent())
    assert lifecycle_notices.format_lifecycle_notice(event, managed, {}) is None


def test_circuit_breaker_notice_lists_every_newly_tripped_breaker():
    text = lifecycle_notices.format_circuit_breaker_notice(["DAILY_LOSS_LIMIT", "MAX_OPEN_POSITIONS"])
    assert "DAILY_LOSS_LIMIT" in text
    assert "MAX_OPEN_POSITIONS" in text
    assert "CIRCUIT BREAKER" in text


def test_reconciliation_failure_notice_includes_the_summary():
    text = lifecycle_notices.format_reconciliation_failure_notice("1 discrepancy(ies) found:\n  [UNKNOWN_ORDER] AMD: ...")
    assert "RECONCILIATION FAILURE" in text
    assert "UNKNOWN_ORDER" in text


# --- OrderManager.on_event wiring -----------------------------------------------------


def test_on_event_fires_for_every_recorded_state_transition():
    broker = FakeBroker()
    broker.connect()
    events = []
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, on_event=lambda e, m, extra: events.append(e))

    managed = manager.submit_entry(make_intent())
    assert "submitting" in events
    assert "acknowledged" in events

    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    manager.poll_entry_fill(managed.intent.intent_id)
    assert "fill" in events
    assert "protection_synced" in events


def test_on_event_fires_for_broker_rejection():
    from src.execution.broker import BrokerOrderRejected

    class _RejectingBroker(FakeBroker):
        def submit_order(self, intent):
            raise BrokerOrderRejected("TWS said no")

    broker = _RejectingBroker()
    broker.connect()
    events = []
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, on_event=lambda e, m, extra: events.append(e))
    manager.submit_entry(make_intent())
    assert "rejected" in events


def test_a_raising_on_event_callback_never_breaks_order_management():
    """The one invariant that matters most here: a Telegram failure (or any
    bug in the notifier) must never prevent the journal from recording the
    real state, and must never propagate up and abort order management."""
    broker = FakeBroker()
    broker.connect()

    def boom(event, managed, extra):
        raise RuntimeError("simulated Telegram failure")

    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG, on_event=boom)
    managed = manager.submit_entry(make_intent())  # must not raise
    assert managed.state == order_state.STATE_ACKNOWLEDGED


# --- run_forever: notify only on a NEW breaker trip / NEW reconciliation failure -----


def test_run_forever_notifies_once_for_a_newly_tripped_breaker_not_every_tick(monkeypatch):
    from src.execution import position_monitor

    sent = []
    monkeypatch.setattr("src.telegram_bot.send_telegram_message", lambda token, chat_id, text, logger: sent.append(text) or True)

    ticks = [
        {"connection_state": "CONNECTED", "breakers": _breaker_result(["DAILY_LOSS_LIMIT"]), "reconciliation": _clean_reconciliation(), "new_entries_allowed": False, "closed_trades": []},
        {"connection_state": "CONNECTED", "breakers": _breaker_result(["DAILY_LOSS_LIMIT"]), "reconciliation": _clean_reconciliation(), "new_entries_allowed": False, "closed_trades": []},
    ]
    call_count = {"n": 0}

    def fake_run_one_tick(broker, manager, config, local_open_trades, logger):
        tick = ticks[min(call_count["n"], len(ticks) - 1)]
        call_count["n"] += 1
        if call_count["n"] >= len(ticks):
            raise KeyboardInterrupt  # stop the infinite loop after we've seen both ticks
        return tick

    monkeypatch.setattr(position_monitor, "run_one_tick", fake_run_one_tick)
    monkeypatch.setattr(position_monitor.time, "sleep", lambda s: None)

    import logging

    with pytest.raises(KeyboardInterrupt):
        position_monitor.run_forever(object(), object(), {}, logging.getLogger("test"), token="TOKEN", chat_id="123")

    breaker_notices = [s for s in sent if "CIRCUIT BREAKER" in s]
    assert len(breaker_notices) == 1  # only the FIRST tick's newly-tripped breaker, not the second tick's repeat


def _breaker_result(tripped):
    from src.execution.circuit_breaker import BreakerResult

    return BreakerResult(tripped=tripped)


def _clean_reconciliation():
    from src.execution.reconciliation import ReconciliationReport

    return ReconciliationReport()
