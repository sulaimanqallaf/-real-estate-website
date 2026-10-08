"""Unit tests for approval_listener.py's per-update handling (not the infinite
poll loop itself, which isn't unit-testable - same precedent as main.run() vs.
main.analyze_symbol elsewhere in this test suite)."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import approval_listener, paper_trades, telegram_bot
from src.strategies.mean_reversion import STRATEGY_NAME_SAFE
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test_approval_listener")


def fresh_config(tmp_path):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    return config


def make_entry(symbol="NVDA"):
    return {
        "symbol": symbol,
        "label": "Strong candidate",
        "score": 85,
        "best_risk_result": {
            "strategy": STRATEGY_NAME_SAFE,
            "tradeable": True,
            "entry": 100.0,
            "stop_loss": 95.0,
            "target": 115.0,
            "risk_reward": 3.0,
            "expected_upside_pct": 15.0,
            "expected_downside_pct": 5.0,
            "shares": 20,
            "dollar_risk": 100.0,
        },
    }


def make_callback_update(action, symbol, report_date, chat_id="123", message_id=42, text="Paper Trade Approval"):
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq1",
            "data": paper_trades.encode_callback_data(action, symbol, report_date),
            "message": {
                "message_id": message_id,
                "text": text,
                "chat": {"id": int(chat_id)},
            },
        },
    }


def test_handle_update_ignores_non_callback_updates(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    calls = []
    monkeypatch.setattr(telegram_bot, "answer_callback_query", lambda *a, **k: calls.append("answer"))
    monkeypatch.setattr(telegram_bot, "edit_message_text", lambda *a, **k: calls.append("edit"))

    approval_listener.handle_update({"update_id": 1, "message": {"text": "hi"}}, "TOKEN", "123", config, LOGGER)

    assert calls == []


def test_handle_update_processes_a_valid_approval(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    record = paper_trades.pending_record_from_entry(make_entry(), "2026-09-09", message_id=42, chat_id="123")
    paper_trades.save_pending_approval(record, config)

    answered = {}
    edited = {}
    monkeypatch.setattr(
        telegram_bot, "answer_callback_query", lambda token, cbid, text, logger: answered.setdefault("text", text)
    )
    monkeypatch.setattr(
        telegram_bot,
        "edit_message_text",
        lambda token, chat_id, message_id, text, logger: edited.setdefault("text", text),
    )

    update = make_callback_update("approve", "NVDA", "2026-09-09")
    approval_listener.handle_update(update, "TOKEN", "123", config, LOGGER)

    assert "recorded in paper_trades.csv" in answered["text"]
    assert "✅" in edited["text"]

    records = paper_trades.load_pending_approvals(config)
    assert records["2026-09-09|NVDA"]["status"] == "APPROVED"


def test_handle_update_rejects_callback_from_unauthorized_chat(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    record = paper_trades.pending_record_from_entry(make_entry(), "2026-09-09", message_id=42, chat_id="123")
    paper_trades.save_pending_approval(record, config)

    answered = {}
    monkeypatch.setattr(
        telegram_bot, "answer_callback_query", lambda token, cbid, text, logger: answered.setdefault("text", text)
    )
    monkeypatch.setattr(telegram_bot, "edit_message_text", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("edit_message_text should never be called for an unauthorized chat")
    ))

    # Configured chat_id is "123"; this update claims to come from a different chat.
    update = make_callback_update("approve", "NVDA", "2026-09-09", chat_id="999")
    approval_listener.handle_update(update, "TOKEN", "123", config, LOGGER)

    assert answered["text"] == "Not authorized."
    # The pending record must be untouched - no decision was recorded.
    records = paper_trades.load_pending_approvals(config)
    assert records["2026-09-09|NVDA"]["status"] == "PENDING"


def test_handle_update_reports_unrecognized_button_gracefully(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    answered = {}
    monkeypatch.setattr(
        telegram_bot, "answer_callback_query", lambda token, cbid, text, logger: answered.setdefault("text", text)
    )
    monkeypatch.setattr(telegram_bot, "edit_message_text", lambda *a, **k: None)

    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cbq1",
            "data": "garbage-not-our-format",
            "message": {"message_id": 42, "text": "x", "chat": {"id": 123}},
        },
    }
    approval_listener.handle_update(update, "TOKEN", "123", config, LOGGER)

    assert answered["text"] == "Unrecognized button."


def test_offset_persists_across_load_save(tmp_path):
    config = fresh_config(tmp_path)
    assert approval_listener._load_offset(config) == 0
    approval_listener._save_offset(config, 555)
    assert approval_listener._load_offset(config) == 555


# --- singleton lock: exactly one approval_listener may poll Telegram at a time ------


def test_singleton_lock_rejects_a_second_instance(tmp_path):
    config = fresh_config(tmp_path)
    first = approval_listener._acquire_singleton_lock(config)
    try:
        with pytest.raises(approval_listener.ApprovalListenerAlreadyRunningError, match="409"):
            approval_listener._acquire_singleton_lock(config)
    finally:
        first.close()


def test_singleton_lock_can_be_reacquired_after_release(tmp_path):
    config = fresh_config(tmp_path)
    first = approval_listener._acquire_singleton_lock(config)
    first.close()

    second = approval_listener._acquire_singleton_lock(config)
    second.close()  # must not raise - the first holder released it cleanly


def test_singleton_lock_writes_the_current_pid(tmp_path):
    config = fresh_config(tmp_path)
    lock_file = approval_listener._acquire_singleton_lock(config)
    try:
        recorded_pid = approval_listener._lock_path(config).read_text().strip()
        assert recorded_pid == str(os.getpid())
    finally:
        lock_file.close()
