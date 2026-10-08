"""Phase 7 Part Z - Approval category: stale approval re-check, duplicate
button click safe, price-moved blocked. (Authorized-chat-only enforcement
is Phase 3's approval_listener.py, already covered by test_approval_listener.py
- this file only covers what's new in execution/approval_bridge.py.)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import approval_bridge, order_manager
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, FakeBroker

logger = logging.getLogger("test")

PERMISSIVE_CONFIG = {
    "execution_risk": {"max_risk_per_trade_pct": 0.05},
    "risk": {"account_equity": 10_000},
    "execution": {"trading_hours_start": "00:00", "trading_hours_end": "23:59"},
}

# A fixed Wednesday, mid-session NY time - injected into every
# execute_approved_trade() call below that expects to pass the trading-
# hours check, so the result never depends on the real wall-clock weekday
# the suite happens to run on (the "00:00-23:59" window above only widens
# the HOUR check - is_within_trading_hours() still hard-blocks Sat/Sun
# regardless of configured hours).
FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def good_record(**overrides):
    record = {
        "symbol": "AMD", "strategy": "Trend Following", "score": 90,
        "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
        "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
    }
    record.update(overrides)
    return record


@pytest.fixture
def broker():
    b = FakeBroker(account_mode=ACCOUNT_MODE_PAPER, account_id="DU1")
    b.connect()
    return b


@pytest.fixture
def manager(broker):
    return order_manager.OrderManager(broker, PERMISSIVE_CONFIG)


def test_valid_approval_executes_against_paper_broker(broker, manager):
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is True
    assert len(broker.submitted_intents) == 1


def test_stale_approval_reexecutes_only_if_account_mode_still_paper(manager):
    live_broker = FakeBroker(account_mode=ACCOUNT_MODE_LIVE, account_id="U1")
    live_broker.connect()
    manager2 = order_manager.OrderManager(live_broker, PERMISSIVE_CONFIG)
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, live_broker, manager2, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "LIVE_ACCOUNT_BLOCKED" in result["reasons"]
    assert live_broker.submitted_intents == []


def test_stale_approval_blocked_when_broker_disconnected(broker, manager):
    broker.disconnect()
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "BROKER_DISCONNECTED" in result["reasons"]


def test_price_moved_too_far_blocks_execution(broker, manager):
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=150.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "PRICE_MOVED_TOO_FAR" in result["reasons"]
    assert broker.submitted_intents == []


def test_missing_current_price_blocks_execution_never_submits_blind(broker, manager):
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=None, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "PRICE_MOVED_TOO_FAR" in result["reasons"]


def test_outside_trading_hours_blocks_execution(broker, manager):
    config = dict(PERMISSIVE_CONFIG)
    config["execution"] = {"trading_hours_start": "09:30", "trading_hours_end": "09:31"}
    result = approval_bridge.execute_approved_trade(good_record(), config, broker, manager, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "OUTSIDE_TRADING_HOURS" in result["reasons"]


def test_duplicate_button_click_is_safe_second_execution_never_double_submits(broker, manager):
    result1 = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=100.0, logger=logger, trade_id="AMD_2026-09-09_zzzz", now=FIXED_NOW)
    assert result1["executed"] is True
    result2 = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=100.0, logger=logger, trade_id="AMD_2026-09-09_zzzz", now=FIXED_NOW)
    assert result2["executed"] is False
    assert "DUPLICATE_INTENT" in result2["reasons"]
    assert len(broker.submitted_intents) == 1  # the second click never reached the broker


def test_circuit_breaker_halt_blocks_execution(tmp_path, broker, manager):
    from src.execution import circuit_breaker

    config = dict(PERMISSIVE_CONFIG)
    config["execution"] = {**PERMISSIVE_CONFIG["execution"], "halt_state_file": str(tmp_path / "halt.json")}
    circuit_breaker.halt(config, reason="test")
    result = approval_bridge.execute_approved_trade(good_record(), config, broker, manager, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    assert result["executed"] is False
    assert "MANUAL_KILL_SWITCH" in result["reasons"]


def test_format_auto_execution_notice_is_never_silent_and_includes_key_fields(broker, manager):
    result = approval_bridge.execute_approved_trade(good_record(), PERMISSIVE_CONFIG, broker, manager, current_market_price=100.0, logger=logger, now=FIXED_NOW)
    notice = approval_bridge.format_auto_execution_notice(good_record(), result["managed"], account_risk_pct=0.005)
    assert "AMD" in notice
    assert "AUTO PAPER TRADE EXECUTED" in notice
    assert "PAPER trade only" in notice
    assert result["managed"].entry_broker_order_id in notice
