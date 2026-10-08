"""Phase 7 continuation - IBKR Paper Manual Execution: a Telegram "Approve
Paper Trade" tap, re-checked against every risk gate at execution time,
submits a real (paper) order only when execution.mode == "IBKR_PAPER" and
the account is verified PAPER. Manual approval only - no autonomous
execution, no live account, no margin, no options.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import src.data_collector as data_collector
import src.paper_trades as paper_trades
from src.execution import approval_bridge, order_manager
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, FakeBroker

logger = logging.getLogger("test")

# A fixed Wednesday, mid-session NY time - injected into every
# handle_manual_approval() call below so results never depend on the real
# wall-clock weekday the suite happens to run on (the config's
# "00:00-23:59" window only widens the HOUR check - is_within_trading_
# hours() hard-blocks Sat/Sun regardless of configured hours).
FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def pending_record(symbol: str, report_date: str = "2026-09-09", **overrides) -> dict:
    record = {
        "report_date": report_date, "symbol": symbol, "chat_id": "12345", "message_id": 1,
        "strategy": "Trend Following", "is_aggressive": False, "signal": "Top Candidate", "score": 90,
        "regime_at_entry": "TRENDING_UP", "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
        "risk_reward": 3.0, "expected_upside_pct": 15.0, "expected_downside_pct": 5.0,
        "shares": 10, "dollar_risk": 50.0, "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(), "decided_at": None,
    }
    record.update(overrides)
    return record


@pytest.fixture
def manual_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "execution": {
            "mode": "IBKR_PAPER", "journal_path": str(tmp_path / "executions.jsonl"),
            "halt_state_file": str(tmp_path / "trading_halt.json"),
            "trading_hours_start": "00:00", "trading_hours_end": "23:59",
        },
        "autonomous_paper": {"enabled": False, "auto_execute": {"enabled": False}},
        "execution_risk": {"max_risk_per_trade_pct": 0.05},
        "portfolio_risk": {},
        "risk": {"account_equity": 10_000},
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json", "pending_expiry_hours": 72},
        "telegram": {"top_candidates_limit": 10},
    }


def seed_pending(config, symbol="AMD", **overrides):
    record = pending_record(symbol, **overrides)
    key = paper_trades._key(symbol, record["report_date"])
    paper_trades._save_all({key: record}, config)
    return record


def paper_broker():
    b = FakeBroker(account_mode=ACCOUNT_MODE_PAPER, account_id="DU123456", net_liquidation=10_000.0)
    b.connect()
    return b


def live_broker():
    b = FakeBroker(account_mode=ACCOUNT_MODE_LIVE, account_id="U123456", net_liquidation=10_000.0)
    b.connect()
    return b


# --- the happy path: manual approve submits to IBKR Paper ---------------------------


def test_approving_in_ibkr_paper_mode_submits_a_real_order(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is True
    assert "submitted to IBKR" in message
    assert len(broker.submitted_intents) == 1
    assert broker.submitted_intents[0].ticker == "AMD"

    df = paper_trades.load_paper_trades_df(manual_config)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "AMD"
    assert df.iloc[0]["status"] == "OPEN"
    # GitHub Issue #1 finding 5: a real IBKR submission must be marked
    # BROKER_PAPER so paper_trade_tracker.py's daily-bar simulation never
    # touches it - only the broker's own actual fill may close it.
    assert df.iloc[0]["provenance"] == paper_trades.PROVENANCE_BROKER_PAPER


def test_approved_trade_id_matches_between_order_intent_and_csv_row(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    intent_trade_id = broker.submitted_intents[0].trade_id
    df = paper_trades.load_paper_trades_df(manual_config)
    assert df.iloc[0]["trade_id"] == intent_trade_id


# --- hard rule: live account is rejected, zero orders submitted --------------------


def test_live_account_blocks_manual_approval_zero_orders_submitted(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    seed_pending(manual_config, "AMD")
    broker = live_broker()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is False
    assert "NOT submitted" in message
    assert broker.submitted_intents == []

    # The pending approval must stay PENDING and retryable - never marked
    # APPROVED when nothing was actually executed.
    records = paper_trades.load_pending_approvals(manual_config)
    assert records[paper_trades._key("AMD", "2026-09-09")]["status"] == "PENDING"

    df = paper_trades.load_paper_trades_df(manual_config)
    assert df.empty


def test_unknown_account_mode_blocks_manual_approval(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    seed_pending(manual_config, "AMD")
    broker = FakeBroker(account_mode="UNKNOWN", account_id="X1")
    broker.connect()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is False
    assert broker.submitted_intents == []


# --- hard rule: DRY_RUN still never calls the broker, no matter what -----------------


def test_dry_run_mode_never_touches_broker_even_for_a_valid_approve(manual_config, monkeypatch):
    manual_config["execution"]["mode"] = "DRY_RUN"
    seed_pending(manual_config, "AMD")

    class _PoisonedBroker:
        def __getattr__(self, name):
            raise AssertionError(f"Broker.{name} must never be touched in DRY_RUN.")

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=_PoisonedBroker(), now=FIXED_NOW)

    assert success is True
    assert "approved and recorded in paper_trades.csv" in message
    df = paper_trades.load_paper_trades_df(manual_config)
    assert len(df) == 1


def test_reject_and_watch_never_touch_broker_even_in_ibkr_paper_mode(manual_config):
    class _PoisonedBroker:
        def __getattr__(self, name):
            raise AssertionError(f"Broker.{name} must never be touched for reject/watch.")

    seed_pending(manual_config, "MSFT")
    success, message = approval_bridge.handle_manual_approval("reject", "MSFT", "2026-09-09", manual_config, logger, broker=_PoisonedBroker(), now=FIXED_NOW)
    assert success is True

    seed_pending(manual_config, "NVDA")
    success, message = approval_bridge.handle_manual_approval("watch", "NVDA", "2026-09-09", manual_config, logger, broker=_PoisonedBroker(), now=FIXED_NOW)
    assert success is True

    df = paper_trades.load_paper_trades_df(manual_config)
    assert df.empty


# --- execution-time re-checks: price moved, circuit breaker, duplicate --------------


def test_price_moved_too_far_blocks_and_leaves_pending_retryable(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 150.0)  # signal entry was 100.0
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is False
    assert "PRICE_MOVED_TOO_FAR" in message
    assert broker.submitted_intents == []
    records = paper_trades.load_pending_approvals(manual_config)
    assert records[paper_trades._key("AMD", "2026-09-09")]["status"] == "PENDING"


def test_missing_current_price_blocks_rather_than_submitting_blind(manual_config, monkeypatch):
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: None)
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is False
    assert broker.submitted_intents == []


def test_manual_kill_switch_blocks_manual_approval(manual_config, monkeypatch):
    from src.execution import circuit_breaker

    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    circuit_breaker.halt(manual_config, reason="test halt")
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)

    assert success is False
    assert broker.submitted_intents == []


def test_duplicate_approval_is_blocked_second_time(manual_config, monkeypatch):
    """Simulates a double-tap / retry after a successful submission: the
    second attempt must not submit a second order."""
    monkeypatch.setattr(data_collector, "fetch_current_price", lambda symbol, logger: 100.0)
    seed_pending(manual_config, "AMD")
    broker = paper_broker()

    journal_path = manual_config["execution"]["journal_path"]
    journal = order_manager.ExecutionJournal(journal_path)
    manager = order_manager.OrderManager(broker, manual_config, journal)

    first = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)
    assert first[0] is True
    assert len(broker.submitted_intents) == 1

    # The pending record is now APPROVED (terminal) - a second click on
    # the exact same button must be refused by peek_pending_decision()
    # before ever reaching the broker again.
    second = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=broker, now=FIXED_NOW)
    assert second[0] is False
    assert len(broker.submitted_intents) == 1  # still just the one


# --- aggressive-mode re-check (Phase 3 invariant, preserved through the new path) --


def test_aggressive_disabled_blocks_manual_approval_before_any_broker_contact(manual_config):
    class _PoisonedBroker:
        def __getattr__(self, name):
            raise AssertionError(f"Broker.{name} must never be touched for an aggressive-blocked approval.")

    manual_config["strategies"] = {"mean_reversion": {"aggressive_mode": {"enabled": False}}}
    seed_pending(manual_config, "GME", is_aggressive=True)

    success, message = approval_bridge.handle_manual_approval("approve", "GME", "2026-09-09", manual_config, logger, broker=_PoisonedBroker(), now=FIXED_NOW)

    assert success is False
    assert "Aggressive" in message


# --- connection failure leaves the pending record retryable -------------------------


def test_connection_failure_leaves_pending_approval_retryable(manual_config, monkeypatch):
    seed_pending(manual_config, "AMD")

    class _FailingBroker:
        def connect(self):
            raise ConnectionRefusedError("TWS not reachable")

    import src.execution.ibkr_client as ibkr_client

    def fake_ibkr_client(*args, **kwargs):
        return _FailingBroker()

    monkeypatch.setattr(ibkr_client, "IBKRClient", fake_ibkr_client)

    success, message = approval_bridge.handle_manual_approval("approve", "AMD", "2026-09-09", manual_config, logger, broker=None, now=FIXED_NOW)

    assert success is False
    assert "could not connect" in message
    records = paper_trades.load_pending_approvals(manual_config)
    assert records[paper_trades._key("AMD", "2026-09-09")]["status"] == "PENDING"
