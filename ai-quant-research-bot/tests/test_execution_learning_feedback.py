"""Phase 7 Part V - learning feedback: when a broker-paper trade's stop or
target leg actually fills, the ACTUAL fill/exit/commission must land in
paper_trades.csv (never a bar-simulation guess), the orphaned sibling exit
leg must be cancelled, and the managed order must move to CLOSED.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import paper_trades
from src.execution import learning_feedback, order_manager, order_state
from src.execution.broker import FakeBroker

logger = logging.getLogger("test")
PERMISSIVE_CONFIG_BASE = {"execution_risk": {"max_risk_per_trade_pct": 0.05}, "risk": {"account_equity": 10_000}}


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    cfg = dict(PERMISSIVE_CONFIG_BASE)
    cfg["data"] = {"journal_dir": str(journal_dir)}
    cfg["paper_trading"] = {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"}
    return cfg


def make_intent(**overrides):
    base = dict(
        intent_id=order_state.new_intent_id(), ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
        trade_id="AMD_2026-09-09_aaaaaaaa",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


def seed_open_trade(config, trade_id="AMD_2026-09-09_aaaaaaaa"):
    record = {
        "symbol": "AMD", "strategy": "Trend Following", "signal": "Top Candidate", "score": 90,
        "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "risk_reward": 3.0,
        "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
        "report_date": "2026-09-09", "decided_at": None,
    }
    paper_trades.record_paper_trade(record, config, trade_id=trade_id)


def make_filled_managed(broker, config, trade_id="AMD_2026-09-09_aaaaaaaa"):
    manager = order_manager.OrderManager(broker, config)
    managed = manager.submit_entry(make_intent(trade_id=trade_id))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    manager.poll_entry_fill(managed.intent.intent_id)
    assert managed.state == order_state.STATE_EXIT_PENDING
    return manager, managed


def test_stop_fill_closes_the_trade_as_stopped_with_actual_exit_price(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)

    broker.simulate_fill(managed.stop_broker_order_id, shares=10, price=94.75)
    closed = learning_feedback.check_exit_fills(manager, config, logger)

    assert len(closed) == 1
    assert closed[0]["status"] == "STOPPED"
    assert closed[0]["exit_price"] == 94.75
    assert managed.state == order_state.STATE_CLOSED


def test_target_fill_closes_the_trade_as_target_hit(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)

    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=115.5)
    closed = learning_feedback.check_exit_fills(manager, config, logger)

    assert len(closed) == 1
    assert closed[0]["status"] == "TARGET_HIT"
    assert closed[0]["exit_price"] == 115.5


def test_orphaned_sibling_leg_is_cancelled_when_one_leg_fills(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)

    target_order_id = managed.target_broker_order_id
    broker.simulate_fill(managed.stop_broker_order_id, shares=10, price=94.75)
    learning_feedback.check_exit_fills(manager, config, logger)

    assert broker._orders[target_order_id].status == "Cancelled"


def test_pnl_and_holding_days_are_computed_from_actual_fill(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)

    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=110.0)
    closed = learning_feedback.check_exit_fills(manager, config, logger)[0]

    assert closed["pnl_dollars"] == 100.0  # (110 - 100) * 10 shares
    assert closed["pnl_pct"] == 10.0


def test_commission_is_recorded_in_notes(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)
    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=112.0, commission=1.5)
    learning_feedback.check_exit_fills(manager, config, logger)

    df = paper_trades.load_paper_trades_df(config)
    row = df[df["trade_id"] == "AMD_2026-09-09_aaaaaaaa"].iloc[0]
    assert "1.5" in str(row["notes"])


def test_no_op_when_no_leg_has_filled_yet(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)

    closed = learning_feedback.check_exit_fills(manager, config, logger)
    assert closed == []
    assert managed.state == order_state.STATE_EXIT_PENDING


def test_calling_twice_after_close_is_idempotent_never_closes_twice(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)
    broker.simulate_fill(managed.stop_broker_order_id, shares=10, price=94.75)

    first = learning_feedback.check_exit_fills(manager, config, logger)
    assert len(first) == 1
    # managed.state is now CLOSED, so a second call must skip it entirely.
    second = learning_feedback.check_exit_fills(manager, config, logger)
    assert second == []


def test_paper_trades_csv_row_matches_actual_broker_fill_not_a_guess(config):
    broker = FakeBroker()
    broker.connect()
    seed_open_trade(config)
    manager, managed = make_filled_managed(broker, config)
    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=112.34)
    learning_feedback.check_exit_fills(manager, config, logger)

    df = paper_trades.load_paper_trades_df(config)
    row = df[df["trade_id"] == "AMD_2026-09-09_aaaaaaaa"].iloc[0]
    assert row["status"] == "TARGET_HIT"
    assert float(row["exit_price"]) == 112.34


def test_close_trade_with_actual_fill_returns_none_for_unknown_trade_id(config):
    seed_open_trade(config)
    result = paper_trades.close_trade_with_actual_fill(
        trade_id="does-not-exist", exit_price=100.0, exit_date="2026-09-10",
        exit_reason="x", status="STOPPED", config=config,
    )
    assert result is None


def test_close_trade_with_actual_fill_is_safe_to_call_twice(config):
    seed_open_trade(config)
    first = paper_trades.close_trade_with_actual_fill(
        trade_id="AMD_2026-09-09_aaaaaaaa", exit_price=94.75, exit_date="2026-09-10",
        exit_reason="Stop loss triggered", status="STOPPED", config=config,
    )
    assert first is not None
    second = paper_trades.close_trade_with_actual_fill(
        trade_id="AMD_2026-09-09_aaaaaaaa", exit_price=94.75, exit_date="2026-09-10",
        exit_reason="Stop loss triggered", status="STOPPED", config=config,
    )
    assert second is None  # already closed - no longer status=="OPEN", never double-applied
