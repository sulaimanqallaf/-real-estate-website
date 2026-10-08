"""src/intelligence/tradingagents_adapter.py - the subprocess boundary to
the real upstream TradingAgents package. None of these tests import or
require the `tradingagents` package itself (it lives only in the
isolated `.venvs/tradingagents/` environment, never in this project's
own); `test_run_one_actually_shells_out_through_a_real_subprocess` is the
one exception that exercises the real subprocess plumbing, using a tiny
fixture script as the stand-in "isolated environment" instead."""

import json
import logging
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import tradingagents_adapter as ta
from src.intelligence.schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL

logger = logging.getLogger("test")


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}, "intelligence": {"tradingagents": {"enabled": True}}}


# --- path resolution --------------------------------------------------------------------


def test_resolve_state_and_memory_db_paths_colocate_with_journal_dir(config):
    assert ta.resolve_state_db_path(config).parent == Path(config["data"]["journal_dir"])
    assert ta.resolve_memory_db_path(config).parent == Path(config["data"]["journal_dir"])
    assert ta.resolve_state_db_path(config) != ta.resolve_memory_db_path(config)


def test_resolve_python_executable_defaults_under_venvs(config):
    path = ta.resolve_python_executable(config)
    assert ".venvs/tradingagents" in str(path)


def test_resolve_python_executable_honors_explicit_config(config):
    config["intelligence"]["tradingagents"]["python_executable"] = "/custom/python"
    assert ta.resolve_python_executable(config) == Path("/custom/python")


# --- mapping (pure function, no subprocess) ----------------------------------------------


def test_build_assessment_maps_buy_and_overweight_to_buy():
    for rating in ("Buy", "Overweight"):
        a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", {"final_rating": rating})
        assert a.action == ACTION_BUY


def test_build_assessment_maps_sell_and_underweight_to_sell():
    for rating in ("Sell", "Underweight"):
        a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", {"final_rating": rating})
        assert a.action == ACTION_SELL


def test_build_assessment_maps_hold_and_review_and_missing_to_hold():
    for raw in ({"final_rating": "Hold"}, {"final_rating": "REVIEW"}, {}):
        a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", raw)
        assert a.action == ACTION_HOLD


def test_build_assessment_marks_data_unavailable_with_no_reports_or_decision():
    a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", {})
    assert a.analyst_opinions["tradingagents_upstream"].data_available is False


def test_build_assessment_includes_analyst_reports_as_evidence():
    raw = {"final_rating": "Buy", "reports": {"market": "strong uptrend", "news": "positive coverage"}}
    a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", raw)
    evidence_text = " ".join(a.analyst_opinions["tradingagents_upstream"].evidence)
    assert "strong uptrend" in evidence_text
    assert "positive coverage" in evidence_text


def test_build_assessment_carries_quant_agent_decision_and_duration():
    a = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", {"final_rating": "Buy"}, quant_agent_decision="ELIGIBLE", duration_ms=1234.5)
    assert a.quant_agent_decision == "ELIGIBLE"
    assert a.duration_ms == 1234.5


# --- cache + call budget (real sqlite, no subprocess) -------------------------------------


def test_cache_round_trip_and_ttl_expiry(config, tmp_path):
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    ta._cache_set(db_path, "key1", {"ok": True, "signal": "Buy"}, now)

    assert ta._cache_get(db_path, "key1", ttl_hours=24, now=now + timedelta(hours=1)) == {"ok": True, "signal": "Buy"}
    assert ta._cache_get(db_path, "key1", ttl_hours=24, now=now + timedelta(hours=25)) is None  # expired
    assert ta._cache_get(db_path, "missing-key", ttl_hours=24, now=now) is None


def test_call_budget_remaining_decrements_and_resets_per_day(config):
    db_path = ta.resolve_state_db_path(config)
    assert ta._call_budget_remaining(db_path, "2026-09-09", max_calls_per_day=2) == 2
    ta._record_call(db_path, "2026-09-09")
    assert ta._call_budget_remaining(db_path, "2026-09-09", max_calls_per_day=2) == 1
    ta._record_call(db_path, "2026-09-09")
    assert ta._call_budget_remaining(db_path, "2026-09-09", max_calls_per_day=2) == 0
    assert ta._call_budget_remaining(db_path, "2026-09-10", max_calls_per_day=2) == 2  # a new day, fresh budget


# --- portfolio context (read-only, never credentials) ------------------------------------


