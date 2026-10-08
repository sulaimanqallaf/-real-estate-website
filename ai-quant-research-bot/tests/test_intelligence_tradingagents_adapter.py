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


def test_build_portfolio_context_degrades_to_empty_dict_with_no_paper_trades(config):
    assert ta._build_portfolio_context(config, logger) == {}


def test_build_portfolio_context_never_includes_credentials():
    ctx = ta._build_portfolio_context({"data": {"journal_dir": "/nonexistent"}}, logger)
    assert "api_key" not in json.dumps(ctx).lower()
    assert "token" not in json.dumps(ctx).lower()


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


def test_enabled_attaches_assessment_and_respects_max_tickers_per_run(config, monkeypatch):
    config["intelligence"]["tradingagents"]["max_tickers_per_run"] = 1

    monkeypatch.setattr(ta, "run_one", lambda *a, **k: {"ok": True, "final_rating": "Buy", "reports": {"market": "m"}})
    entries = [{"symbol": "AMD"}, {"symbol": "NVDA"}]
    ta.run_shadow_tradingagents_research(entries, "2026-09-09", config, logger)

    assert entries[0].get("tradingagents_assessment") is not None
    assert entries[1].get("tradingagents_assessment") is None  # beyond the per-run cap


def test_never_touches_execution_affecting_fields(config, monkeypatch):
    monkeypatch.setattr(ta, "run_one", lambda *a, **k: {"ok": True, "final_rating": "Sell"})
    entry = {"symbol": "AMD", "best_risk_result": {"tradeable": True}, "portfolio_evaluation": {"decision": "ACCEPT"}}
    before = dict(entry["best_risk_result"]), dict(entry["portfolio_evaluation"])
    ta.run_shadow_tradingagents_research([entry], "2026-09-09", config, logger)
    assert entry["best_risk_result"] == before[0]
    assert entry["portfolio_evaluation"] == before[1]
    assert "execution_decision" not in entry
