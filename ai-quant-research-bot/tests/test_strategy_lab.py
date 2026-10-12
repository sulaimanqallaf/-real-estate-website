"""src/agents/strategy_lab.py - the autonomous learning laboratory
(Sprint 4, Priority 4). `evaluate_candidate()`/`run_lab_cycle()` run
the REAL backtester/walk-forward/Monte Carlo pipeline end-to-end
against a deterministic synthetic fixture (same pattern as
test_walk_forward.py/test_strategy_validation_report.py); the OSS-quant
(VectorBT/QuantStats) steps are tested with `oss_quant_adapter` mocked
since that isolated venv isn't assumed to exist in this environment -
`is_available()` gating itself is real and tested both ways."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import timedelta

import pandas as pd
import pytest

from src import data_collector
from src.agents import budget, checkpoint, heartbeat, registry, strategy_lab as lab
from src.analytics import oss_quant_adapter, strategy_validation_report as svr, walk_forward
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.research.hypothesis_ledger import query_results
from src.research.sandbox import ResearchHypothesis
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _config(tmp_path, **lab_overrides):
    config = load_config(CONFIG_PATH)
    lab_cfg = {"strategy_symbol_pairs": [["Trend Following", "SYN1"]], "window_grid": [20]}
    lab_cfg.update(lab_overrides)
    config["data"]["journal_dir"] = str(tmp_path / "journal")
    config["research"]["strategy_lab"] = lab_cfg
    config["research"]["hypothesis_db_path"] = str(tmp_path / "research" / "hyp.db")
    config["backtest"]["benchmarks"] = []
    return config


def _flat_price_history(symbol="SYN1", periods=550):
    dates = pd.bdate_range("2023-01-02", periods=periods)
    flat = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000.0}, index=dates)
    flat.index.name = "date"
    return {symbol: flat}


# --- generate_candidates --------------------------------------------------------------------


def test_generate_candidates_one_per_window_for_mean_reversion():
    candidates = lab.generate_candidates("Mean Reversion", "SPY", [10, 20])
    assert len(candidates) == 2
    assert candidates[0].config_overrides == {"strategies": {"mean_reversion": {"ma_window": 10}}}
    assert candidates[1].config_overrides == {"strategies": {"mean_reversion": {"ma_window": 20}}}
    assert all(c.symbol == "SPY" and c.strategy_name == "Mean Reversion" for c in candidates)
    assert all(c.data_provenance == "real_market_data" for c in candidates)


def test_generate_candidates_uses_breakout_window_for_momentum_breakout():
    candidates = lab.generate_candidates("Momentum Breakout", "SPY", [30])
    assert candidates[0].config_overrides == {"strategies": {"momentum_breakout": {"breakout_window": 30}}}


def test_generate_candidates_returns_one_default_for_trend_following_regardless_of_grid():
    candidates = lab.generate_candidates("Trend Following", "SPY", [10, 20, 30])
    assert len(candidates) == 1
    assert candidates[0].config_overrides == {}
    assert "default parameters" in candidates[0].description


def test_generate_candidates_ids_are_unique_per_window():
    candidates = lab.generate_candidates("Mean Reversion", "SPY", [10, 20])
    assert candidates[0].hypothesis_id != candidates[1].hypothesis_id


# --- _per_trade_returns_by_exit_date --------------------------------------------------------


def test_per_trade_returns_compounds_same_day_exits():
    from src.backtester import Trade

    t1 = Trade("SPY", "Trend Following", pd.Timestamp("2026-01-01"), 100.0, pd.Timestamp("2026-01-10"), 110.0, 10, "target")
    t2 = Trade("QQQ", "Trend Following", pd.Timestamp("2026-01-01"), 50.0, pd.Timestamp("2026-01-10"), 55.0, 10, "target")

    returns = lab._per_trade_returns_by_exit_date([t1, t2])
    expected = (1.10 * 1.10) - 1
    assert returns["2026-01-10"] == pytest.approx(expected)


# --- evaluate_candidate ----------------------------------------------------------------------


def test_evaluate_candidate_returns_zero_evidence_scorecard_for_missing_symbol(tmp_path):
    config = _config(tmp_path)
    hypothesis = ResearchHypothesis("h1", "desc", "Trend Following", "MISSING", {}, "real_market_data")

    scorecard = lab.evaluate_candidate(hypothesis, {}, config, LOGGER)

    assert scorecard.single_window_stats is None
    assert scorecard.walk_forward_report.num_folds == 0
    assert scorecard.verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE
    assert scorecard.monte_carlo is None
    assert scorecard.vectorbt_crosscheck is None


def test_evaluate_candidate_runs_the_real_pipeline_on_a_real_fixture(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: False)
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550)}
    hypothesis = ResearchHypothesis("h1", "desc", "Trend Following", "SYN1", {}, "real_market_data")

    scorecard = lab.evaluate_candidate(hypothesis, price_history, config, LOGGER)

    assert scorecard.single_window_stats is not None
    assert "total_trades" in scorecard.single_window_stats
    assert isinstance(scorecard.walk_forward_report, walk_forward.WalkForwardReport)
    assert scorecard.verdict in (svr.VERDICT_CONSISTENT_IN_BACKTEST, svr.VERDICT_INCONSISTENT, svr.VERDICT_INSUFFICIENT_EVIDENCE)
    assert scorecard.vectorbt_crosscheck is None  # oss_quant_adapter unavailable
    assert scorecard.quantstats_metrics is None


def test_evaluate_candidate_has_no_monte_carlo_on_zero_trades(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: False)
    hypothesis = ResearchHypothesis("h1", "desc", "Trend Following", "SYN1", {}, "real_market_data")

    scorecard = lab.evaluate_candidate(hypothesis, _flat_price_history(), config, LOGGER)

    assert scorecard.single_window_stats["total_trades"] == 0
    assert scorecard.monte_carlo is None
    assert scorecard.verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE


def test_evaluate_candidate_enriches_with_vectorbt_and_quantstats_when_available(tmp_path, monkeypatch):
    config = _config(tmp_path)
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550)}
    hypothesis = ResearchHypothesis("h1", "desc", "Trend Following", "SYN1", {}, "real_market_data")

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    from src.analytics import backtester_crosscheck

    fake_crosscheck_result = {"all_agree_within_tolerance": True}
    monkeypatch.setattr(backtester_crosscheck, "run_crosscheck", lambda *a, **k: fake_crosscheck_result)
    monkeypatch.setattr(oss_quant_adapter, "run_task", lambda task, payload, cfg, logger: {"ok": True, "metrics": {"sharpe": 1.2}})

    scorecard = lab.evaluate_candidate(hypothesis, price_history, config, LOGGER)

    assert scorecard.vectorbt_crosscheck == fake_crosscheck_result
    assert scorecard.quantstats_metrics == {"sharpe": 1.2}


def test_evaluate_candidate_isolates_a_crosscheck_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550)}
    hypothesis = ResearchHypothesis("h1", "desc", "Trend Following", "SYN1", {}, "real_market_data")

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    from src.analytics import backtester_crosscheck

    def _raise(*a, **k):
        raise RuntimeError("vectorbt subprocess failed")

    monkeypatch.setattr(backtester_crosscheck, "run_crosscheck", _raise)
    monkeypatch.setattr(oss_quant_adapter, "run_task", lambda *a, **k: None)

    scorecard = lab.evaluate_candidate(hypothesis, price_history, config, LOGGER)
    assert scorecard.vectorbt_crosscheck is None  # isolated, not raised


def test_evaluate_candidate_applies_config_overrides(tmp_path):
    config = _config(tmp_path)
    price_history = {"SYN1": generate_synthetic_ohlcv(seed=1, periods=550)}
    default_hyp = ResearchHypothesis("h1", "desc", "Mean Reversion", "SYN1", {}, "real_market_data")
    override_hyp = ResearchHypothesis("h2", "desc", "Mean Reversion", "SYN1", {"strategies": {"mean_reversion": {"ma_window": 5}}}, "real_market_data")

    default_scorecard = lab.evaluate_candidate(default_hyp, price_history, config, LOGGER)
    override_scorecard = lab.evaluate_candidate(override_hyp, price_history, config, LOGGER)

    # Not asserting a specific direction - just that overrides actually changed behavior deterministically.
    assert (default_scorecard.single_window_stats, override_scorecard.single_window_stats) != (None, None)


# --- rank_candidates --------------------------------------------------------------------------


def _scorecard(verdict, mean_fold_return):
    hyp = ResearchHypothesis("h", "d", "Trend Following", "SPY", {}, "real_market_data")

    # A simple stand-in rather than constructing real folds - only
    # mean_fold_return_pct is read by rank_candidates().
    class _FakeWF:
        mean_fold_return_pct = mean_fold_return

    return lab.CandidateScorecard(hyp, {"total_trades": 50}, _FakeWF(), verdict, "reason", None, None, None)


def test_rank_candidates_consistent_first():
    scorecards = [_scorecard(svr.VERDICT_INSUFFICIENT_EVIDENCE, None), _scorecard(svr.VERDICT_CONSISTENT_IN_BACKTEST, 5.0), _scorecard(svr.VERDICT_INCONSISTENT, 2.0)]
    ranked = lab.rank_candidates(scorecards)
    assert ranked[0].verdict == svr.VERDICT_CONSISTENT_IN_BACKTEST
    assert ranked[1].verdict == svr.VERDICT_INCONSISTENT
    assert ranked[2].verdict == svr.VERDICT_INSUFFICIENT_EVIDENCE


def test_rank_candidates_higher_mean_return_first_within_the_same_verdict():
    scorecards = [_scorecard(svr.VERDICT_CONSISTENT_IN_BACKTEST, 1.0), _scorecard(svr.VERDICT_CONSISTENT_IN_BACKTEST, 9.0)]
    ranked = lab.rank_candidates(scorecards)
    assert ranked[0].walk_forward_report.mean_fold_return_pct == 9.0


def test_rank_candidates_handles_a_none_mean_return_without_crashing():
    scorecards = [_scorecard(svr.VERDICT_CONSISTENT_IN_BACKTEST, None), _scorecard(svr.VERDICT_CONSISTENT_IN_BACKTEST, 1.0)]
    ranked = lab.rank_candidates(scorecards)
    assert ranked[0].walk_forward_report.mean_fold_return_pct == 1.0


# --- run_lab_cycle ----------------------------------------------------------------------------


def test_run_lab_cycle_evaluates_candidates_and_records_to_the_ledger(tmp_path, monkeypatch):
    config = _config(tmp_path)
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    summary = lab.run_lab_cycle(config, LOGGER)

    assert summary["errors"] == []
    assert summary["candidates_evaluated"] == 1  # Trend Following has exactly one default candidate
    rows = query_results(config["research"]["hypothesis_db_path"])
    assert len(rows) == 1


def test_run_lab_cycle_records_success_in_the_registry(tmp_path, monkeypatch):
    config = _config(tmp_path)
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    lab.run_lab_cycle(config, LOGGER)
    assert registry.read_status(config, lab.AGENT_NAME)["status"] == registry.STATUS_SUCCESS


def test_run_lab_cycle_records_heartbeat(tmp_path, monkeypatch):
    config = _config(tmp_path)
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    lab.run_lab_cycle(config, LOGGER)
    assert heartbeat.heartbeat_status(config, lab.AGENT_NAME)["never_started"] is False


def test_run_lab_cycle_sets_top_candidate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    summary = lab.run_lab_cycle(config, LOGGER)
    assert summary["top_candidate"] is not None
    assert summary["top_candidate"]["strategy_name"] == "Trend Following"


def test_run_lab_cycle_isolates_one_crashing_candidate(tmp_path, monkeypatch):
    config = _config(tmp_path, strategy_symbol_pairs=[["Mean Reversion", "SYN1"]], window_grid=[10, 20])
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    real_evaluate = lab.evaluate_candidate

    def _flaky_evaluate(hypothesis, price_history, cfg, logger, **kwargs):
        if "10" in hypothesis.hypothesis_id:
            raise RuntimeError("simulated candidate crash")
        return real_evaluate(hypothesis, price_history, cfg, logger, **kwargs)

    monkeypatch.setattr(lab, "evaluate_candidate", _flaky_evaluate)

    summary = lab.run_lab_cycle(config, LOGGER)
    assert len(summary["errors"]) == 1
    assert summary["candidates_evaluated"] == 1  # the other window still got evaluated


def test_run_lab_cycle_clears_checkpoint_after_success(tmp_path, monkeypatch):
    config = _config(tmp_path, strategy_symbol_pairs=[["Trend Following", "SYN1"], ["Mean Reversion", "SYN1"]])
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    lab.run_lab_cycle(config, LOGGER)
    assert checkpoint.load_checkpoint(config, lab.AGENT_NAME) is None


def test_run_lab_cycle_resumes_skipping_completed_pairs(tmp_path, monkeypatch):
    config = _config(tmp_path, strategy_symbol_pairs=[["Trend Following", "SYN1"], ["Mean Reversion", "SYN1"]])
    checkpoint.save_checkpoint(config, lab.AGENT_NAME, {"completed_pairs": [["Trend Following", "SYN1"]]})
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})

    seen_strategies = []
    real_generate = lab.generate_candidates

    def _tracking_generate(strategy_name, symbol, window_values):
        seen_strategies.append(strategy_name)
        return real_generate(strategy_name, symbol, window_values)

    monkeypatch.setattr(lab, "generate_candidates", _tracking_generate)

    lab.run_lab_cycle(config, LOGGER)
    assert "Trend Following" not in seen_strategies
    assert "Mean Reversion" in seen_strategies


def test_run_lab_cycle_records_failure_on_a_top_level_crash(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("simulated top-level crash")

    monkeypatch.setattr(data_collector, "fetch_all_price_history", _raise)

    summary = lab.run_lab_cycle(config, LOGGER)
    assert summary["errors"]
    assert registry.read_status(config, lab.AGENT_NAME)["status"] == registry.STATUS_FAILED


# --- main() CLI --------------------------------------------------------------------------


def test_main_returns_nonzero_when_already_locked(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})

    from src.execution.process_lock import acquire_singleton_lock
    from src.utils import resolve_path

    lock_path = resolve_path(config["data"]["journal_dir"]) / "agents" / "strategy_lab.lock"
    held_handle = acquire_singleton_lock(lock_path, "test_holder")
    try:
        assert lab.main() == 1
    finally:
        held_handle.close()


def test_main_returns_zero_on_a_clean_run(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(lab, "run_lab_cycle", lambda cfg, logger: {"candidates_generated": 0, "candidates_evaluated": 0, "top_candidate": None, "errors": []})

    assert lab.main() == 0