def test_build_portfolio_context_is_unknown_not_empty_when_config_is_incomplete(config):
    # This config has no "paper_trading" section at all - load_paper_trades_df()
    # cannot even determine the CSV path, so the right answer is "we don't
    # know", never a confident "zero holdings."
    assert ta._build_portfolio_context(config, logger) is None


def test_build_portfolio_context_never_includes_credentials():
    ctx = ta._build_portfolio_context({"data": {"journal_dir": "/nonexistent"}}, logger)
    assert "api_key" not in json.dumps(ctx).lower()
    assert "token" not in json.dumps(ctx).lower()


def _paper_trading_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"},
        "risk": {"account_equity": 10_000},
        "intelligence": {"tradingagents": {"enabled": True}},
    }


def _record(config, symbol, provenance, status="OPEN", trade_id=None, entry=100.0, shares=10):
    import src.paper_trades as paper_trades

    paper_trades.record_paper_trade(
        {
            "symbol": symbol, "strategy": "Trend Following", "signal": "Top Candidate", "score": 90,
            "entry": entry, "stop_loss": 95.0, "target": 115.0, "risk_reward": 3.0,
            "shares": shares, "dollar_risk": 50.0, "regime_at_entry": "TRENDING_UP",
            "report_date": "2026-09-09", "decided_at": None,
        },
        config, trade_id=trade_id, provenance=provenance,
    )
    if status != "OPEN":
        df = paper_trades.load_paper_trades_df(config)
        df.loc[df["ticker"] == symbol, "status"] = status
        df.to_csv(paper_trades._paper_trades_path(config), index=False)


def test_build_portfolio_context_is_a_known_flat_book_with_an_empty_but_readable_csv(tmp_path):
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    # Force the file to exist (empty, but a real, readable ledger) - distinct
    # from "no paper_trading config at all" (unknown -> None).
    paper_trades.load_paper_trades_df(config)  # no rows recorded
    ctx = ta._build_portfolio_context(config, logger)
    assert ctx == {"cash": 10_000, "currency": None, "positions": []}


def test_build_portfolio_context_includes_an_actual_broker_paper_position(tmp_path):
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    _record(config, "AMD", paper_trades.PROVENANCE_BROKER_PAPER, entry=100.0, shares=10)

    ctx = ta._build_portfolio_context(config, logger)
    assert ctx is not None
    assert ctx["positions"] == [{"ticker": "AMD", "quantity": 10.0, "average_price": 100.0}]


def test_build_portfolio_context_excludes_a_simulated_never_submitted_row(tmp_path):
    """The exact bug class this follow-up asks to guard against: a
    SIMULATED (DRY_RUN, never actually submitted to any broker) row must
    never be reported to TradingAgents as if it were a real holding."""
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    _record(config, "AMD", paper_trades.PROVENANCE_SIMULATED, entry=100.0, shares=10)

    ctx = ta._build_portfolio_context(config, logger)
    assert ctx is not None
    assert ctx["positions"] == []  # a known, confirmed flat book - the SIMULATED row is real data, correctly excluded


def test_build_portfolio_context_excludes_a_broker_paper_row_that_is_already_closed(tmp_path):
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    _record(config, "AMD", paper_trades.PROVENANCE_BROKER_PAPER, status="TARGET_HIT", entry=100.0, shares=10)

    ctx = ta._build_portfolio_context(config, logger)
    assert ctx["positions"] == []


def test_build_portfolio_context_mixes_correctly_when_both_provenances_are_present(tmp_path):
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    _record(config, "AMD", paper_trades.PROVENANCE_BROKER_PAPER, entry=100.0, shares=10)
    _record(config, "NVDA", paper_trades.PROVENANCE_SIMULATED, entry=200.0, shares=5)

    ctx = ta._build_portfolio_context(config, logger)
    tickers = [p["ticker"] for p in ctx["positions"]]
    assert tickers == ["AMD"]  # only the real broker-paper row - NVDA's simulated row stays out


def test_build_portfolio_context_skips_a_row_with_a_missing_quantity_rather_than_guessing(tmp_path):
    import src.paper_trades as paper_trades

    config = _paper_trading_config(tmp_path)
    _record(config, "AMD", paper_trades.PROVENANCE_BROKER_PAPER, entry=100.0, shares=10)
    df = paper_trades.load_paper_trades_df(config)
    df.loc[df["ticker"] == "AMD", "position_size"] = None
    df.to_csv(paper_trades._paper_trades_path(config), index=False)

    ctx = ta._build_portfolio_context(config, logger)
    assert ctx["positions"] == []  # never fabricates a quantity for a malformed row


