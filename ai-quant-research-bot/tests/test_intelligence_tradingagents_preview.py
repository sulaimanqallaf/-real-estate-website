"""src/intelligence/tradingagents_preview.py - GitHub Issue #1 follow-up:
"validate the enhanced TradingAgents Telegram report using existing
cached results only." Every test here proves the preview is read-only
and cache-only: no subprocess call, no Telegram send, no execution-
affecting field ever touched."""

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import memory, tradingagents_adapter as ta, tradingagents_preview as preview

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


_FULL_RAW = {
    "ok": True, "final_rating": "Sell", "signal": "Sell",
    "reports": {"market": "AMD showing weakness."},
    "bull_history": "Bull: AMD margins improving.",
    "bear_history": "Bear: AMD facing competitive pressure.",
    "final_trade_decision": "Sell on weakening momentum.",
    "token_usage": {"gpt-6-sol": {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": 100, "calls": 1}},
    "estimated_cost_usd": {"total_usd": 0.03864, "by_model": {}, "unknown_models": []},
}


# --- build_preview_entries ---------------------------------------------------------------


def test_build_preview_entries_returns_empty_list_with_no_cache(config):
    assert preview.build_preview_entries(config, logger) == []


def test_build_preview_entries_builds_one_entry_per_cached_ticker(config):
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)
    _seed_cache(config, "QQQ", "2026-10-08", {**_FULL_RAW, "final_rating": "Overweight"})

    entries = preview.build_preview_entries(config, logger)
    symbols = {e["symbol"] for e in entries}
    assert symbols == {"AMD", "QQQ"}
    assert all(e["tradingagents_assessment"] is not None for e in entries)


def test_build_preview_entries_labels_unrecoverable_metadata_as_unknown(config):
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    # An orphan row with no matching request.json anywhere - metadata unrecoverable.
    ta._cache_set(db_path, "orphan-key-no-request-file", _FULL_RAW, now)

    entries = preview.build_preview_entries(config, logger)
    assert entries[0]["symbol"] == "UNKNOWN"
    assert entries[0]["tradingagents_assessment"].report_date == "UNKNOWN"


def test_build_preview_entries_skips_a_malformed_cache_row_without_crashing(config):
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    with ta._connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cache (cache_key, result_json, cached_at, ticker, report_date) VALUES (?, ?, ?, ?, ?)",
            ("badkey", "not valid json", now.isoformat(), "AMD", "2026-10-08"),
        )
    # list_cached_raw_results() already skips rows whose JSON fails to parse.
    assert preview.build_preview_entries(config, logger) == []


def test_build_preview_entries_includes_quant_comparison_when_the_deterministic_engine_has_a_matching_record(config):
    from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment

    det_db = memory.resolve_db_path(config)
    opinions = {"technical": AgentOpinion(analyst="technical", action="BUY", confidence=0.5, thesis="t")}
    memory.record_assessment(det_db, AgentResearchAssessment(
        ticker="AMD", report_date="2026-10-08", as_of="2026-10-08T00:00:00+00:00", action="BUY", confidence=0.5,
        thesis="t", bull_points=[], bear_points=[], risk_notes=[], analyst_opinions=opinions, data_provenance=[],
        quant_agent_decision="ELIGIBLE",
    ))
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)

    entries = preview.build_preview_entries(config, logger)
    assert entries[0]["tradingagents_assessment"].quant_agent_decision == "ELIGIBLE"


# --- format_preview ------------------------------------------------------------------------


def test_format_preview_reports_nothing_cached(config):
    text = preview.format_preview(config, logger)
    assert "No cached TradingAgents results found" in text


def test_format_preview_uses_the_real_report_writer_formatting_function(config, monkeypatch):
    """The whole point: it must call the SAME function the real report
    uses, not a reimplementation."""
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)
    calls = []

    import src.report_writer as report_writer

    original = report_writer.format_tradingagents_context_line

    def spy(entry):
        calls.append(entry)
        return original(entry)

    monkeypatch.setattr(report_writer, "format_tradingagents_context_line", spy)
    preview.format_preview(config, logger)
    assert len(calls) == 1
    assert calls[0]["symbol"] == "AMD"


def test_format_preview_shows_recommendation_bull_bear_quant_and_cost(config):
    from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment

    det_db = memory.resolve_db_path(config)
    opinions = {"technical": AgentOpinion(analyst="technical", action="SELL", confidence=0.5, thesis="t")}
    memory.record_assessment(det_db, AgentResearchAssessment(
        ticker="AMD", report_date="2026-10-08", as_of="2026-10-08T00:00:00+00:00", action="SELL", confidence=0.5,
        thesis="t", bull_points=[], bear_points=[], risk_notes=[], analyst_opinions=opinions, data_provenance=[],
        quant_agent_decision="NOT_ELIGIBLE",
    ))
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)

    text = preview.format_preview(config, logger)
    assert "SELL" in text
    assert "Bull case: Bull: AMD margins improving." in text
    assert "Bear case: Bear: AMD facing competitive pressure." in text
    assert "NOT_ELIGIBLE" in text and "agrees" in text  # SELL == mapped NOT_ELIGIBLE -> SELL
    assert "Estimated cost" in text
    assert "Token usage" in text


# --- hard guarantees: no API calls, no Telegram, no execution --------------------------


def test_preview_never_calls_a_subprocess(config, monkeypatch):
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)

    def boom(*a, **k):
        raise AssertionError("preview must never shell out to a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    preview.format_preview(config, logger)  # must not raise


def test_preview_module_never_imports_telegram_bot():
    import ast

    source = Path(preview.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert not any("telegram" in name.lower() for name in imported_names)


def test_preview_module_never_imports_execution_or_broker_code():
    import ast

    source = Path(preview.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    forbidden = ("execution", "ibkr", "circuit_breaker", "broker")
    assert not any(any(f in name.lower() for f in forbidden) for name in imported_names)


def test_preview_never_sends_a_telegram_message(config, monkeypatch):
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)

    def boom(*a, **k):
        raise AssertionError("preview must never send a Telegram message")

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", boom)
    preview.format_preview(config, logger)


def test_preview_never_touches_execution_affecting_fields(config):
    """Mirrors the existing shadow-mode invariant tests - the preview
    builds its own synthetic entries, so this proves it never fabricates
    best_risk_result/regime_evaluation/portfolio_evaluation/
    execution_decision on them."""
    _seed_cache(config, "AMD", "2026-10-08", _FULL_RAW)
    entries = preview.build_preview_entries(config, logger)
    for entry in entries:
        assert "best_risk_result" not in entry
        assert "regime_evaluation" not in entry
        assert "portfolio_evaluation" not in entry
        assert "execution_decision" not in entry
