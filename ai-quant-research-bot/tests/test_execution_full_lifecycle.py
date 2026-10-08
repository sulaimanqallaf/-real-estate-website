"""GitHub Issue #1 PRIORITY 4 - dedicated end-to-end PAPER lifecycle
integration tests tying multiple subsystems together, beyond what the
scattered unit/scenario tests already cover individually:

- crash recovery at distinct lifecycle boundaries (after entry
  acknowledged but unfilled, and after fill/protection but before exit),
  rehydrating a brand-new OrderManager purely from the ExecutionJournal
- a duplicate order is refused after that exact restart, for the same
  trade_id
- a broker disconnect blocks a pending manual approval end-to-end
  (not just position_monitor's own tick)
- a stop and target that BOTH fill on the same tick (a gap through both
  legs) closes the trade exactly once and never fabricates a second
  fill out of the sibling leg's cancel
- stale cached daily-bar data blocks execution end-to-end through
  approval_bridge.handle_manual_approval (GitHub Issue #1: never assume
  delayed market data is real-time)

Model-rollback end-to-end (decision ledger -> retrain_scheduler ->
model_events) already has real, non-mocked coverage in
tests/test_ml_retrain_scheduler.py's check_for_champion_deterioration
tests; not duplicated here.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import paper_trades
from src.execution import approval_bridge, learning_feedback, order_manager, order_state
from src.execution.broker import ACCOUNT_MODE_PAPER, FakeBroker
from src.ml import decision_ledger

logger = logging.getLogger("test")


def make_intent(**overrides):
    base = dict(
        intent_id=order_state.new_intent_id(), ticker="AMD", side=order_state.SIDE_BUY, quantity=10,
        order_type=order_state.ORDER_TYPE_LIMIT, entry_price=100.0, stop_loss=95.0,
        target_price=115.0, strategy="Trend Following", signal_score=90, quant_score=None,
        risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
    )
    base.update(overrides)
    return order_state.OrderIntent(**base)


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "execution_risk": {"max_risk_per_trade_pct": 0.05},
        "risk": {"account_equity": 10_000},
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"},
    }


def _record_open_trade(config, trade_id, symbol="AMD"):
    paper_trades.record_paper_trade(
        {
            "symbol": symbol, "strategy": "Trend Following", "signal": "Top Candidate", "score": 90,
            "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "risk_reward": 3.0,
            "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
            "report_date": "2026-09-09", "decided_at": None,
        },
        config, trade_id=trade_id, provenance=paper_trades.PROVENANCE_BROKER_PAPER,
    )


# --- crash recovery at distinct lifecycle boundaries --------------------------------


def test_crash_after_entry_acknowledged_but_unfilled_resumes_and_still_syncs_protection(config, tmp_path):
    journal_path = tmp_path / "journal" / "executions.jsonl"
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()

    dying_manager = order_manager.OrderManager(broker, config, order_manager.ExecutionJournal(journal_path))
    managed = dying_manager.submit_entry(make_intent(trade_id="AMD_2026-09-09_aaaaaaaa"))
    assert managed.state == order_state.STATE_ACKNOWLEDGED
    del dying_manager  # the process "crashes" here - before any fill was ever seen

    # A fresh process, same broker (a real IBKR Paper account's state
    # survives a local crash; only our own in-memory state was lost).
    restarted_manager = order_manager.OrderManager(broker, config, order_manager.ExecutionJournal(journal_path))
    restarted_manager.restore_from_journal_rows(order_manager.ExecutionJournal(journal_path).read_all())
    resumed = restarted_manager.all_managed()
    assert len(resumed) == 1
    assert resumed[0].state == order_state.STATE_ACKNOWLEDGED

    broker_order_id = resumed[0].entry_broker_order_id
    broker.simulate_fill(broker_order_id, shares=10, price=100.0)
    restarted_manager.poll_entry_fill(resumed[0].intent.intent_id)

    updated = restarted_manager.get(resumed[0].intent.intent_id)
    assert updated.state == order_state.STATE_EXIT_PENDING
    assert updated.stop_broker_order_id is not None
    assert updated.target_broker_order_id is not None


def test_crash_between_fill_and_protection_sync_is_detected_and_completed_on_restart(config, tmp_path):
    """The specific recovery gap poll_entry_fill()'s docstring calls out:
    a process that died in the narrow window AFTER journaling the fill but
    BEFORE _sync_protection() actually placed the stop/target orders -
    leaving a genuinely filled, wholly unprotected position. A restarted
    manager must notice this on its very next poll, not just on a NEW
    fill (there won't be one - the position is already fully filled)."""
    journal_path = tmp_path / "journal" / "executions.jsonl"
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    journal = order_manager.ExecutionJournal(journal_path)

    dying_manager = order_manager.OrderManager(broker, config, journal)
    managed = dying_manager.submit_entry(make_intent(trade_id="AMD_2026-09-09_bbbbbbbb"))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    # Manually journal a "fill, no protection yet" row - reproducing the
    # exact crash window, rather than relying on timing a real crash
    # mid-way through poll_entry_fill().
    managed.filled_quantity = 10
    managed.avg_fill_price = 100.0
    managed.state = order_state.STATE_FILLED
    journal.record_state(managed, event="fill", extra={"newly_filled": 10})
    del dying_manager

    restarted_manager = order_manager.OrderManager(broker, config, journal)
    restarted_manager.restore_from_journal_rows(journal.read_all())
    resumed = restarted_manager.all_managed()[0]
    assert resumed.state == order_state.STATE_FILLED
    assert resumed.stop_broker_order_id is None  # confirms the gap was actually reproduced

    restarted_manager.poll_entry_fill(resumed.intent.intent_id)
    updated = restarted_manager.get(resumed.intent.intent_id)
    assert updated.state == order_state.STATE_EXIT_PENDING
    assert updated.stop_broker_order_id is not None
    assert updated.target_broker_order_id is not None
    assert broker._orders[updated.stop_broker_order_id].quantity == 10


def test_crash_during_exit_pending_is_resumed_and_a_later_fill_still_closes_the_trade(config, tmp_path):
    journal_path = tmp_path / "journal" / "executions.jsonl"
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    journal = order_manager.ExecutionJournal(journal_path)
    trade_id = "AMD_2026-09-09_cccccccc"
    _record_open_trade(config, trade_id)

    dying_manager = order_manager.OrderManager(broker, config, journal)
    managed = dying_manager.submit_entry(make_intent(trade_id=trade_id))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    dying_manager.poll_entry_fill(managed.intent.intent_id)
    assert managed.state == order_state.STATE_EXIT_PENDING
    del dying_manager  # crashes while fully protected and waiting for an exit

    restarted_manager = order_manager.OrderManager(broker, config, journal)
    restarted_manager.restore_from_journal_rows(journal.read_all())
    resumed = restarted_manager.all_managed()[0]
    assert resumed.state == order_state.STATE_EXIT_PENDING

    broker.simulate_fill(resumed.target_broker_order_id, shares=10, price=112.0)
    closed = learning_feedback.check_exit_fills(restarted_manager, config, logger)

    assert len(closed) == 1
    assert closed[0]["status"] == "TARGET_HIT"
    assert restarted_manager.get(resumed.intent.intent_id).state == order_state.STATE_CLOSED

    outcomes = decision_ledger.query_decisions(decision_ledger.resolve_db_path(config), only_with_outcome=True)
    # No decision row was ever recorded for this trade_id in this test
    # (that happens in main.py/approval_bridge.py, not here) - confirms
    # record_outcome's "match an existing row" safety just no-ops rather
    # than fabricating one, instead of asserting on a row that doesn't exist.
    assert outcomes == []


# --- duplicate prevention survives the exact same restart ---------------------------


def test_duplicate_order_is_refused_after_a_restart_for_the_same_trade_id(config, tmp_path):
    journal_path = tmp_path / "journal" / "executions.jsonl"
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    journal = order_manager.ExecutionJournal(journal_path)
    trade_id = "AMD_2026-09-09_dddddddd"

    dying_manager = order_manager.OrderManager(broker, config, journal)
    dying_manager.submit_entry(make_intent(trade_id=trade_id))
    del dying_manager

    restarted_manager = order_manager.OrderManager(broker, config, journal)
    restarted_manager.restore_from_journal_rows(journal.read_all())
    assert len(broker.submitted_intents) == 1

    retried = restarted_manager.submit_entry(make_intent(trade_id=trade_id, intent_id=order_state.new_intent_id()))
    assert retried.state == order_state.STATE_ERROR
    assert "Duplicate" in retried.rejection_reason
    assert len(broker.submitted_intents) == 1  # never reached the broker a second time


# --- broker disconnect blocks a pending manual approval end-to-end ------------------


def test_broker_disconnected_blocks_a_pending_manual_approval(config):
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    broker.disconnect()  # e.g. TWS dropped between the Telegram button render and the tap
    manager = order_manager.OrderManager(broker, config)

    record = {
        "symbol": "AMD", "strategy": "Trend Following", "score": 90, "entry": 100.0,
        "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
    }
    result = approval_bridge.execute_approved_trade(record, config, broker, manager, current_market_price=100.0, logger=logger)

    assert result["executed"] is False
    assert "BROKER_DISCONNECTED" in result["reasons"]
    assert broker.submitted_intents == []


# --- simultaneous stop + target fill (a gap through both legs) ---------------------


def test_stop_and_target_both_filling_on_the_same_tick_closes_the_trade_exactly_once(config, tmp_path):
    """A gap can blow through both the stop AND the target between one
    poll and the next - e.g. a halt resumes far below the stop, or both
    legs sit inside a single huge print. check_exit_fills() must close
    the trade exactly once (never double-record, never crash), and the
    broker-side cancel of whichever leg it didn't act on must never
    retroactively erase the fact that leg ALSO genuinely filled (see
    FakeBroker.cancel_order's no-op-on-Filled guard)."""
    journal_path = tmp_path / "journal" / "executions.jsonl"
    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    journal = order_manager.ExecutionJournal(journal_path)
    trade_id = "AMD_2026-09-09_eeeeeeee"
    _record_open_trade(config, trade_id)

    manager = order_manager.OrderManager(broker, config, journal)
    managed = manager.submit_entry(make_intent(trade_id=trade_id))
    broker.simulate_fill(managed.entry_broker_order_id, shares=10, price=100.0)
    manager.poll_entry_fill(managed.intent.intent_id)
    assert managed.state == order_state.STATE_EXIT_PENDING

    # Both legs fill before the next poll ever runs.
    broker.simulate_fill(managed.stop_broker_order_id, shares=10, price=94.5)
    broker.simulate_fill(managed.target_broker_order_id, shares=10, price=115.0)

    closed = learning_feedback.check_exit_fills(manager, config, logger)
    assert len(closed) == 1  # exactly one close, not two
    assert closed[0]["status"] == "STOPPED"  # stop is checked first - deterministic, not a race

    # The target leg's OWN fill must survive the stop-leg's sibling-cancel
    # call, rather than being silently overwritten to "Cancelled".
    target_order = broker.get_order(managed.target_broker_order_id)
    assert target_order.status == "Filled"

    # Re-running the poll again must never re-close (or re-cancel) an
    # already-CLOSED managed order.
    closed_again = learning_feedback.check_exit_fills(manager, config, logger)
    assert closed_again == []

    df = paper_trades.load_paper_trades_df(config)
    row = df[df["trade_id"] == trade_id].iloc[0]
    assert row["status"] == "STOPPED"


# --- stale cached daily-bar data blocks execution end-to-end -----------------------


def test_stale_cached_daily_bar_blocks_a_manual_approval(config, tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    config["data"]["raw_dir"] = str(raw_dir)
    stale_date = (datetime.now(timezone.utc) - timedelta(days=10)).date()
    pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.DatetimeIndex([stale_date], name="date"),
    ).to_csv(raw_dir / "AMD_daily.csv")

    config["execution"] = {"mode": "IBKR_PAPER", "journal_path": str(tmp_path / "journal" / "executions.jsonl")}
    config["paper_trading"]["pending_expiry_hours"] = 72
    pending = {
        "report_date": "2026-09-09", "symbol": "AMD", "chat_id": "12345", "message_id": 1,
        "strategy": "Trend Following", "is_aggressive": False, "signal": "Top Candidate", "score": 90,
        "regime_at_entry": "TRENDING_UP", "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
        "risk_reward": 3.0, "expected_upside_pct": 15.0, "expected_downside_pct": 5.0,
        "shares": 10, "dollar_risk": 50.0, "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(), "decided_at": None,
    }
    key = paper_trades._key("AMD", "2026-09-09")
    paper_trades._save_all({key: pending}, config)

    broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)
    broker.connect()
    monkeypatch.setattr("src.data_collector.fetch_current_price", lambda symbol, logger: 100.0)

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", config, logger, broker=broker)

    assert success is False
    assert "DATA_STALE" in message
    assert broker.submitted_intents == []