def test_build_portfolio_context_excludes_a_row_from_an_old_format_csv_missing_status_or_provenance(tmp_path):
    """A corrupt/old-format CSV missing the `provenance`/`status` columns
    entirely: `load_paper_trades_df()` already reindexes those in as NaN
    (never raises) - a NaN status can never equal "OPEN", so the row is
    correctly excluded from positions, same as any other malformed row,
    rather than treated as a confirmed open position."""
    config = _paper_trading_config(tmp_path)
    journal_dir = Path(config["data"]["journal_dir"])
    (journal_dir / "paper_trades.csv").write_text("ticker,entry_price\nAMD,100.0\n", encoding="utf-8")

    ctx = ta._build_portfolio_context(config, logger)
    assert ctx is not None
    assert ctx["positions"] == []


# --- run_one: real subprocess plumbing using a fixture script in place of tradingagents --


_FAKE_RUNNER = textwrap.dedent("""
    import json, sys
    with open(sys.argv[1]) as f:
        request = json.load(f)
    print(json.dumps({"ok": True, "signal": "Buy", "final_rating": "Buy", "reports": {"market": "fake report for " + request["ticker"]}}))
""")


@pytest.fixture
def fake_runner(tmp_path):
    script = tmp_path / "fake_runner.py"
    script.write_text(_FAKE_RUNNER)
    return script


def test_run_one_actually_shells_out_through_a_real_subprocess(config, fake_runner, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner)

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert result is not None
    assert result["signal"] == "Buy"
    assert "AMD" in result["reports"]["market"]


def test_run_one_uses_the_cache_on_a_second_call_and_never_shells_out_again(config, fake_runner, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    first = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert first is not None

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called again on a cache hit")

    monkeypatch.setattr(subprocess, "run", boom)
    second = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now + timedelta(minutes=1))
    assert second == first


def test_run_one_never_calls_subprocess_once_the_daily_budget_is_exhausted(config, fake_runner, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner)
    config["intelligence"]["tradingagents"]["max_calls_per_day"] = 1
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)  # spends the only call
    ta.run_one("NVDA", "2026-09-09", config, logger, portfolio_context={}, now=now)  # different ticker, same day -> different cache key

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called once the daily budget is exhausted")

    monkeypatch.setattr(subprocess, "run", boom)
    result = ta.run_one("TSLA", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert result is None


def test_run_one_times_out_then_retries_then_gives_up(config, monkeypatch):
    config["intelligence"]["tradingagents"]["max_retries"] = 1
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ta.time, "sleep", lambda s: None)  # don't actually wait in tests
    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={})
    assert result is None
    assert len(calls) == 2  # original attempt + 1 retry


def test_run_one_returns_none_and_does_not_cache_a_reported_failure(config, tmp_path, monkeypatch):
    script = tmp_path / "failing_runner.py"
    script.write_text("import json; print(json.dumps({'ok': False, 'error': 'no API key'}))")
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: script)

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={})
    assert result is None

    db_path = ta.resolve_state_db_path(config)
    key = ta._cache_key("AMD", "2026-09-09", ["market", "social", "news", "fundamentals"], {})
    assert ta._cache_get(db_path, key, ttl_hours=24, now=datetime.now(timezone.utc)) is None


# --- run_shadow_tradingagents_research: disabled-by-default + shadow-mode wiring ----------


def test_disabled_by_default_leaves_entries_untouched(tmp_path):
    config = {"data": {"journal_dir": str(tmp_path)}}  # no intelligence.tradingagents key at all
    entries = [{"symbol": "AMD"}]
    ta.run_shadow_tradingagents_research(entries, "2026-09-09", config, logger)
    assert "tradingagents_assessment" not in entries[0]


def _tradeable_entry(symbol, score=50):
    return {
        "symbol": symbol, "score": score,
        "best_risk_result": {"tradeable": True}, "regime_evaluation": {"blocked": False},
        "portfolio_evaluation": {"decision": "ACCEPT"},
    }


def test_enabled_attaches_assessment_and_respects_max_tickers_per_run(config, monkeypatch):
    config["intelligence"]["tradingagents"]["max_tickers_per_run"] = 1

    monkeypatch.setattr(ta, "run_one", lambda *a, **k: {"ok": True, "final_rating": "Buy", "reports": {"market": "m"}})
    entries = [_tradeable_entry("AMD", score=90), _tradeable_entry("NVDA", score=50)]
    ta.run_shadow_tradingagents_research(entries, "2026-09-09", config, logger)

    assert entries[0].get("tradingagents_assessment") is not None  # higher score - selected
    assert entries[1].get("tradingagents_assessment") is None  # beyond the per-run cap


