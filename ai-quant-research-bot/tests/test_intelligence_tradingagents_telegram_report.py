"""src/intelligence/tradingagents_telegram_report.py - GitHub Issue #1
follow-up: a safe, idempotent, dry-run-by-default command to deliver the
cached TradingAgents preview to Telegram. Every test proves: dry-run by
default, --send required and explicit, duplicate sends blocked by a
content-based idempotency key, Telegram length limits handled via the
EXISTING telegram_bot helpers, and zero OpenAI/IBKR calls regardless."""

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import tradingagents_adapter as ta
from src.intelligence import tradingagents_telegram_report as tr

logger = logging.getLogger("test")


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}, "intelligence": {"tradingagents": {"enabled": True}}}


def _seed_cache(config, ticker, report_date, raw, now=None):
    now = now or datetime(2026, 10, 8, tzinfo=timezone.utc)
    db_path = ta.resolve_state_db_path(config)
    key = ta._cache_key(ticker, report_date, ["market", "social", "news", "fundamentals"], {})
    ta._cache_set(db_path, key, raw, now, ticker=ticker, report_date=report_date)


_RAW = {
    "ok": True, "final_rating": "Sell",
    "bull_history": "## Bull\n\nMargins improving.", "bear_history": "## Bear\n\nCompetitive pressure.",
    "token_usage": {"gpt-6-sol": {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": 0}},
}


@pytest.fixture
def fake_telegram(monkeypatch):
    sent = {"calls": []}

    def fake_send(token, chat_id, text, lg):
        sent["calls"].append((token, chat_id, text))
        return True

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", fake_send)
    return sent


# --- idempotency key -----------------------------------------------------------------------


def test_compute_idempotency_key_is_stable_for_identical_text():
    assert tr.compute_idempotency_key("hello") == tr.compute_idempotency_key("hello")


def test_compute_idempotency_key_differs_for_different_text():
    assert tr.compute_idempotency_key("hello") != tr.compute_idempotency_key("hello!")


def test_was_already_sent_is_false_before_anything_is_recorded(config):
    assert tr.was_already_sent(config, "some-key") is False


# --- send_cached_report: dry-run reuse, idempotency, chunking -------------------------------


def test_send_cached_report_sends_once_and_blocks_an_identical_resend(config, fake_telegram):
    _seed_cache(config, "AMD", "2026-10-08", _RAW)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    first = tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    assert first["sent"] is True
    assert len(fake_telegram["calls"]) == 1

    second = tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    assert second["sent"] is False
    assert "duplicate" in second["reason"]
    assert len(fake_telegram["calls"]) == 1  # no new Telegram call made


def test_send_cached_report_force_bypasses_the_idempotency_check(config, fake_telegram):
    _seed_cache(config, "AMD", "2026-10-08", _RAW)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    forced = tr.send_cached_report(config, logger, "TOKEN", "CHAT", force=True, now=now)
    assert forced["sent"] is True
    assert len(fake_telegram["calls"]) == 2


def test_send_cached_report_allows_a_new_send_once_cached_content_actually_changes(config, fake_telegram):
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    _seed_cache(config, "AMD", "2026-10-08", _RAW)
    tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)

    _seed_cache(config, "QQQ", "2026-10-08", {**_RAW, "final_rating": "Overweight"})
    second = tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    assert second["sent"] is True  # different content -> different idempotency key -> allowed
    assert len(fake_telegram["calls"]) == 2


def test_send_cached_report_reuses_the_exact_preview_formatting(config, fake_telegram, monkeypatch):
    """The literal requirement: "reuse the exact report-preview
    formatting logic" - never a separate reimplementation."""
    _seed_cache(config, "AMD", "2026-10-08", _RAW)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    from src.intelligence import tradingagents_preview

    expected_text = tradingagents_preview.format_preview(config, logger)
    tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    assert fake_telegram["calls"][0][2] == expected_text


def test_send_cached_report_never_records_a_send_on_telegram_failure(config, monkeypatch):
    _seed_cache(config, "AMD", "2026-10-08", _RAW)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("src.telegram_bot.send_telegram_message", lambda *a, **k: False)

    result = tr.send_cached_report(config, logger, "TOKEN", "CHAT", now=now)
    assert result["sent"] is False
    assert "failed" in result["reason"]
    assert tr.was_already_sent(config, result["idempotency_key"]) is False  # safe to retry


def test_send_cached_report_handles_long_content_by_splitting_into_multiple_messages(config, fake_telegram):
    """GitHub Issue #1 follow-up: "handle Telegram message length
    limits" - reuses telegram_bot's own chunk_message()/send_report(),
    never truncates data or reimplements chunking."""
    for i in range(30):
        _seed_cache(config, f"TICK{i}", "2026-10-08", {**_RAW, "bull_history": "Long bull case. " * 50, "bear_history": "Long bear case. " * 50})
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    result = tr.send_cached_report(config, logger, "TOKEN", "CHAT", max_message_chars=1000, now=now)
    assert result["sent"] is True
    assert result["chunks"] > 1
    assert len(fake_telegram["calls"]) == result["chunks"]
    # Each individual chunk actually sent respects the Telegram hard limit.
    for _, _, text in fake_telegram["calls"]:
        assert len(text) <= 4096


def test_send_cached_report_still_works_with_nothing_cached(config, fake_telegram):
    result = tr.send_cached_report(config, logger, "TOKEN", "CHAT")
    assert result["sent"] is True
    assert "No cached TradingAgents results found" in fake_telegram["calls"][0][2]


# --- hard guarantees: no OpenAI/IBKR calls, ever --------------------------------------------


def test_send_cached_report_never_calls_a_subprocess(config, fake_telegram, monkeypatch):
    _seed_cache(config, "AMD", "2026-10-08", _RAW)

    def boom(*a, **k):
        raise AssertionError("sending the cached report must never shell out to a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    tr.send_cached_report(config, logger, "TOKEN", "CHAT")  # must not raise


@pytest.mark.parametrize("relative_path", [
    "src/execution/circuit_breaker.py", "src/execution/execution_policy.py", "src/execution/order_manager.py",
    "src/execution/approval_bridge.py", "src/execution/position_monitor.py", "src/execution/ibkr_client.py",
    "src/portfolio_risk.py", "src/risk_manager.py",
])
def test_execution_and_risk_files_never_mention_this_module(relative_path):
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / relative_path).read_text(encoding="utf-8").lower()
    assert "tradingagents_telegram_report" not in text


def test_module_itself_never_imports_execution_or_broker_code():
    import ast

    source = Path(tr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    forbidden = ("execution", "ibkr", "circuit_breaker", "broker")
    assert not any(any(f in name.lower() for f in forbidden) for name in imported_names)


def test_preview_module_itself_still_never_imports_telegram_bot():
    """The module split must hold: tradingagents_preview.py's own
    "never sends Telegram" invariant is untouched by adding this
    SEPARATE delivery module."""
    import ast

    from src.intelligence import tradingagents_preview

    source = Path(tradingagents_preview.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert not any("telegram" in name.lower() for name in imported_names)
