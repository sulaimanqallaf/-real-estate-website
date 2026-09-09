"""Phase 7 Part T - Telegram command center: /status, /positions, /orders,
/performance, /halt, /resume. Authorized-chat-only enforcement is tested at
the approval_listener.handle_update() level (mirrors the existing button-
press authorization check); the formatting functions themselves are tested
directly here.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import approval_listener
from src.execution import circuit_breaker, order_manager, telegram_commands

logger = logging.getLogger("test")


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "execution": {"mode": "DRY_RUN", "journal_path": str(tmp_path / "executions.jsonl"), "halt_state_file": str(tmp_path / "halt.json")},
        "autonomous_paper": {"enabled": False, "auto_execute": {"enabled": False}},
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"},
    }


def test_parse_command_recognizes_all_six_commands():
    for cmd in ("/status", "/positions", "/orders", "/performance", "/halt", "/resume"):
        assert telegram_commands.parse_command(cmd) == cmd


def test_parse_command_is_case_insensitive_and_ignores_trailing_args():
    assert telegram_commands.parse_command("/STATUS") == "/status"
    assert telegram_commands.parse_command("/halt now please") == "/halt"


def test_parse_command_returns_none_for_ordinary_text():
    assert telegram_commands.parse_command("hello there") is None
    assert telegram_commands.parse_command("") is None


def test_status_reports_mode_and_manual_halt_state(config):
    reply = telegram_commands.format_status_reply(config)
    assert "DRY_RUN" in reply
    assert "disabled" in reply  # autonomous/auto-execute both disabled in this fixture


def test_status_reports_halted_after_halt_called(config):
    circuit_breaker.halt(config, reason="test reason")
    reply = telegram_commands.format_status_reply(config)
    assert "YES" in reply
    assert "test reason" in reply


def test_positions_reports_no_open_positions_when_csv_empty(config):
    reply = telegram_commands.format_positions_reply(config)
    assert "No open positions" in reply


def test_positions_lists_open_rows(config):
    from src import paper_trades

    record = {
        "symbol": "AMD", "strategy": "Trend Following", "signal": "Top Candidate", "score": 90,
        "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "risk_reward": 3.0,
        "shares": 10, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
        "report_date": "2026-09-09", "decided_at": None,
    }
    paper_trades.record_paper_trade(record, config)
    reply = telegram_commands.format_positions_reply(config)
    assert "AMD" in reply
    assert "100.0" in reply


def test_orders_reports_no_active_orders_when_journal_empty(config):
    reply = telegram_commands.format_orders_reply(config)
    assert "No active orders" in reply


def test_orders_lists_active_managed_orders_from_journal(config):
    from src.execution import order_state
    from src.execution.broker import FakeBroker
    from datetime import datetime, timezone

    broker = FakeBroker()
    broker.connect()
    journal = order_manager.ExecutionJournal(config["execution"]["journal_path"])
    manager = order_manager.OrderManager(broker, {"execution_risk": {"max_risk_per_trade_pct": 0.05}, "risk": {"account_equity": 10_000}}, journal)
    intent = order_state.OrderIntent(
        intent_id=order_state.new_intent_id(), ticker="AMD", side="BUY", quantity=10, order_type="LIMIT",
        entry_price=100.0, stop_loss=95.0, target_price=115.0, strategy="Trend Following", signal_score=90,
        quant_score=None, risk_amount=50.0, created_at=datetime.now(timezone.utc), account_mode_at_creation="PAPER",
    )
    manager.submit_entry(intent)
    reply = telegram_commands.format_orders_reply(config)
    assert "AMD" in reply
    assert "ACKNOWLEDGED" in reply


def test_performance_reports_no_data_when_no_closed_trades(config):
    reply = telegram_commands.format_performance_reply(config)
    assert "No closed trades" in reply


def test_halt_reply_actually_halts(config):
    reply = telegram_commands.format_halt_reply(config, reason="Telegram /halt")
    assert "HALTED" in reply
    halted, reason = circuit_breaker.is_halted(config)
    assert halted is True
    assert reason == "Telegram /halt"


def test_resume_reply_clears_manual_halt_but_states_it_never_overrides_hard_blocks(config):
    circuit_breaker.halt(config, reason="pre-existing halt")
    reply = telegram_commands.format_resume_reply(config)
    assert "does NOT override" in reply
    halted, _ = circuit_breaker.is_halted(config)
    assert halted is False


def test_handle_text_command_returns_none_for_unrecognized_text(config):
    assert telegram_commands.handle_text_command("just chatting", config, logger) is None


def test_handle_text_command_dispatches_status(config):
    reply = telegram_commands.handle_text_command("/status", config, logger)
    assert "STATUS" in reply


# --- authorization wiring at the approval_listener level -----------------------------


def test_unauthorized_chat_cannot_halt_trading(config, monkeypatch):
    update = {"message": {"chat": {"id": 999}, "text": "/halt"}}
    handled = approval_listener._handle_text_message(update, token="tok", chat_id="12345", config=config, logger=logger)
    assert handled is True
    halted, _ = circuit_breaker.is_halted(config)
    assert halted is False  # unauthorized chat's /halt must never actually take effect


def test_authorized_chat_can_halt_trading(config, monkeypatch):
    sent = {}

    def fake_send(token, chat_id, text, logger):
        sent["text"] = text
        return True

    import src.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot, "send_telegram_message", fake_send)
    update = {"message": {"chat": {"id": 12345}, "text": "/halt"}}
    handled = approval_listener._handle_text_message(update, token="tok", chat_id="12345", config=config, logger=logger)
    assert handled is True
    halted, _ = circuit_breaker.is_halted(config)
    assert halted is True
    assert "HALTED" in sent["text"]


def test_ordinary_message_is_not_treated_as_a_command(config):
    update = {"message": {"chat": {"id": 12345}, "text": "hi bot"}}
    handled = approval_listener._handle_text_message(update, token="tok", chat_id="12345", config=config, logger=logger)
    assert handled is False


def test_handle_update_routes_text_commands_before_callback_query_check(config, monkeypatch):
    import src.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot, "send_telegram_message", lambda *a, **k: True)
    update = {"message": {"chat": {"id": 12345}, "text": "/status"}}
    # Must not raise even though there is no callback_query key at all.
    approval_listener.handle_update(update, token="tok", chat_id="12345", config=config, logger=logger)
