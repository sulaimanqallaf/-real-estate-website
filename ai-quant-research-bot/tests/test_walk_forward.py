"""src/analytics/walk_forward.py - walk-forward, out-of-sample strategy
validation (Sprint 3, "Real Strategy Validation" milestone, Task V2).
`_build_folds` is tested directly on hand-built fixtures for exact
boundary control; `run_walk_forward` is tested end-to-end against the
REAL backtester strategy loop on a deterministic synthetic fixture
(same pattern as tests/test_analytics_backtester_crosscheck.py) so
these tests exercise the actual fold-splitting + aggregation logic,
not a stand-in for it."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src.analytics import walk_forward as wf
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config():
    return load_config(CONFIG_PATH)


def _flat_df(start: str, periods: int) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=periods)
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000.0}, index=dates)


# --- _build_folds ------------------------------------------------------------------------


def test_build_folds_returns_nothing_for_empty_input():
    assert wf._build_folds({}, 90) == []


def test_build_folds_returns_nothing_when_every_frame_is_empty():
    assert wf._build_folds({"AAPL": pd.DataFrame(columns=["close"])}, 90) == []


def test_build_folds_splits_into_non_overlapping_sequential_windows():
    df = _flat_df("2023-01-02", periods=400)  # ~19 months of business days
    folds = wf._build_folds({"AAPL": df}, 90)

    assert len(folds) >= 2
    for (s1, e1), (s2, e2) in zip(folds, folds[1:]):
        assert e1 == s2  # exactly contiguous, no gap and no overlap
        assert s1 < e1
    assert folds[0][0] == df.index.min()
    assert folds[-1][1] <= df.index.max()


def test_build_folds_uses_only_the_intersection_of_symbols_date_ranges():
    long_df = _flat_df("2020-01-02", periods=1200)  # extends well past short_df's end
    short_df = _flat_df("2023-01-02", periods=200)  # starts much later, ends earlier

    folds = wf._build_folds({"LONG": long_df, "SHORT": short_df}, 90)

    assert folds, "expected at least one fold within the overlapping range"
    assert folds[0][0] >= short_df.index.min()
    assert folds[-1][1] <= short_df.index.max()


def test_build_folds_returns_nothing_when_the_overlap_is_shorter_than_one_fold():
    a = _flat_df("2023-01-02", periods=10)
    b = _flat_df("2023-01-02", periods=10)
    assert wf._build_folds({"A": a, "B": b}, 90) == []


# --- run_walk_forward (end-to-end, real strategy loop) -----------------------------------


def test_run_walk_forward_produces_multiple_folds_on_real_strategy_logic():
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1, periods=550)  # ~2+ years of business days

    report = wf.run_walk_forward("Trend Following", ["SYN1"], {"SYN1": df}, config, LOGGER, fold_period="90d")

    assert report.strategy_name == "Trend Following"
    assert report.num_folds >= 2
    for fold in report.folds:
        assert "total_return_pct" in fold.stats
        assert fold.fold_index >= 0


def test_run_walk_forward_computes_benchmark_buy_and_hold_per_fold():
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1, periods=550)
    bench = generate_synthetic_ohlcv(seed=2, periods=550)

    report = wf.run_walk_forward("Trend Following", ["SYN1"], {"SYN1": df, "SPY": bench}, config, LOGGER, fold_period="90d", benchmark_symbols=["SPY"])

    assert report.num_folds >= 2
    assert all("SPY" in f.benchmark_buy_and_hold_pct for f in report.folds)
    assert all(f.benchmark_buy_and_hold_pct["SPY"] is not None for f in report.folds)


def test_run_walk_forward_returns_zero_folds_when_symbol_is_missing():
    config = fresh_config()
    report = wf.run_walk_forward("Trend Following", ["MISSING"], {}, config, LOGGER, fold_period="90d")
    assert report.num_folds == 0
    assert report.folds == []


def test_run_walk_forward_defaults_fold_period_from_config():
    config = fresh_config()
    config["backtest"]["walk_forward"] = {"fold_period": "30d"}
    df = generate_synthetic_ohlcv(seed=1, periods=550)

    report = wf.run_walk_forward("Trend Following", ["SYN1"], {"SYN1": df}, config, LOGGER)  # no fold_period arg
    assert report.fold_period == "30d"


def test_run_walk_forward_all_strategies_covers_every_configured_strategy():
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1, periods=550)
    universe = config["strategy_universe"]
    all_symbols = set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"])
    price_history = {s: df for s in all_symbols}

    reports = wf.run_walk_forward_all_strategies(price_history, config, LOGGER, fold_period="180d")

    assert set(reports.keys()) == {"Mean Reversion", "Momentum Breakout", "Trend Following"}
    assert all(isinstance(r, wf.WalkForwardReport) for r in reports.values())


# --- WalkForwardReport properties ---------------------------------------------------------


def _report_with_returns(returns_pct: list[float], benchmark_returns: list[float | None] | None = None) -> wf.WalkForwardReport:
    benchmark_returns = benchmark_returns or [None] * len(returns_pct)
    folds = [
        wf.Fold(i, f"2023-0{i+1}-01", f"2023-0{i+1}-28", {"total_return_pct": r}, {"SPY": b})
        for i, (r, b) in enumerate(zip(returns_pct, benchmark_returns))
    ]
    return wf.WalkForwardReport("Trend Following", ["AAPL"], "90d", folds)


def test_report_properties_on_zero_folds():
    report = wf.WalkForwardReport("Trend Following", ["AAPL"], "90d", folds=[])
    assert report.num_folds == 0
    assert report.fraction_profitable is None
    assert report.mean_fold_return_pct is None
    assert report.std_fold_return_pct is None
    assert report.fraction_beating_benchmark("SPY") is None


def test_report_properties_on_a_mix_of_profitable_and_losing_folds():
    report = _report_with_returns([5.0, -2.0, 3.0, -1.0])
    assert report.num_folds == 4
    assert report.num_profitable_folds == 2
    assert report.fraction_profitable == pytest.approx(0.5)
    assert report.mean_fold_return_pct == pytest.approx(1.25)
    assert report.std_fold_return_pct is not None


def test_std_fold_return_is_none_with_fewer_than_two_folds():
    report = _report_with_returns([5.0])
    assert report.std_fold_return_pct is None


def test_fraction_beating_benchmark_only_counts_folds_with_both_numbers():
    report = _report_with_returns([5.0, -2.0, 3.0], benchmark_returns=[2.0, None, 4.0])
    # fold 0: 5.0 > 2.0 (beat); fold 1: benchmark unknown, excluded; fold 2: 3.0 < 4.0 (did not beat)
    assert report.fraction_beating_benchmark("SPY") == pytest.approx(0.5)


def test_fraction_beating_benchmark_is_none_with_no_usable_pairs():
    report = _report_with_returns([5.0, -2.0], benchmark_returns=[None, None])
    assert report.fraction_beating_benchmark("SPY") is None


# --- format_walk_forward_text -------------------------------------------------------------


def test_format_text_reports_no_folds_honestly():
    report = wf.WalkForwardReport("Trend Following", ["AAPL"], "90d", folds=[])
    text = wf.format_walk_forward_text(report)
    assert "No folds" in text


def test_format_text_includes_every_fold_and_a_summary_line():
    report = _report_with_returns([5.0, -2.0], benchmark_returns=[2.0, 1.0])
    text = wf.format_walk_forward_text(report)
    assert "Fold 0" in text and "Fold 1" in text
    assert "Summary" in text
    assert "Beat SPY buy & hold" in text


# --- CLI entry point --------------------------------------------------------------------


def test_main_aborts_cleanly_when_no_price_data_is_fetched(tmp_path, monkeypatch):
    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {})
    monkeypatch.setattr(sys, "argv", ["walk_forward.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: fresh_config() | {"logging": {"log_dir": str(tmp_path / "logs")}})

    assert wf.main() == 1


def test_main_prints_a_report_per_strategy_when_data_is_available(tmp_path, monkeypatch, capsys):
    from src import data_collector

    df = generate_synthetic_ohlcv(seed=1, periods=550)
    config = fresh_config()
    universe = config["strategy_universe"]
    all_symbols = set(universe["mean_reversion"] + universe["momentum_breakout"] + universe["trend_following"] + config["backtest"]["benchmarks"])

    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, config, logger: {s: df for s in all_symbols})
    monkeypatch.setattr(sys, "argv", ["walk_forward.py", "--fold-period", "180d"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})

    exit_code = wf.main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Mean Reversion" in captured.out
    assert "Momentum Breakout" in captured.out
    assert "Trend Following" in captured.out
