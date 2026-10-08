"""Phase 7 continuation - Telegram approval buttons for DRY_RUN: only a
candidate whose order review says "Would submit: YES" gets an Approve/
Reject/Watch Only button; approving one writes to paper_trades.csv only
and never contacts IBKR, no matter what.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import src.approval_listener as approval_listener
import src.paper_trades as paper_trades
from src.main import _process_execution_layer, _send_paper_trade_approvals

logger = logging.getLogger("test")

# A fixed Wednesday, mid-session NY time - AMD's "would_submit: YES"
# review depends on passing the trading-hours check, which the config's
# "00:00-23:59" window alone cannot guarantee (is_within_trading_hours()
# hard-blocks Sat/Sun regardless of configured hours).
FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def qa(confidence="VERY_HIGH", edge="POSITIVE"):
    return type("QA", (), {"ml_confidence": confidence, "strategy_edge": edge})()


def make_entry(symbol, **overrides):
    entry = {
        "symbol": symbol,
        "label": "Top Candidate",
        "score": 90,
        "best_risk_result": {"tradeable": True, "strategy": "Trend Following"},
        "regime_evaluation": {"blocked": False, "regime": "TRENDING_UP"},
        "portfolio_evaluation": {
            "decision": "ACCEPT",
            "position": {
                "strategy": "Trend Following", "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
                "shares": 10, "dollar_risk": 50.0, "risk_reward": 3.0,
            },
        },
        "quant_assessment": qa(),
    }
    entry.update(overrides)
    return entry


def pending_record(symbol: str, report_date: str = "2026-09-09", **overrides) -> dict:
    """Matches paper_trades.pending_record_from_entry()'s exact shape -
    every field process_decision()/record_paper_trade() actually read."""
    from datetime import datetime, timezone

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
def dry_run_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "execution": {
            "mode": "DRY_RUN", "journal_path": str(tmp_path / "executions.jsonl"),
            "trading_hours_start": "00:00", "trading_hours_end": "23:59",
        },
        # autonomous_paper intentionally omitted/false - DRY_RUN review runs
        # regardless, since it's purely informational and never auto-executes.
        "autonomous_paper": {"enabled": False, "auto_execute": {"enabled": False}},
        "execution_risk": {"max_risk_per_trade_pct": 0.05},
        "portfolio_risk": {},
        "risk": {"account_equity": 10_000},
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json", "pending_expiry_hours": 72},
        "telegram": {"top_candidates_limit": 10},
    }


# --- buttons only for "Would submit: YES" candidates --------------------------------


def test_valid_candidate_gets_an_approval_button(dry_run_config, monkeypatch):
    sent = []
    monkeypatch.setattr("src.telegram_bot.send_message_with_keyboard", lambda token, chat_id, text, keyboard, logger: sent.append(text) or 1)

    entry = make_entry("AMD")
    blocked = _process_execution_layer([entry], "2026-09-09", dry_run_config, logger, None, None, broker=None, now=FIXED_NOW)
    assert blocked == set()  # AMD is fully valid - not blocked

    _send_paper_trade_approvals([entry], "2026-09-09", "tok", "chat1", dry_run_config, logger, skip_tickers=blocked)
    assert any("AMD" in msg for msg in sent)


def test_blocked_candidate_gets_no_approval_button(dry_run_config, monkeypatch):
    sent = []
    monkeypatch.setattr("src.telegram_bot.send_message_with_keyboard", lambda token, chat_id, text, keyboard, logger: sent.append(text) or 1)

    # Risk amount far over the configured per-trade limit -> validate_intent
    # fails -> would_submit is False.
    entry = make_entry(
        "TSLA",
        portfolio_evaluation={
            "decision": "ACCEPT",
            "position": {"strategy": "Trend Following", "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 5000.0, "risk_reward": 3.0},
        },
    )
    blocked = _process_execution_layer([entry], "2026-09-09", dry_run_config, logger, None, None, broker=None, now=FIXED_NOW)
    assert blocked == {"TSLA"}

    _send_paper_trade_approvals([entry], "2026-09-09", "tok", "chat1", dry_run_config, logger, skip_tickers=blocked)
    assert sent == []  # no button, no message at all for a blocked candidate


def test_mixed_batch_only_valid_candidate_gets_a_button(dry_run_config, monkeypatch):
    sent = []
    monkeypatch.setattr("src.telegram_bot.send_message_with_keyboard", lambda token, chat_id, text, keyboard, logger: sent.append(text) or 1)

    valid_entry = make_entry("AMD")
    blocked_entry = make_entry(
        "TSLA",
        portfolio_evaluation={
            "decision": "ACCEPT",
            "position": {"strategy": "Trend Following", "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 5000.0, "risk_reward": 3.0},
        },
    )
    ticker_results = [valid_entry, blocked_entry]
    blocked = _process_execution_layer(ticker_results, "2026-09-09", dry_run_config, logger, None, None, broker=None, now=FIXED_NOW)
    assert blocked == {"TSLA"}

    _send_paper_trade_approvals(ticker_results, "2026-09-09", "tok", "chat1", dry_run_config, logger, skip_tickers=blocked)
    assert len(sent) == 1
    assert "AMD" in sent[0]
    assert "TSLA" not in sent[0]


# --- hard rule: approval writes to paper_trades.csv only, never touches IBKR -------


def test_approving_a_dry_run_candidate_writes_only_to_paper_trades_csv(dry_run_config):
    record = pending_record("AMD")
    key = paper_trades._key("AMD", "2026-09-09")
    paper_trades._save_all({key: record}, dry_run_config)

    success, message = paper_trades.process_decision("approve", "AMD", "2026-09-09", dry_run_config, logger)
    assert success is True

    df = paper_trades.load_paper_trades_df(dry_run_config)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "AMD"
    assert df.iloc[0]["status"] == "OPEN"

    # The execution journal must be untouched - approving in DRY_RUN never
    # creates an OrderIntent, a ManagedOrder, or any journal entry.
    journal_path = Path(dry_run_config["execution"]["journal_path"])
    assert not journal_path.exists()


def test_approval_path_never_constructs_a_real_ibkr_client(dry_run_config, monkeypatch):
    """Poisons IBKRClient.__init__ to raise if ever instantiated, then
    drives the full button-press flow (handle_update -> process_decision)
    for an 'approve' action. If anything in that path ever constructed a
    real broker client, this test would fail with the poisoned exception
    instead of succeeding normally."""
    from src.execution.ibkr_client import IBKRClient

    def poisoned_init(self, *args, **kwargs):
        raise AssertionError("IBKRClient must never be constructed by the Telegram approval path.")

    monkeypatch.setattr(IBKRClient, "__init__", poisoned_init)

    record = pending_record("AMD")
    key = paper_trades._key("AMD", "2026-09-09")
    paper_trades._save_all({key: record}, dry_run_config)

    update = {
        "callback_query": {
            "id": "cbq1",
            "data": paper_trades.encode_callback_data("approve", "AMD", "2026-09-09"),
            "message": {"chat": {"id": 12345}, "message_id": 1, "text": "AMD candidate"},
        }
    }

    import src.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot, "answer_callback_query", lambda *a, **k: True)
    monkeypatch.setattr(telegram_bot, "edit_message_text", lambda *a, **k: True)

    approval_listener.handle_update(update, token="tok", chat_id="12345", config=dry_run_config, logger=logger)

    df = paper_trades.load_paper_trades_df(dry_run_config)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "AMD"


def test_reject_and_watch_only_also_never_touch_ibkr(dry_run_config, monkeypatch):
    from src.execution.ibkr_client import IBKRClient

    def poisoned_init(self, *args, **kwargs):
        raise AssertionError("IBKRClient must never be constructed by the Telegram approval path.")

    monkeypatch.setattr(IBKRClient, "__init__", poisoned_init)

    import src.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot, "answer_callback_query", lambda *a, **k: True)
    monkeypatch.setattr(telegram_bot, "edit_message_text", lambda *a, **k: True)

    for action, symbol in (("reject", "MSFT"), ("watch", "NVDA")):
        record = pending_record(symbol)
        key = paper_trades._key(symbol, "2026-09-09")
        paper_trades._save_all({key: record}, dry_run_config)

        update = {
            "callback_query": {
                "id": f"cbq-{action}",
                "data": paper_trades.encode_callback_data(action, symbol, "2026-09-09"),
                "message": {"chat": {"id": 12345}, "message_id": 1, "text": f"{symbol} candidate"},
            }
        }
        approval_listener.handle_update(update, token="tok", chat_id="12345", config=dry_run_config, logger=logger)

    df = paper_trades.load_paper_trades_df(dry_run_config)
    assert df.empty  # neither reject nor watch-only ever creates a paper_trades.csv row
