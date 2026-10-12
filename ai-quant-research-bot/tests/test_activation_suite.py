"""src/activation_suite.py - the Sprint 5 real-world activation suite.
`_run_strategy_validation`/`_run_iex_data_check`/`_run_connectivity_preflight`
already have their own dedicated tests in test_validation_suite.py (this
module reuses `validation_suite.run_validation_suite()` wholesale, never
re-implementing them) - these tests focus on what's genuinely new here:
academic-provider classification + the paper_memory write-through, the
SEC/FRED check, the Telegram delivery check, and report assembly."""

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import activation_suite as acs
from src import validation_suite as vs
from src.data_providers import base, macro_provider, sec_provider
from src.data_providers.sec_provider import SOURCE_COMPANY_TICKERS
from src.research.papers import Paper

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {
        "data": {"reports_dir": str(tmp_path / "reports"), "journal_dir": str(tmp_path / "journal")},
        "research": {"paper_memory_db_path": str(tmp_path / "research" / "mem.db")},
        "backtest": {"benchmarks": ["SPY"]},
        "strategy_universe": {"mean_reversion": ["AAPL"], "momentum_breakout": ["AAPL"], "trend_following": ["AAPL"]},
        "execution": {"halt_state_file": str(tmp_path / "trading_halt.json")},
    }


_REAL_PAPER = Paper(
    title="Momentum Anomaly in Equity Returns", authors=("A. Author",), year=2020, venue="JF", citation_count=120,
    abstract="An empirical backtest of momentum, out-of-sample.", url="https://example.com/1",
    external_ids={"doi": "10.1/momentum"}, source="openalex",
)


# --- _classify_provider_result ---------------------------------------------------------------


def test_classify_ok_result_is_pass():
    result = base.ok("src", [1, 2, 3])
    status, detail = acs._classify_provider_result(result)
    assert status == acs.STATUS_PASS
    assert "3" in detail


def test_classify_blocked_result_is_blocked():
    result = base.provider_blocked("src", "connection refused")
    status, detail = acs._classify_provider_result(result)
    assert status == acs.STATUS_BLOCKED
    assert detail == "connection refused"


def test_classify_error_result_is_fail():
    result = base.provider_error("src", "malformed response")
    status, detail = acs._classify_provider_result(result)
    assert status == acs.STATUS_FAIL


def test_classify_unavailable_result_is_skipped():
    result = base.unavailable("src", "not configured")
    status, detail = acs._classify_provider_result(result)
    assert status == acs.STATUS_SKIPPED


# --- _run_academic_providers -----------------------------------------------------------------


def _patch_academic(monkeypatch, openalex=None, arxiv=None, semantic=None):
    from src.data_providers import arxiv_provider, openalex_provider, semantic_scholar_provider

    if openalex is not None:
        monkeypatch.setattr(openalex_provider, "fetch_papers", openalex)
    if arxiv is not None:
        monkeypatch.setattr(arxiv_provider, "fetch_papers", arxiv)
    if semantic is not None:
        monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", semantic)