def test_never_touches_execution_affecting_fields(config, monkeypatch):
    monkeypatch.setattr(ta, "run_one", lambda *a, **k: {"ok": True, "final_rating": "Sell"})
    entry = {"symbol": "AMD", "best_risk_result": {"tradeable": True}, "portfolio_evaluation": {"decision": "ACCEPT"}}
    before = dict(entry["best_risk_result"]), dict(entry["portfolio_evaluation"])
    ta.run_shadow_tradingagents_research([entry], "2026-09-09", config, logger)
    assert entry["best_risk_result"] == before[0]
    assert entry["portfolio_evaluation"] == before[1]
    assert "execution_decision" not in entry


# --- secret redaction -------------------------------------------------------------------


def test_redact_masks_common_api_key_shapes():
    assert ta._redact("call failed: sk-abcdefghijklmnop") == "call failed: [REDACTED]"
    assert ta._redact("call failed: sk-ant-abcdefghijklmnop") == "call failed: [REDACTED]"
    assert ta._redact("Authorization: Bearer abcdefghijklmnop") == "[REDACTED]"
    assert "abcdefghijklmnop" not in ta._redact("ANTHROPIC_API_KEY=abcdefghijklmnop")


def test_redact_leaves_ordinary_text_alone():
    assert ta._redact("no API key was set for provider openai") == "no API key was set for provider openai"


def test_redact_handles_none_and_empty():
    assert ta._redact(None) == ""
    assert ta._redact("") == ""


# --- candidate selection: never spends on an already-rejected candidate -----------------


def test_select_worthwhile_candidates_excludes_upstream_rejections():
    rejected = _tradeable_entry("ZZZZ", score=99)
    rejected["label"] = "Avoid"
    kept = _tradeable_entry("AMD", score=10)
    selected = ta._select_worthwhile_candidates([rejected, kept], max_tickers=5)
    assert selected == [kept]


def test_select_worthwhile_candidates_ranks_by_quant_score_when_available():
    low_quant = _tradeable_entry("AMD", score=99)
    low_quant["quant_assessment"] = type("QA", (), {"quant_score": 10})()
    high_quant = _tradeable_entry("NVDA", score=1)
    high_quant["quant_assessment"] = type("QA", (), {"quant_score": 90})()
    selected = ta._select_worthwhile_candidates([low_quant, high_quant], max_tickers=1)
    assert selected == [high_quant]  # quant_score wins over the plain rule score


# --- spend reservation integrated into run_one -------------------------------------------


_FAKE_RUNNER_WITH_USAGE = textwrap.dedent("""
    import json, sys
    with open(sys.argv[1]) as f:
        request = json.load(f)
    print(json.dumps({
        "ok": True, "signal": "Buy", "final_rating": "Buy",
        "reports": {"market": "m"},
        "token_usage": {"test-model": {"input_tokens": 1000000, "output_tokens": 0, "calls": 1}},
    }))
""")


@pytest.fixture
def fake_runner_with_usage(tmp_path):
    script = tmp_path / "fake_runner_usage.py"
    script.write_text(_FAKE_RUNNER_WITH_USAGE)
    return script


def test_run_one_commits_the_real_priced_cost_not_the_reservation_ceiling(config, fake_runner_with_usage, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_with_usage)
    config["intelligence"]["tradingagents"]["pricing"] = {"test-model": {"input": 2.00, "output": 10.00}}
    config["intelligence"]["tradingagents"]["max_cost_per_call_usd"] = 1.00  # the conservative reservation ceiling
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert result["estimated_cost_usd"]["total_usd"] == 2.00  # 1M input tokens @ $2/M - real cost, not the $1.00 reservation

    spend_db = ta.tradingagents_spend.resolve_ledger_path(config)
    totals = ta.tradingagents_spend.spent_today_and_month(spend_db, now)
    assert totals["today_usd"] == 2.00


def test_run_one_refuses_to_call_once_the_daily_dollar_cap_is_reached(config, fake_runner_with_usage, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_with_usage)
    config["intelligence"]["tradingagents"]["max_daily_spend_usd"] = 0.0001  # effectively zero room
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called once the daily $ cap refuses the reservation")

    monkeypatch.setattr(subprocess, "run", boom)
    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert result is None


def test_run_one_releases_the_reservation_when_the_process_never_starts(config, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path("/does/not/exist/python"))
    config["intelligence"]["tradingagents"]["max_retries"] = 0
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert result is None

    spend_db = ta.tradingagents_spend.resolve_ledger_path(config)
    totals = ta.tradingagents_spend.spent_today_and_month(spend_db, now)
    assert totals["today_usd"] == 0.0  # released, not committed - the process never actually ran


