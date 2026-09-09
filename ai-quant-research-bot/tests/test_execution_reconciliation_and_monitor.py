"""Phase 7 Part Z - Broker reconciliation (Part J) and position monitoring
(Part Q / Monitoring category): partial/complete fill, disconnect/reconnect
freezing new entries, and an unknown broker position causing a
reconciliation halt.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import order_manager, order_state, position_monitor, reconciliation
from src.execution.broker import CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, FakeBroker

logger = logging.getLogger("test")
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


# --- reconciliation discrepancy kinds --------------------------------------------------


def test_reconcile_clean_when_local_and_broker_agree():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent(quantity=10))
    broker.simulate_fill(order.broker_order_id, shares=10, price=100.0)
    local_trades = [{"ticker": "AMD", "position_size": 10}]
    local_orders = [{"broker_order_id": order.broker_order_id, "filled_quantity": 10}]
    report = reconciliation.reconcile(broker, local_trades, local_orders)
    assert report.ok


def test_reconcile_detects_local_open_broker_missing():
    broker = FakeBroker()
    broker.connect()
    local_trades = [{"ticker": "AMD", "position_size": 10}]
    report = reconciliation.reconcile(broker, local_trades, [])
    assert not report.ok
    assert report.discrepancies[0].kind == reconciliation.DISCREPANCY_LOCAL_OPEN_BROKER_MISSING


def test_reconcile_detects_broker_position_local_missing():
    broker = FakeBroker()
    broker.connect()
    broker.inject_unknown_position("MSFT", 5)
    report = reconciliation.reconcile(broker, [], [])
    assert not report.ok
    assert report.discrepancies[0].kind == reconciliation.DISCREPANCY_BROKER_POSITION_LOCAL_MISSING


def test_reconcile_detects_quantity_mismatch():
    broker = FakeBroker()
    broker.connect()
    broker.inject_unknown_position("AMD", 7)
    local_trades = [{"ticker": "AMD", "position_size": 10}]
    report = reconciliation.reconcile(broker, local_trades, [])
    assert not report.ok
    assert report.discrepancies[0].kind == reconciliation.DISCREPANCY_QUANTITY_MISMATCH


def test_reconcile_detects_unknown_order():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent())
    report = reconciliation.reconcile(broker, [], [])
    assert not report.ok
    assert any(d.kind == reconciliation.DISCREPANCY_UNKNOWN_ORDER for d in report.discrepancies)


def test_reconcile_detects_fill_mismatch():
    broker = FakeBroker()
    broker.connect()
    order = broker.submit_order(make_intent(quantity=10))
    broker.simulate_fill(order.broker_order_id, shares=4, price=100.0)
    local_orders = [{"broker_order_id": order.broker_order_id, "filled_quantity": 0.0}]
    report = reconciliation.reconcile(broker, [], local_orders)
    assert not report.ok
    assert any(d.kind == reconciliation.DISCREPANCY_FILL_MISMATCH for d in report.discrepancies)


def test_reconcile_summary_reports_clean_state():
    broker = FakeBroker()
    broker.connect()
    report = reconciliation.reconcile(broker, [], [])
    assert "clean" in report.summary().lower()


# --- position_monitor.run_one_tick ------------------------------------------------------


def test_run_one_tick_disconnected_freezes_new_entries():
    broker = FakeBroker()  # never connected
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG)
    tick = position_monitor.run_one_tick(broker, manager, PERMISSIVE_CONFIG, [], logger)
    assert tick["new_entries_allowed"] is False
    assert tick["connection_state"] == CONNECTION_DISCONNECTED


def test_run_one_tick_polls_fills_for_in_flight_orders_and_syncs_protection():
    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG)
    managed = manager.submit_entry(make_intent(quantity=10))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)

    tick = position_monitor.run_one_tick(broker, manager, PERMISSIVE_CONFIG, [], logger)
    assert tick["connection_state"] == CONNECTION_CONNECTED
    updated = manager.get(managed.intent.intent_id)
    assert updated.state == order_state.STATE_EXIT_PENDING
    assert updated.stop_broker_order_id is not None


def test_run_one_tick_unknown_broker_position_causes_reconciliation_halt():
    broker = FakeBroker()
    broker.connect()
    broker.inject_unknown_position("ZZZZ", 100)  # a position local state never created
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG)
    tick = position_monitor.run_one_tick(broker, manager, PERMISSIVE_CONFIG, [], logger)
    assert not tick["reconciliation"].ok
    assert tick["new_entries_allowed"] is False
    assert "RECONCILIATION_FAILURE" in tick["breakers"].tripped


def test_run_one_tick_reports_closed_trades_from_learning_feedback(tmp_path):
    from src import paper_trades

    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    config = dict(PERMISSIVE_CONFIG)
    config["data"] = {"journal_dir": str(journal_dir)}
    config["paper_trading"] = {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"}

    paper_trades.record_paper_trade(
        {
            "symbol": "AMD", "strategy": "Trend Following", "signal": "Top Candidate", "score": 90,
            "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "risk_reward": 3.0,
            "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
            "report_date": "2026-09-09", "decided_at": None,
        },
        config, trade_id="AMD_2026-09-09_aaaaaaaa",
    )

    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, config)
    managed = manager.submit_entry(make_intent(trade_id="AMD_2026-09-09_aaaaaaaa"))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    manager.poll_entry_fill(managed.intent.intent_id)
    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=112.0)

    tick = position_monitor.run_one_tick(broker, manager, config, [], logger)
    assert len(tick["closed_trades"]) == 1
    assert tick["closed_trades"][0]["status"] == "TARGET_HIT"


def test_run_one_tick_never_raises_even_if_one_managed_order_poll_fails(monkeypatch):
    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, PERMISSIVE_CONFIG)
    managed = manager.submit_entry(make_intent(quantity=10))

    def boom(intent_id):
        raise RuntimeError("simulated poll failure")

    monkeypatch.setattr(manager, "poll_entry_fill", boom)
    tick = position_monitor.run_one_tick(broker, manager, PERMISSIVE_CONFIG, [], logger)
    assert tick["connection_state"] == CONNECTION_CONNECTED  # did not raise/crash the tick
