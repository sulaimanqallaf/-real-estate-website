"""src/validation_suite.py - one-click real-world validation suite
(Sprint 4, Priority 1). Each `_run_*` helper is tested against its
real underlying module with the network/broker call monkeypatched out
(the underlying checks each already have their own dedicated test
suites from Sprint 3 - these tests focus on THIS module's job: status
mapping, isolation across checks, and report formatting/security)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

import pandas as pd
import pytest

from src import validation_suite as vs
from src.analytics import strategy_validation_report as svr
from src.data_providers import alpaca_provider as ap
from src.data_providers import base, iex_validation as iv
from src.execution import connectivity_preflight as cp
from src.execution.broker import ACCOUNT_MODE_PAPER, FakeBroker

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {
        "data": {"reports_dir": str(tmp_path / "reports")},
        "backtest": {"benchmarks": ["SPY"]},
        "strategy_universe": {"mean_reversion": ["AAPL"], "momentum_breakout": ["AAPL"], "trend_following": ["AAPL"]},
        "execution": {"halt_state_file": str(tmp_path / "trading_halt.json")},
    }


# --- _run_strategy_validation ---------------------------------------------------------------


def test_strategy_validation_reports_blocked_when_no_price_data(tmp_path, monkeypatch):
    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {})
    lines = vs._run_strategy_validation(_config(tmp_path), LOGGER)
    assert len(lines) == 1
    assert lines[0].status == vs.STATUS_BLOCKED


def test_strategy_validation_maps_each_verdict_to_the_right_status(tmp_path, monkeypatch):
    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {"AAPL": pd.DataFrame(), "SPY": pd.DataFrame()})

    fake_report = svr.ValidationReport(
        data_provenance="real_market_data",
        backtest_window={"start": "2026-01-01", "end": "2026-01-02"},
        results=[
            svr.StrategyValidationResult("Mean Reversion", ["AAPL"], {}, None, {}, svr.VERDICT_CONSISTENT_IN_BACKTEST, "good"),
            svr.StrategyValidationResult("Momentum Breakout", ["AAPL"], {}, None, {}, svr.VERDICT_INCONSISTENT, "bad"),
            svr.StrategyValidationResult("Trend Following", ["AAPL"], {}, None, {}, svr.VERDICT_INSUFFICIENT_EVIDENCE, "not enough"),
        ],
    )
    monkeypatch.setattr(svr, "run_validation_report", lambda *a, **k: fake_report)

    lines = vs._run_strategy_validation(_config(tmp_path), LOGGER)

    by_name = {l.name: l for l in lines}
    assert by_name["Mean Reversion"].status == vs.STATUS_PASS
    assert by_name["Momentum Breakout"].status == vs.STATUS_FAIL
    assert by_name["Trend Following"].status == vs.STATUS_SKIPPED
    assert all(l.section == "strategy_validation" for l in lines)


def test_strategy_validation_reports_blocked_when_report_is_none(tmp_path, monkeypatch):
    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {"AAPL": pd.DataFrame({"close": [1]})})
    monkeypatch.setattr(svr, "run_validation_report", lambda *a, **k: None)

    lines = vs._run_strategy_validation(_config(tmp_path), LOGGER)
    assert len(lines) == 1 and lines[0].status == vs.STATUS_BLOCKED


# --- _run_iex_data_check -------------------------------------------------------------------


def test_iex_check_is_skipped_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: False)
    lines = vs._run_iex_data_check(_config(tmp_path), LOGGER)
    assert len(lines) == 1
    assert lines[0].status == vs.STATUS_SKIPPED
    assert "ALPACA_API_KEY_ID" in lines[0].detail


def test_iex_check_maps_each_symbol_status(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    fake_report = iv.ValidationReport(
        results=[
            iv.SymbolValidationResult("SPY", iv.STATUS_PASS, "all good"),
            iv.SymbolValidationResult("AAPL", iv.STATUS_FAIL, "bad bars"),
            iv.SymbolValidationResult("MSFT", iv.STATUS_UNVALIDATED, "network unavailable"),
        ]
    )
    monkeypatch.setattr(iv, "run_validation", lambda *a, **k: fake_report)

    lines = vs._run_iex_data_check(_config(tmp_path), LOGGER)

    by_name = {l.name: l for l in lines}
    assert by_name["SPY"].status == vs.STATUS_PASS
    assert by_name["AAPL"].status == vs.STATUS_FAIL
    assert by_name["MSFT"].status == vs.STATUS_BLOCKED
    assert all(l.section == "alpaca_iex" for l in lines)


# --- _run_connectivity_preflight -----------------------------------------------------------


def test_connectivity_preflight_reports_blocked_when_connect_fails(tmp_path, monkeypatch):
    from src.execution import ibkr_client

    class _AlwaysFails:
        def __init__(self, config):
            pass

        def connect(self):
            raise ibkr_client.IBKRConnectionError("simulated: no TWS/Gateway reachable")

    monkeypatch.setattr(ibkr_client, "IBKRClient", _AlwaysFails)
    monkeypatch.setattr(
        ibkr_client.IBKRConfig, "from_env",
        classmethod(lambda cls: ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id="")),
    )

    lines = vs._run_connectivity_preflight(_config(tmp_path), LOGGER, run_reconnect_check=False)

    names = {l.name for l in lines}
    assert {"paper_identity", "reconnect_handling", "order_reconciliation"} <= names
    assert all(l.status == vs.STATUS_BLOCKED for l in lines if l.name in ("paper_identity", "reconnect_handling", "order_reconciliation"))
    # The kill switch needs no broker - it still runs and reports for real.
    kill_switch_lines = [l for l in lines if l.name == "kill_switch"]
    assert len(kill_switch_lines) == 1
    assert kill_switch_lines[0].status == vs.STATUS_PASS


def test_connectivity_preflight_skips_reconnect_check_by_default(tmp_path, monkeypatch):
    from src.execution import ibkr_client

    fake_broker = FakeBroker(account_mode=ACCOUNT_MODE_PAPER)

    class _Succeeds:
        def __init__(self, config):
            pass

        def connect(self):
            fake_broker.connect()

        def account_summary(self):
            return fake_broker.account_summary()

        def connection_state(self):
            return fake_broker.connection_state()

        def positions(self):
            return fake_broker.positions()

        def open_orders(self):
            return fake_broker.open_orders()

        def disconnect(self):
            fake_broker.disconnect()

    monkeypatch.setattr(ibkr_client, "IBKRClient", _Succeeds)
    monkeypatch.setattr(
        ibkr_client.IBKRConfig, "from_env",
        classmethod(lambda cls: ibkr_client.IBKRConfig(host="127.0.0.1", port=7497, client_id=1, account_id="")),
    )

    lines = vs._run_connectivity_preflight(_config(tmp_path), LOGGER, run_reconnect_check=False)

    reconnect_lines = [l for l in lines if l.name == "reconnect_handling"]
    assert len(reconnect_lines) == 1
    assert reconnect_lines[0].status == vs.STATUS_SKIPPED
    assert "skipped by default" in reconnect_lines[0].detail


# --- run_validation_suite: isolation ------------------------------------------------------


def test_run_validation_suite_isolates_a_crashing_check(tmp_path, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(vs, "_run_strategy_validation", _raise)
    monkeypatch.setattr(vs, "_run_iex_data_check", lambda config, logger: [vs.SuiteLine("alpaca_iex", "SPY", vs.STATUS_PASS, "ok")])
    monkeypatch.setattr(vs, "_run_connectivity_preflight", lambda config, logger, run_reconnect_check: [vs.SuiteLine("ibkr_preflight", "kill_switch", vs.STATUS_PASS, "ok")])

    report = vs.run_validation_suite(_config(tmp_path), LOGGER)

    statuses = {l.name: l.status for l in report.lines}
    assert statuses["-"] == vs.STATUS_BLOCKED  # the crashed check
    assert statuses["SPY"] == vs.STATUS_PASS
    assert statuses["kill_switch"] == vs.STATUS_PASS


def test_run_validation_suite_records_start_and_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_run_strategy_validation", lambda config, logger: [])
    monkeypatch.setattr(vs, "_run_iex_data_check", lambda config, logger: [])
    monkeypatch.setattr(vs, "_run_connectivity_preflight", lambda config, logger, run_reconnect_check: [])

    report = vs.run_validation_suite(_config(tmp_path), LOGGER)
    assert report.started_at is not None
    assert report.finished_at is not None
    assert report.finished_at >= report.started_at


# --- SuiteReport / format_suite_report_text / save_suite_report_text -----------------------


def test_counts_tallies_every_status():
    report = vs.SuiteReport(
        started_at=datetime.now(),
        lines=[
            vs.SuiteLine("a", "x", vs.STATUS_PASS, "ok"),
            vs.SuiteLine("a", "y", vs.STATUS_PASS, "ok"),
            vs.SuiteLine("a", "z", vs.STATUS_FAIL, "bad"),
            vs.SuiteLine("b", "w", vs.STATUS_SKIPPED, "n/a"),
            vs.SuiteLine("b", "v", vs.STATUS_BLOCKED, "no network"),
        ],
    )
    assert report.counts == {vs.STATUS_PASS: 2, vs.STATUS_FAIL: 1, vs.STATUS_SKIPPED: 1, vs.STATUS_BLOCKED: 1}
    assert report.any_failed is True


def test_format_text_groups_by_section_and_reports_counts():
    report = vs.SuiteReport(
        started_at=datetime.now(),
        lines=[
            vs.SuiteLine("strategy_validation", "Mean Reversion", vs.STATUS_PASS, "ok"),
            vs.SuiteLine("alpaca_iex", "SPY", vs.STATUS_SKIPPED, "not configured"),
        ],
    )
    text = vs.format_suite_report_text(report)
    assert "--- strategy_validation ---" in text
    assert "--- alpaca_iex ---" in text
    assert "1 PASS, 0 FAIL, 1 SKIPPED, 0 BLOCKED" in text
    assert "no FAILs" in text
    assert "remain OFF" in text


def test_format_text_reports_failure_result_line():
    report = vs.SuiteReport(started_at=datetime.now(), lines=[vs.SuiteLine("a", "x", vs.STATUS_FAIL, "bad")])
    text = vs.format_suite_report_text(report)
    assert "FAILED" in text


def test_format_text_redacts_a_leaked_secret_in_a_detail_string():
    report = vs.SuiteReport(started_at=datetime.now(), lines=[vs.SuiteLine("a", "x", vs.STATUS_FAIL, "token: abc123secretvalue")])
    text = vs.format_suite_report_text(report)
    assert "abc123secretvalue" not in text
    assert "[REDACTED]" in text


def test_save_suite_report_text_writes_the_expected_file(tmp_path):
    config = _config(tmp_path)
    out_path = vs.save_suite_report_text("hello world", config, now=datetime(2026, 4, 1, 9, 30, 0))
    assert out_path == tmp_path / "reports" / "validation_suite_2026-04-01_093000.txt"
    assert out_path.read_text(encoding="utf-8") == "hello world"


# --- CLI entry point --------------------------------------------------------------------


def test_main_returns_nonzero_when_any_check_failed(tmp_path, monkeypatch):
    fake_report = vs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[vs.SuiteLine("a", "x", vs.STATUS_FAIL, "bad")])
    monkeypatch.setattr(vs, "run_validation_suite", lambda config, logger, run_reconnect_check=False: fake_report)
    monkeypatch.setattr(sys, "argv", ["validation_suite.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    assert vs.main() == 1


def test_main_returns_zero_when_nothing_failed(tmp_path, monkeypatch):
    fake_report = vs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[vs.SuiteLine("a", "x", vs.STATUS_PASS, "ok")])
    captured = {}

    def _fake_run(config, logger, run_reconnect_check=False):
        captured["run_reconnect_check"] = run_reconnect_check
        return fake_report

    monkeypatch.setattr(vs, "run_validation_suite", _fake_run)
    monkeypatch.setattr(sys, "argv", ["validation_suite.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    assert vs.main() == 0
    assert captured["run_reconnect_check"] is False


def test_main_passes_full_reconnect_check_flag_through(tmp_path, monkeypatch):
    fake_report = vs.SuiteReport(started_at=datetime.now(), finished_at=datetime.now(), lines=[])
    captured = {}

    def _fake_run(config, logger, run_reconnect_check=False):
        captured["run_reconnect_check"] = run_reconnect_check
        return fake_report

    monkeypatch.setattr(vs, "run_validation_suite", _fake_run)
    monkeypatch.setattr(sys, "argv", ["validation_suite.py", "--full-reconnect-check"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"reports_dir": str(tmp_path / "reports")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    vs.main()
    assert captured["run_reconnect_check"] is True