# --- per-ticker isolation: one bad ticker never stops the rest --------------------------


def test_one_ticker_raising_never_blocks_the_next_ticker(config, monkeypatch):
    config["intelligence"]["tradingagents"]["max_tickers_per_run"] = 5

    def flaky_run_one(ticker, *a, **k):
        if ticker == "AMD":
            raise RuntimeError("simulated failure for AMD only")
        return {"ok": True, "final_rating": "Buy", "reports": {"market": "m"}}

    monkeypatch.setattr(ta, "run_one", flaky_run_one)
    entries = [_tradeable_entry("AMD", score=90), _tradeable_entry("NVDA", score=50)]
    ta.run_shadow_tradingagents_research(entries, "2026-09-09", config, logger)

    assert entries[0].get("tradingagents_assessment") is None  # AMD failed
    assert entries[1].get("tradingagents_assessment") is not None  # NVDA still processed


# --- GitHub Issue #1 follow-up: pricing gaps, fail-closed spend, cache re-pricing --------


_FAKE_RUNNER_UNPRICED_MODEL = textwrap.dedent("""
    import json, sys
    with open(sys.argv[1]) as f:
        request = json.load(f)
    print(json.dumps({
        "ok": True, "signal": "Buy", "final_rating": "Buy",
        "reports": {"market": "m"},
        "token_usage": {"totally-unpriced-model": {"input_tokens": 1000000, "output_tokens": 0, "calls": 1}},
    }))
""")


@pytest.fixture
def fake_runner_unpriced(tmp_path):
    script = tmp_path / "fake_runner_unpriced.py"
    script.write_text(_FAKE_RUNNER_UNPRICED_MODEL)
    return script


def test_build_assessment_includes_a_token_usage_risk_note():
    raw = {"final_rating": "Buy", "token_usage": {"gpt-6-sol": {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": 200, "calls": 1}}}
    assessment = ta.build_assessment("AMD", "2026-09-09", "2026-09-09T00:00:00+00:00", raw)
    token_notes = [n for n in assessment.risk_notes if n.startswith("Token usage")]
    assert len(token_notes) == 1
    assert "gpt-6-sol" in token_notes[0]
    assert "cached 200" in token_notes[0]


def test_run_one_fails_closed_charging_the_full_reservation_for_an_unpriced_model(config, fake_runner_unpriced, monkeypatch, caplog):
    import logging as logging_module

    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_unpriced)
    config["intelligence"]["tradingagents"]["max_cost_per_call_usd"] = 0.75
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    with caplog.at_level(logging_module.WARNING):
        result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)

    assert result["estimated_cost_usd"]["total_usd"] is None  # genuinely unknown
    spend_db = ta.tradingagents_spend.resolve_ledger_path(config)
    totals = ta.tradingagents_spend.spent_today_and_month(spend_db, now)
    assert totals["today_usd"] == 0.75  # the full conservative reservation, not $0 - fail closed
    assert any("fail closed" in r.message.lower() or "no pricing entry" in r.message.lower() for r in caplog.records)


def test_cache_hit_is_repriced_with_the_current_pricing_table_no_new_subprocess_call(config, fake_runner_unpriced, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_unpriced)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    first = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)
    assert first["estimated_cost_usd"]["total_usd"] is None  # unpriced at call time

    # Pricing is "fixed" after the fact, exactly like adding gpt-6-sol/
    # gpt-6-luna was - no new subprocess call should be needed to see it.
    config["intelligence"]["tradingagents"]["pricing"] = {"totally-unpriced-model": {"input": 1.00, "output": 1.00}}

    def boom(*a, **k):
        raise AssertionError("a cache hit must never shell out to the subprocess again")

    monkeypatch.setattr(subprocess, "run", boom)
    second = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now + timedelta(minutes=1))
    assert second["estimated_cost_usd"]["total_usd"] == pytest.approx(1.00)  # 1M input tokens @ $1/M


def test_cache_hit_never_fabricates_a_cost_for_an_entry_cached_before_token_tracking_existed(config, fake_runner_with_usage, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_with_usage)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)

    # Simulate an old-format cached row with no "token_usage" key at all.
    db_path = ta.resolve_state_db_path(config)
    key = ta._cache_key("AMD", "2026-09-09", ["market", "social", "news", "fundamentals"], {})
    with ta._connect(db_path) as conn:
        conn.execute("UPDATE cache SET result_json = ? WHERE cache_key = ?", (json.dumps({"ok": True, "final_rating": "Buy"}), key))

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now + timedelta(minutes=1))
    assert result.get("estimated_cost_usd") is None  # never fabricated - stays unknown, not a guessed number


