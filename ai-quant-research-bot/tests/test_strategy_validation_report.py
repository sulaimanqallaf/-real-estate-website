"""src/analytics/strategy_validation_report.py - transparent,
consolidated strategy validation report (Sprint 3, "Real Strategy
Validation" milestone, Task V3). `_evidence_verdict` is tested directly
against hand-built stats/fold fixtures for exact threshold control;
`run_validation_report` is tested end-to-end against the REAL
backtester + walk-forward pipeline on a deterministic synthetic
fixture (same pattern as test_walk_forward.py and
test_analytics_backtester_crosscheck.py)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime

import pytest

from src.analytics import strategy_validation_report as svr
from src.analytics import walk_forward as wf
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config():
    return load_config(CONFIG_PATH)


def _wf_report(num_folds: int, fraction_profitable: float | None) -> wf.WalkForwardReport:
    """A WalkForwardReport with exactly the fraction_profitable this
    test wants, built directly from hand-picked fold returns rather
    than relying on real strategy behavior to land on a round number."""
    if num_folds == 0:
        folds = []
    else:
        num_profitable = round((fraction_profitable or 0.0) * num_folds)
        returns = [5.0] * num_profitable + [-5.0] * (num_folds - num_profitable)
        folds = [wf.Fold(i, f"2023-{i+1:02d}-01", f"2023-{i+1:02d}-28", {"total_return_pct": r}, {}) for i, r in enumerate(returns)]
    return wf.WalkForwardReport("Trend Following", ["AAPL"], "90d", folds)


# --- _evidence_verdict ---------------------------------------------------------------------


def test_verdict_is_insufficient_evidence_with_too_few_trades():
    stats = {"total_trades": 5, "total_return_pct": 50.0}
    verdict, reason = svr._evidence_verdict(stats, _wf_report(num_folds=5, fraction_profitable=1.0))
    assert verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE
    assert "5 trade" in reason


def test_verdict_is_insufficient_evidence_with_too_few_folds():
    stats = {"total_trades": 100, "total_return_pct": 50.0}
    verdict, reason = svr._evidence_verdict(stats, _wf_report(num_folds=2, fraction_profitable=1.0))
    assert verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE
    assert "2 walk-forward fold" in reason


def test_verdict_is_inconsistent_when_fewer_than_half_the_folds_are_profitable():
    stats = {"total_trades": 100, "total_return_pct": 50.0}
    verdict, reason = svr._evidence_verdict(stats, _wf_report(num_folds=10, fraction_profitable=0.4))
    assert verdict == svr.VERDICT_INCONSISTENT
    assert "40%" in reason


def test_verdict_is_consistent_at_exactly_the_fraction_threshold():
    stats = {"total_trades": 100, "total_return_pct": 50.0}
    verdict, _ = svr._evidence_verdict(stats, _wf_report(num_folds=10, fraction_profitable=0.5))
    assert verdict == svr.VERDICT_CONSISTENT_IN_BACKTEST


def test_verdict_is_consistent_with_enough_evidence_and_majority_profitable_folds():
    stats = {"total_trades": 100, "total_return_pct": 50.0}
    verdict, reason = svr._evidence_verdict(stats, _wf_report(num_folds=10, fraction_profitable=0.8))
    assert verdict == svr.VERDICT_CONSISTENT_IN_BACKTEST
    assert "NOT a forecast" in reason


def test_verdict_thresholds_are_at_the_module_level_not_hardcoded_twice():
    # Exactly at the trade/fold floor should NOT be flagged insufficient.
    stats = {"total_trades": svr.MIN_TRADES_FOR_EVIDENCE, "total_return_pct": 10.0}
    verdict, _ = svr._evidence_verdict(stats, _wf_report(num_folds=svr.MIN_FOLDS_FOR_EVIDENCE, fraction_profitable=1.0))
    assert verdict != svr.VERDICT_INSUFFICIENT_EVIDENCE


# --- run_validation_report (end-to-end, real backtester + walk-forward) ------------------


def _single_symbol_config():
    config = fresh_config()
    config["strategy_universe"] = {
        "mean_reversion": ["SYN1"],
        "momentum_breakout": ["SYN1"],
        "trend_following": ["SYN1"],
    }
    config["backtest"]["benchmarks"] = ["SPY"]
    return config


def test_rejects_an_unlabeled_data_provenance():
    config = _single_symbol_config()
    with pytest.raises(ValueError, match="data_provenance"):
        svr.run_validation_report({"SYN1": generate_synthetic_ohlcv(seed=1)}, config, LOGGER, data_provenance="made_up")


def test_returns_none_when_price_history_is_empty():
    config = _single_symbol_config()
    assert svr.run_validation_report({}, config, LOGGER, data_provenance="synthetic_fixture") is None


def test_produces_one_result_per_strategy_with_a_valid_verdict_and_benchmark():
    config = _single_symbol_config()
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550), "SPY": generate_synthetic_ohlcv(seed=2, periods=550)}

    report = svr.run_validation_report(price_history, config, LOGGER, data_provenance="synthetic_fixture")

    assert report is not None
    assert report.data_provenance == "synthetic_fixture"
    assert {r.strategy_name for r in report.results} == {"Mean Reversion", "Momentum Breakout", "Trend Following"}
    for r in report.results:
        assert r.verdict in (svr.VERDICT_INSUFFICIENT_EVIDENCE, svr.VERDICT_INCONSISTENT, svr.VERDICT_CONSISTENT_IN_BACKTEST)
        assert r.verdict_reason
        assert "SPY" in r.benchmark_buy_and_hold_pct
        assert isinstance(r.walk_forward_report, wf.WalkForwardReport)


def test_benchmark_is_absent_when_its_price_history_is_not_provided():
    config = _single_symbol_config()
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550)}  # no SPY

    report = svr.run_validation_report(price_history, config, LOGGER, data_provenance="synthetic_fixture")

    assert report is not None
    for r in report.results:
        assert r.benchmark_buy_and_hold_pct == {}


def test_low_trade_count_fixture_is_flagged_insufficient_not_fabricated_profitable():
    """A flat, zero-volatility series never satisfies any strategy's
    trigger condition - genuinely zero trades. The report must say so
    honestly (INSUFFICIENT_EVIDENCE), never claim consistency."""
    import pandas as pd

    config = _single_symbol_config()
    dates = pd.bdate_range("2023-01-02", periods=550)
    flat = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000.0}, index=dates)
    flat.index.name = "date"

    report = svr.run_validation_report({"SYN1": flat}, config, LOGGER, data_provenance="synthetic_fixture")

    assert report is not None
    for r in report.results:
        assert r.single_window_stats.get("total_trades", 0) == 0
        assert r.verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE


# --- format_validation_report_text ---------------------------------------------------------


def test_format_text_includes_provenance_disclaimer_and_every_strategy():
    config = _single_symbol_config()
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550), "SPY": generate_synthetic_ohlcv(seed=2, periods=550)}
    report = svr.run_validation_report(price_history, config, LOGGER, data_provenance="synthetic_fixture")

    text = svr.format_validation_report_text(report)

    assert "Data provenance: synthetic_fixture" in text
    assert "NOT a forecast" in text
    for name in ("Mean Reversion", "Momentum Breakout", "Trend Following"):
        assert name in text
        assert "VERDICT" in text


# --- save_validation_report_text ------------------------------------------------------------


def test_save_validation_report_text_writes_the_expected_file(tmp_path):
    config = fresh_config()
    config["data"]["reports_dir"] = str(tmp_path / "reports")

    out_path = svr.save_validation_report_text("hello world", config, now=datetime(2026, 3, 15))

    assert out_path == tmp_path / "reports" / "strategy_validation_2026-03-15.txt"
    assert out_path.read_text(encoding="utf-8") == "hello world"


# --- CLI entry point --------------------------------------------------------------------


def test_main_aborts_cleanly_when_no_price_data_is_fetched(tmp_path, monkeypatch):
    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {})
    monkeypatch.setattr(sys, "argv", ["strategy_validation_report.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    config = fresh_config()
    config["logging"] = {"log_dir": str(tmp_path / "logs")}
    monkeypatch.setattr("src.utils.load_config", lambda path: config)

    assert svr.main() == 1


def test_main_prints_and_saves_a_report_when_data_is_available(tmp_path, monkeypatch, capsys):
    from src import data_collector

    config = _single_symbol_config()
    config["data"]["reports_dir"] = str(tmp_path / "reports")
    config["logging"] = {"log_dir": str(tmp_path / "logs")}
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550), "SPY": generate_synthetic_ohlcv(seed=2, periods=550)}

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: price_history)
    monkeypatch.setattr(sys, "argv", ["strategy_validation_report.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config)

    exit_code = svr.main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "STRATEGY VALIDATION REPORT" in captured.out
    assert "Saved to" in captured.out
    saved_files = list((tmp_path / "reports").glob("strategy_validation_*.txt"))
    assert len(saved_files) == 1