def test_academic_providers_all_unavailable_skips_everything_including_memory(tmp_path, monkeypatch):
    config = _config(tmp_path)
    unavailable = lambda *a, **k: base.unavailable("x", "no results")
    _patch_academic(monkeypatch, openalex=unavailable, arxiv=unavailable, semantic=unavailable)

    lines = acs._run_academic_providers(config, LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["openalex"].status == acs.STATUS_SKIPPED
    assert by_name["arxiv"].status == acs.STATUS_SKIPPED
    assert by_name["semantic_scholar"].status == acs.STATUS_SKIPPED
    assert by_name["paper_memory"].status == acs.STATUS_SKIPPED


def test_academic_providers_records_a_real_paper_into_permanent_memory(tmp_path, monkeypatch):
    config = _config(tmp_path)
    unavailable = lambda *a, **k: base.unavailable("x", "no results")
    ok_with_paper = lambda *a, **k: base.ok("openalex", [_REAL_PAPER])
    _patch_academic(monkeypatch, openalex=ok_with_paper, arxiv=unavailable, semantic=unavailable)

    lines = acs._run_academic_providers(config, LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["openalex"].status == acs.STATUS_PASS
    assert by_name["paper_memory"].status == acs.STATUS_PASS

    from src.research import paper_memory

    rows = paper_memory.query_papers(config["research"]["paper_memory_db_path"])
    assert len(rows) == 1
    assert rows[0]["title"] == _REAL_PAPER.title


def test_academic_providers_surfaces_a_raised_exception_as_fail(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("openalex is down")

    unavailable = lambda *a, **k: base.unavailable("x", "no results")
    _patch_academic(monkeypatch, openalex=_raise, arxiv=unavailable, semantic=unavailable)

    lines = acs._run_academic_providers(config, LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["openalex"].status == acs.STATUS_FAIL
    assert "openalex is down" in by_name["openalex"].detail


def test_academic_providers_surfaces_a_blocked_result(tmp_path, monkeypatch):
    config = _config(tmp_path)
    blocked = lambda *a, **k: base.provider_blocked("arxiv", "connection refused")
    unavailable = lambda *a, **k: base.unavailable("x", "no results")
    _patch_academic(monkeypatch, openalex=unavailable, arxiv=blocked, semantic=unavailable)

    lines = acs._run_academic_providers(config, LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["arxiv"].status == acs.STATUS_BLOCKED


# --- _run_sec_and_fred ------------------------------------------------------------------------


def test_sec_and_fred_skipped_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["sec_ticker_cik"].status == acs.STATUS_SKIPPED
    assert by_name["fred"].status == acs.STATUS_SKIPPED


def test_sec_ticker_cik_passes_when_aapl_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "Test Co test@example.com")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(sec_provider, "fetch_company_tickers", lambda *a, **k: base.ok(SOURCE_COMPANY_TICKERS, {"AAPL": "0000320193", "MSFT": "0000789019"}))

    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["sec_ticker_cik"].status == acs.STATUS_PASS
    assert "0000320193" in by_name["sec_ticker_cik"].detail


def test_sec_ticker_cik_fails_when_aapl_is_missing_from_a_real_response(tmp_path, monkeypatch):
    """A real fetch that succeeded but doesn't contain AAPL is a real
    anomaly worth failing loudly on, not a quiet SKIPPED."""
    monkeypatch.setenv("SEC_IDENTITY", "Test Co test@example.com")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(sec_provider, "fetch_company_tickers", lambda *a, **k: base.ok(SOURCE_COMPANY_TICKERS, {"MSFT": "0000789019"}))

    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["sec_ticker_cik"].status == acs.STATUS_FAIL


def test_sec_ticker_cik_reports_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "Test Co test@example.com")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(sec_provider, "fetch_company_tickers", lambda *a, **k: base.provider_blocked(SOURCE_COMPANY_TICKERS, "connection refused"))

    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["sec_ticker_cik"].status == acs.STATUS_BLOCKED


def test_fred_passes_on_a_real_observation(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    obs = macro_provider.MacroObservation(series_id="FEDFUNDS", value=5.25, observation_date=datetime(2026, 1, 1).date(), release_date=None)
    monkeypatch.setattr(macro_provider, "fetch_series_latest", lambda *a, **k: base.ok(macro_provider.SOURCE_FRED, [obs]))

    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["fred"].status == acs.STATUS_PASS


def test_fred_reports_fail_on_a_provider_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(macro_provider, "fetch_series_latest", lambda *a, **k: base.provider_error(macro_provider.SOURCE_FRED, "malformed"))

    lines = acs._run_sec_and_fred(_config(tmp_path), LOGGER)
    by_name = {l.name: l for l in lines}
    assert by_name["fred"].status == acs.STATUS_FAIL


# --- _run_telegram_delivery -------------------------------------------------------------------


def test_telegram_skipped_when_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    lines = acs._run_telegram_delivery({}, LOGGER)
    assert lines[0].status == acs.STATUS_SKIPPED


def test_telegram_passes_when_delivery_succeeds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("src.telegram_bot.send_telegram_message", lambda *a, **k: True)
    lines = acs._run_telegram_delivery({}, LOGGER)
    assert lines[0].status == acs.STATUS_PASS


def test_telegram_fails_when_delivery_is_reported_unsuccessful(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("src.telegram_bot.send_telegram_message", lambda *a, **k: False)
    lines = acs._run_telegram_delivery({}, LOGGER)
    assert lines[0].status == acs.STATUS_FAIL


def test_telegram_never_logs_the_token_in_its_report_line(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "super-secret-token-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("src.telegram_bot.send_telegram_message", lambda *a, **k: True)
    lines = acs._run_telegram_delivery({}, LOGGER)
    assert "super-secret-token-value" not in lines[0].detail


# --- run_activation_suite: merges validation_suite + isolates a crash -------------------------


def test_run_activation_suite_merges_the_base_suite_and_the_three_new_checks(tmp_path, monkeypatch):
    base_report = vs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[vs.SuiteLine("strategy_validation", "Mean Reversion", vs.STATUS_PASS, "ok")])
    monkeypatch.setattr(vs, "run_validation_suite", lambda config, logger, run_reconnect_check=False: base_report)
    monkeypatch.setattr(acs, "_run_academic_providers", lambda config, logger: [acs.SuiteLine("academic_providers", "openalex", acs.STATUS_SKIPPED, "n/a")])
    monkeypatch.setattr(acs, "_run_sec_and_fred", lambda config, logger: [acs.SuiteLine("sec_and_fred", "fred", acs.STATUS_SKIPPED, "n/a")])
    monkeypatch.setattr(acs, "_run_telegram_delivery", lambda config, logger: [acs.SuiteLine("telegram", "test_message", acs.STATUS_SKIPPED, "n/a")])

    report = acs.run_activation_suite(_config(tmp_path), LOGGER)
    sections = {l.section for l in report.lines}
    assert sections == {"strategy_validation", "academic_providers", "sec_and_fred", "telegram"}


def test_run_activation_suite_isolates_a_crashing_new_check(tmp_path, monkeypatch):
    base_report = vs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[])
    monkeypatch.setattr(vs, "run_validation_suite", lambda config, logger, run_reconnect_check=False: base_report)

    def _raise(config, logger):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(acs, "_run_academic_providers", _raise)
    monkeypatch.setattr(acs, "_run_sec_and_fred", lambda config, logger: [])
    monkeypatch.setattr(acs, "_run_telegram_delivery", lambda config, logger: [])

    report = acs.run_activation_suite(_config(tmp_path), LOGGER)
    statuses = {l.name: l.status for l in report.lines}
    assert statuses["-"] == acs.STATUS_BLOCKED


# --- format / save ----------------------------------------------------------------------------


def test_format_activation_report_text_uses_the_sprint5_title():
    report = vs.SuiteReport(started_at=datetime.now(), lines=[vs.SuiteLine("a", "x", vs.STATUS_PASS, "ok")])
    text = acs.format_activation_report_text(report)
    assert "REAL-WORLD ACTIVATION SUITE (Sprint 5)" in text
    assert "1 PASS" in text


def test_save_activation_report_text_writes_the_expected_file(tmp_path):
    config = _config(tmp_path)
    out_path = acs.save_activation_report_text("hello world", config, now=datetime(2026, 4, 1, 9, 30, 0))
    assert out_path == tmp_path / "reports" / "activation_suite_2026-04-01_093000.txt"
    assert out_path.read_text(encoding="utf-8") == "hello world"


# --- main() CLI --------------------------------------------------------------------------------


def test_main_returns_nonzero_when_any_check_failed(tmp_path, monkeypatch):
    fake_report = acs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[acs.SuiteLine("a", "x", acs.STATUS_FAIL, "bad")])
    monkeypatch.setattr(acs, "run_activation_suite", lambda config, logger, run_reconnect_check=False: fake_report)
    monkeypatch.setattr(sys, "argv", ["activation_suite.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    assert acs.main() == 1


def test_main_returns_zero_when_nothing_failed(tmp_path, monkeypatch):
    fake_report = acs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[acs.SuiteLine("a", "x", acs.STATUS_PASS, "ok")])
    monkeypatch.setattr(acs, "run_activation_suite", lambda config, logger, run_reconnect_check=False: fake_report)
    monkeypatch.setattr(sys, "argv", ["activation_suite.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    assert acs.main() == 0


def test_main_passes_full_reconnect_check_flag_through(tmp_path, monkeypatch):
    fake_report = acs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[])
    captured = {}

    def _fake_run(config, logger, run_reconnect_check=False):
        captured["run_reconnect_check"] = run_reconnect_check
        return fake_report

    monkeypatch.setattr(acs, "run_activation_suite", _fake_run)
    monkeypatch.setattr(sys, "argv", ["activation_suite.py", "--full-reconnect-check"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    acs.main()
    assert captured["run_reconnect_check"] is True