def test_inspect_cached_results_rereads_without_any_subprocess_call(config, fake_runner_unpriced, monkeypatch):
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: fake_runner_unpriced)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)

    def boom(*a, **k):
        raise AssertionError("inspecting the cache must never shell out to a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)

    inspected = ta.inspect_cached_results(config)
    assert len(inspected) == 1
    assert inspected[0]["ticker"] == "AMD"
    assert inspected[0]["report_date"] == "2026-09-09"
    assert inspected[0]["estimated_cost_usd"]["total_usd"] is None  # still unpriced with the default table

    # Now inspect again with an override pricing table passed directly - still no subprocess call.
    priced = ta.inspect_cached_results(config, pricing_table={"totally-unpriced-model": {"input": 2.00, "output": 2.00}})
    assert priced[0]["estimated_cost_usd"]["total_usd"] == pytest.approx(2.00)


def test_inspect_cached_results_returns_empty_list_for_a_missing_database(tmp_path):
    config = {"data": {"journal_dir": str(tmp_path)}}
    assert ta.inspect_cached_results(config) == []


def test_cache_table_migrates_an_old_schema_missing_ticker_and_report_date_columns(tmp_path):
    db_path = tmp_path / "state.db"
    conn = __import__("sqlite3").connect(str(db_path))
    conn.execute("CREATE TABLE cache (cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, cached_at TEXT NOT NULL)")
    conn.execute("INSERT INTO cache VALUES (?, ?, ?)", ("oldkey", json.dumps({"ok": True}), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    # Any _connect() call (e.g. via _cache_set) must migrate the table in place, not crash.
    ta._cache_set(db_path, "newkey", {"ok": True, "final_rating": "Buy"}, datetime.now(timezone.utc), ticker="AMD", report_date="2026-09-09")
    with ta._connect(db_path) as conn:
        rows = conn.execute("SELECT cache_key, ticker, report_date FROM cache ORDER BY cache_key").fetchall()
    assert [dict(r) for r in rows] == [
        {"cache_key": "newkey", "ticker": "AMD", "report_date": "2026-09-09"},
        {"cache_key": "oldkey", "ticker": None, "report_date": None},
    ]


# --- GitHub Issue #1 follow-up: recover ticker/report_date for legacy NULL rows --------


def _write_old_schema_cache_row(db_path, cache_key, result, cached_at):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS cache (cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, cached_at TEXT NOT NULL)")
    conn.execute("INSERT INTO cache (cache_key, result_json, cached_at) VALUES (?, ?, ?)", (cache_key, json.dumps(result), cached_at))
    conn.commit()
    conn.close()


def _write_request_file(config, ticker, trade_date, selected_analysts=None, config_overrides=None):
    selected_analysts = selected_analysts or ["market", "social", "news", "fundamentals"]
    config_overrides = config_overrides or {}
    work_dir = ta.resolve_work_dir(config) / f"{ticker}_{trade_date}"
    work_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "ticker": ticker, "trade_date": trade_date, "work_dir": str(work_dir),
        "selected_analysts": selected_analysts, "config_overrides": config_overrides, "portfolio": None,
    }
    (work_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
    return ta._cache_key(ticker, trade_date, selected_analysts, config_overrides)


def test_recover_cache_metadata_backfills_ticker_and_report_date_from_a_real_request_file(config):
    db_path = ta.resolve_state_db_path(config)
    key = _write_request_file(config, "AMD", "2026-09-09")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    _write_old_schema_cache_row(db_path, key, {"ok": True, "final_rating": "Sell"}, now.isoformat())

    result = ta.recover_cache_metadata_from_request_files(config)
    assert result == {"recovered": 1, "unmatched_rows": 0, "checked_files": 1}

    with ta._connect(db_path) as conn:
        row = conn.execute("SELECT ticker, report_date FROM cache WHERE cache_key = ?", (key,)).fetchone()
    assert row["ticker"] == "AMD"
    assert row["report_date"] == "2026-09-09"


def test_recover_cache_metadata_never_overwrites_a_row_that_already_has_metadata(config):
    db_path = ta.resolve_state_db_path(config)
    key = _write_request_file(config, "AMD", "2026-09-09")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    with ta._connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cache (cache_key, result_json, cached_at, ticker, report_date) VALUES (?, ?, ?, ?, ?)",
            (key, json.dumps({"ok": True}), now.isoformat(), "SOMETHING_ELSE", "2020-01-01"),
        )

    ta.recover_cache_metadata_from_request_files(config)

    with ta._connect(db_path) as conn:
        row = conn.execute("SELECT ticker, report_date FROM cache WHERE cache_key = ?", (key,)).fetchone()
    assert row["ticker"] == "SOMETHING_ELSE"  # untouched - only ever fills in a currently-NULL field
    assert row["report_date"] == "2020-01-01"


def test_recover_cache_metadata_leaves_a_row_null_with_no_matching_request_file(config):
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    # A cache row whose request.json was never written (or was deleted) - no file anywhere to recover from.
    _write_old_schema_cache_row(db_path, "some-orphan-cache-key", {"ok": True, "final_rating": "Buy"}, now.isoformat())

    result = ta.recover_cache_metadata_from_request_files(config)
    assert result == {"recovered": 0, "unmatched_rows": 1, "checked_files": 0}

    with ta._connect(db_path) as conn:
        row = conn.execute("SELECT ticker, report_date FROM cache WHERE cache_key = ?", ("some-orphan-cache-key",)).fetchone()
    assert row["ticker"] is None  # retained as NULL - never guessed
    assert row["report_date"] is None


def test_recover_cache_metadata_never_matches_a_request_file_for_a_different_call(config):
    """A request.json for a ticker/date/analysts/overrides combination
    that was never actually cached (e.g. a call that failed before ever
    writing to the cache) must never be mistaken for a match on an
    unrelated orphan row - the cache_key hash has to agree exactly."""
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    _write_old_schema_cache_row(db_path, "some-orphan-cache-key", {"ok": True}, now.isoformat())
    _write_request_file(config, "NVDA", "2026-09-10")  # unrelated - different cache_key entirely

    result = ta.recover_cache_metadata_from_request_files(config)
    assert result == {"recovered": 0, "unmatched_rows": 1, "checked_files": 1}


def test_recover_cache_metadata_handles_a_corrupt_request_file_without_crashing(config):
    db_path = ta.resolve_state_db_path(config)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    _write_old_schema_cache_row(db_path, "some-orphan-cache-key", {"ok": True}, now.isoformat())
    bad_dir = ta.resolve_work_dir(config) / "CORRUPT_2026-09-09"
    bad_dir.mkdir(parents=True)
    (bad_dir / "request.json").write_text("{not valid json", encoding="utf-8")

    result = ta.recover_cache_metadata_from_request_files(config)
    assert result["recovered"] == 0  # corrupt file skipped, never crashes


def test_recover_cache_metadata_makes_no_api_call(config, monkeypatch):
    db_path = ta.resolve_state_db_path(config)
    key = _write_request_file(config, "AMD", "2026-09-09")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    _write_old_schema_cache_row(db_path, key, {"ok": True}, now.isoformat())

    def boom(*a, **k):
        raise AssertionError("recovering cache metadata must never shell out to a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    ta.recover_cache_metadata_from_request_files(config)  # must not raise AssertionError


def test_recover_cache_metadata_returns_zeroes_for_a_missing_database(tmp_path):
    config = {"data": {"journal_dir": str(tmp_path)}}
    assert ta.recover_cache_metadata_from_request_files(config) == {"recovered": 0, "unmatched_rows": 0, "checked_files": 0}


def test_inspect_cached_results_recovers_legacy_null_metadata_automatically(config):
    """The exact scenario reported: historical cache rows with ticker/
    report_date showing as None after the first migration. inspect_
    cached_results() must now recover them automatically, with zero new
    API calls, from the request.json files already on disk."""
    db_path = ta.resolve_state_db_path(config)
    amd_key = _write_request_file(config, "AMD", "2026-09-09")
    qqq_key = _write_request_file(config, "QQQ", "2026-09-09")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    _write_old_schema_cache_row(db_path, amd_key, {"ok": True, "final_rating": "Sell"}, now.isoformat())
    _write_old_schema_cache_row(db_path, qqq_key, {"ok": True, "final_rating": "Overweight"}, now.isoformat())

    inspected = ta.inspect_cached_results(config)
    by_key = {r["ticker"]: r for r in inspected}
    assert set(by_key.keys()) == {"AMD", "QQQ"}
    assert all(r["report_date"] == "2026-09-09" for r in inspected)


# --- GitHub Issue #1 follow-up: raw_label, confidence, and debate-text summarization ----


def test_build_assessment_preserves_raw_label_distinct_from_normalized_action():
    """The exact bug reported: QQQ's raw upstream rating was Overweight,
    but build_assessment() must keep that verbatim alongside (never
    instead of) the normalized BUY action."""
    raw = {"final_rating": "Overweight"}
    assessment = ta.build_assessment("QQQ", "2026-10-08", "2026-10-08T00:00:00+00:00", raw)
    assert assessment.action == "BUY"
    assert assessment.raw_label == "Overweight"
    assert assessment.analyst_opinions["tradingagents_upstream"].raw_label == "Overweight"


def test_build_assessment_preserves_a_raw_sell_rating_too():
    raw = {"final_rating": "Sell"}
    assessment = ta.build_assessment("AMD", "2026-10-08", "2026-10-08T00:00:00+00:00", raw)
    assert assessment.action == "SELL"
    assert assessment.raw_label == "Sell"


def test_build_assessment_never_fabricates_a_confidence_number():
    for rating in ("Buy", "Sell", "Hold", "Overweight", "Underweight", None):
        assessment = ta.build_assessment("AMD", "2026-10-08", "2026-10-08T00:00:00+00:00", {"final_rating": rating} if rating else {})
        assert assessment.confidence is None
        assert assessment.analyst_opinions["tradingagents_upstream"].confidence is None


# --- _summarize_debate_text: pure text processing, no LLM ------------------------------


def test_summarize_debate_text_strips_markdown_headings_and_emphasis():
    text = "## Bull Case\n\n**Strong margins** and __healthy demand__."
    result = ta._summarize_debate_text(text)
    assert "#" not in result
    assert "**" not in result
    assert "__" not in result
    assert "Strong margins" in result
    assert "healthy demand" in result


def test_summarize_debate_text_leaves_short_text_untouched_with_no_truncation_label():
    text = "AMD margins are improving quarter over quarter."
    result = ta._summarize_debate_text(text)
    assert result == text
    assert "excerpt" not in result


def test_summarize_debate_text_truncates_at_a_word_boundary_with_an_explicit_label():
    text = "word " * 200  # much longer than the excerpt limit
    result = ta._summarize_debate_text(text, max_chars=50)
    assert result.endswith(")")
    assert "excerpt" in result
    assert "see cached transcript for the full debate" in result
    # never cuts mid-word: the text before the "…" must end on a word boundary
    before_ellipsis = result.split("…")[0]
    assert not before_ellipsis.endswith("wor")


def test_summarize_debate_text_returns_none_for_empty_or_missing_input():
    assert ta._summarize_debate_text(None) is None
    assert ta._summarize_debate_text("") is None
    assert ta._summarize_debate_text("   \n\n  ") is None


def test_summarize_debate_text_never_invokes_a_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("summarizing debate text must never shell out - it is pure text processing")

    monkeypatch.setattr(subprocess, "run", boom)
    ta._summarize_debate_text("## Some heading\n\nSome **bold** text " * 50)


def test_build_assessment_bull_bear_points_are_markdown_cleaned_and_labeled_when_long():
    raw = {
        "final_rating": "Buy",
        "bull_history": "## Bull Case\n\n" + ("Strong fundamentals. " * 40),
        "bear_history": "Short bear note.",
    }
    assessment = ta.build_assessment("AMD", "2026-10-08", "2026-10-08T00:00:00+00:00", raw)
    assert "#" not in assessment.bull_points[0]
    assert "excerpt" in assessment.bull_points[0]
    assert assessment.bear_points[0] == "Short bear note."


# --- GitHub Issue #1 follow-up: shared repriced_cost_info() helper ---------------------


def test_repriced_cost_info_recomputes_from_token_usage_with_a_given_table():
    raw = {"token_usage": {"gpt-6-luna": {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 0}}}
    cost = ta.repriced_cost_info(raw, {"gpt-6-luna": {"input": 0.10, "output": 0.50}})
    assert cost["total_usd"] == pytest.approx(0.10)


def test_repriced_cost_info_returns_whatever_was_stored_when_no_token_usage_key_exists():
    raw = {"estimated_cost_usd": {"total_usd": None, "by_model": {}, "unknown_models": []}}
    assert ta.repriced_cost_info(raw) == raw["estimated_cost_usd"]


def test_repriced_cost_info_never_mutates_the_input_dict():
    raw = {"token_usage": {"gpt-6-luna": {"input_tokens": 1000, "output_tokens": 0}}}
    before = dict(raw)
    ta.repriced_cost_info(raw, {"gpt-6-luna": {"input": 1.0, "output": 1.0}})
    assert raw == before
