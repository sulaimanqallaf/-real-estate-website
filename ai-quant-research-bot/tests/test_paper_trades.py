"""Unit tests for paper_trades.py: the Telegram approval flow's core logic.

Covers the callback_data scheme, the pending-approval store, and process_decision's
gating - in particular that an Aggressive candidate can never be approved while
aggressive_mode.enabled is false, even if it was true when the message was sent.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import paper_trades
from src.strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config(tmp_path):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    return config


def make_entry(symbol="NVDA", strategy=STRATEGY_NAME_SAFE, label="Strong candidate", score=85):
    return {
        "symbol": symbol,
        "label": label,
        "score": score,
        "best_risk_result": {
            "strategy": strategy,
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


# --- callback_data ------------------------------------------------------------


def test_encode_decode_round_trip():
    data = paper_trades.encode_callback_data("approve", "NVDA", "2026-09-09")
    assert paper_trades.decode_callback_data(data) == ("approve", "NVDA", "2026-09-09")


def test_encode_rejects_unknown_action():
    with pytest.raises(ValueError):
        paper_trades.encode_callback_data("yolo", "NVDA", "2026-09-09")


def test_decode_returns_none_for_garbage():
    assert paper_trades.decode_callback_data("not-a-valid-payload") is None
    assert paper_trades.decode_callback_data("pt:approve:NVDA") is None  # missing report_date
    assert paper_trades.decode_callback_data("pt:yolo:NVDA:2026-09-09") is None  # bad action


def test_callback_data_stays_within_telegram_64_byte_limit():
    data = paper_trades.encode_callback_data("approve", "GOOGL", "2026-09-09")
    assert len(data.encode("utf-8")) <= 64


def test_build_approval_keyboard_has_three_buttons():
    keyboard = paper_trades.build_approval_keyboard("NVDA", "2026-09-09")
    buttons = [b for row in keyboard["inline_keyboard"] for b in row]
    assert len(buttons) == 3
    labels = {b["text"] for b in buttons}
    assert any("Approve" in label for label in labels)
    assert any("Reject" in label for label in labels)
    assert any("Watch Only" in label for label in labels)


def test_format_approval_message_flags_aggressive_candidates():
    safe_msg = paper_trades.format_approval_message(make_entry(strategy=STRATEGY_NAME_SAFE))
    aggressive_msg = paper_trades.format_approval_message(make_entry(strategy=STRATEGY_NAME_AGGRESSIVE))
    assert "AGGRESSIVE" not in safe_msg
    assert "AGGRESSIVE" in aggressive_msg


# --- pending-approval store ----------------------------------------------------


def test_save_and_load_pending_approval_round_trip(tmp_path):
    config = fresh_config(tmp_path)
    entry = make_entry()
    record = paper_trades.pending_record_from_entry(entry, "2026-09-09", message_id=42, chat_id="123")
    paper_trades.save_pending_approval(record, config)

    loaded = paper_trades.load_pending_approvals(config)
    assert loaded["2026-09-09|NVDA"]["message_id"] == 42
    assert loaded["2026-09-09|NVDA"]["status"] == "PENDING"
    assert loaded["2026-09-09|NVDA"]["is_aggressive"] is False


def test_pending_record_flags_aggressive_strategy(tmp_path):
    entry = make_entry(strategy=STRATEGY_NAME_AGGRESSIVE)
    record = paper_trades.pending_record_from_entry(entry, "2026-09-09", message_id=1, chat_id="123")
    assert record["is_aggressive"] is True


def test_expired_pending_approval_is_pruned_on_load(tmp_path):
    config = fresh_config(tmp_path)
    config["paper_trading"]["pending_expiry_hours"] = 0  # anything already saved is instantly "expired"
    entry = make_entry()
    record = paper_trades.pending_record_from_entry(entry, "2026-09-09", message_id=1, chat_id="123")
    paper_trades.save_pending_approval(record, config)

    loaded = paper_trades.load_pending_approvals(config)
    assert loaded == {}


# --- process_decision -----------------------------------------------------------


def _seed_pending(config, entry, report_date="2026-09-09", message_id=1, chat_id="123"):
    record = paper_trades.pending_record_from_entry(entry, report_date, message_id, chat_id)
    paper_trades.save_pending_approval(record, config)
    return record


def test_approve_safe_candidate_writes_paper_trade(tmp_path, caplog):
    import logging

    config = fresh_config(tmp_path)
    _seed_pending(config, make_entry(symbol="NVDA", strategy=STRATEGY_NAME_SAFE))

    success, message = paper_trades.process_decision("approve", "NVDA", "2026-09-09", config, logging.getLogger("t"))

    assert success is True
    assert "recorded in paper_trades.csv" in message
    trades_path = Path(config["data"]["journal_dir"]) / config["paper_trading"]["paper_trades_file"]
    df = pd.read_csv(trades_path)
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "NVDA"
    assert df.iloc[0]["status"] == "OPEN"

    # The pending record itself should now reflect the decision.
    records = paper_trades.load_pending_approvals(config)
    assert records["2026-09-09|NVDA"]["status"] == "APPROVED"


def test_approve_aggressive_candidate_succeeds_when_enabled(tmp_path):
    import logging

    config = fresh_config(tmp_path)
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True
    _seed_pending(config, make_entry(symbol="SPY", strategy=STRATEGY_NAME_AGGRESSIVE))

    success, message = paper_trades.process_decision("approve", "SPY", "2026-09-09", config, logging.getLogger("t"))

    assert success is True
    trades_path = Path(config["data"]["journal_dir"]) / config["paper_trading"]["paper_trades_file"]
    assert trades_path.exists()
    assert pd.read_csv(trades_path).iloc[0]["is_aggressive"] == True  # noqa: E712


def test_approve_aggressive_candidate_blocked_when_disabled_at_decision_time(tmp_path):
    """The critical guarantee: even if aggressive_mode.enabled was true when the
    message was sent, disabling it before the button is tapped must block approval."""
    import logging

    config = fresh_config(tmp_path)
    # Seed as if the candidate had been sent while aggressive mode was enabled...
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True
    _seed_pending(config, make_entry(symbol="SPY", strategy=STRATEGY_NAME_AGGRESSIVE))
    # ...then the operator disables it before the user gets around to tapping Approve.
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = False

    success, message = paper_trades.process_decision("approve", "SPY", "2026-09-09", config, logging.getLogger("t"))

    assert success is False
    assert "cannot be approved" in message
    trades_path = Path(config["data"]["journal_dir"]) / config["paper_trading"]["paper_trades_file"]
    assert not trades_path.exists()  # nothing was ever written

    records = paper_trades.load_pending_approvals(config)
    assert records["2026-09-09|SPY"]["status"] == "BLOCKED_AGGRESSIVE_DISABLED"


def test_reject_and_watch_only_never_write_a_paper_trade(tmp_path):
    import logging

    config = fresh_config(tmp_path)
    _seed_pending(config, make_entry(symbol="AAPL"))

    success, message = paper_trades.process_decision("reject", "AAPL", "2026-09-09", config, logging.getLogger("t"))
    assert success is True
    assert "rejected" in message.lower()

    trades_path = Path(config["data"]["journal_dir"]) / config["paper_trading"]["paper_trades_file"]
    assert not trades_path.exists()

    _seed_pending(config, make_entry(symbol="MSFT"))
    success, message = paper_trades.process_decision("watch", "MSFT", "2026-09-09", config, logging.getLogger("t"))
    assert success is True
    assert "watch" in message.lower()
    assert not trades_path.exists()


def test_double_decision_is_refused(tmp_path):
    import logging

    config = fresh_config(tmp_path)
    _seed_pending(config, make_entry(symbol="NVDA"))
    logger = logging.getLogger("t")

    first_success, _ = paper_trades.process_decision("approve", "NVDA", "2026-09-09", config, logger)
    assert first_success is True

    second_success, second_message = paper_trades.process_decision("approve", "NVDA", "2026-09-09", config, logger)
    assert second_success is False
    assert "already marked" in second_message

    trades_path = Path(config["data"]["journal_dir"]) / config["paper_trading"]["paper_trades_file"]
    assert len(pd.read_csv(trades_path)) == 1  # not double-recorded


def test_unknown_pending_approval_is_refused(tmp_path):
    import logging

    config = fresh_config(tmp_path)
    success, message = paper_trades.process_decision("approve", "GHOST", "2026-09-09", config, logging.getLogger("t"))
    assert success is False
    assert "No pending approval found" in message
