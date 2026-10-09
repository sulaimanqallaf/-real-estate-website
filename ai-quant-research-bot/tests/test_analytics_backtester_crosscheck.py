"""src/analytics/backtester_crosscheck.py - Phase 3's "compare results
with existing backtester using IDENTICAL datasets/execution
assumptions."

Runs the REAL src/backtester.py strategy loop against a real (seeded,
deterministic, clearly-labeled synthetic) price series - only the
VectorBT subprocess call is mocked, so these tests exercise the actual
trade-selection and stats logic this module depends on, not a stand-in
for it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

import pytest

from src.analytics import backtester_crosscheck, oss_quant_adapter
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config():
    return load_config(CONFIG_PATH)


def test_rejects_an_unlabeled_data_provenance():
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1)
    with pytest.raises(ValueError, match="data_provenance"):
        backtester_crosscheck.run_crosscheck("SYN1", "Trend Following", {"SYN1": df}, config, LOGGER, data_provenance="made_up")


def test_returns_none_when_backtester_produces_zero_trades():
    config = fresh_config()
    # A flat, zero-volatility price series never satisfies any
    # strategy's trigger condition (e.g. Trend Following needs
    # ema_50 > ema_200, impossible on a constant series) - genuinely
    # zero trades, not a mocked-away answer.
    import pandas as pd

    dates = pd.bdate_range("2023-01-02", periods=550)
    flat = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000.0}, index=dates)
    flat.index.name = "date"

    result = backtester_crosscheck.run_crosscheck("FLAT", "Trend Following", {"FLAT": flat}, config, LOGGER, data_provenance="synthetic_fixture")
    assert result is None


def test_returns_none_when_oss_quant_venv_not_set_up(monkeypatch):
    config = fresh_config()
    config["analytics"] = {"oss_quant": {"python_executable": "/nonexistent/python"}}
    df = generate_synthetic_ohlcv(seed=1)

    result = backtester_crosscheck.run_crosscheck("SYN1", "Trend Following", {"SYN1": df}, config, LOGGER, data_provenance="synthetic_fixture")
    assert result is None


def test_crosscheck_reports_agreement_when_vectorbt_echoes_our_own_numbers(monkeypatch):
    """A fake run_task that recomputes total_return/max_drawdown from the
    SAME trades our backtester produced (deterministically, without
    needing the real isolated venv) should land within tolerance -
    proving the comparison logic itself (not just VectorBT's real
    output) is correct."""
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1)

    from src import backtester
    from datetime import timedelta

    lookback_days = backtester.period_to_days(config["backtest"]["lookback_period"])
    backtest_start = df.index.max() - timedelta(days=lookback_days)
    trades, equity_curve = backtester._run_strategy_backtest("Trend Following", ["SYN1"], {"SYN1": df}, backtest_start, config, LOGGER)
    assert trades, "fixture must produce at least one real trade for this test to mean anything"
    our_stats = backtester._compute_stats(trades, equity_curve, config["backtest"]["initial_capital"])

    def fake_run_task(task, payload, cfg, logger, timeout_seconds=None):
        assert task == "vectorbt_trade_replay"
        realized = sum((xp - ep) * sz for ep, xp, sz in zip(payload["entry_prices"], payload["exit_prices"], payload["sizes"]))
        return {
            "ok": True,
            "total_return": realized / payload["init_cash"],
            "sharpe": our_stats["sharpe_ratio"],
            "max_drawdown": abs(our_stats["max_drawdown_pct"]) / 100.0,
            "num_trades": len(trades),
            "win_rate": our_stats["win_rate_pct"] / 100.0,
        }

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(backtester_crosscheck.oss_quant_adapter, "run_task", fake_run_task)

    result = backtester_crosscheck.run_crosscheck("SYN1", "Trend Following", {"SYN1": df}, config, LOGGER, data_provenance="synthetic_fixture")

    assert result is not None
    assert result["data_provenance"] == "synthetic_fixture"
    assert result["all_agree_within_tolerance"] is True
    assert result["comparisons"]["total_return_pct"]["agree"] is True
    assert isinstance(result["comparisons"]["total_return_pct"]["agree"], bool)


def test_crosscheck_reports_disagreement_honestly_when_fed_divergent_numbers(monkeypatch):
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1)

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        backtester_crosscheck.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {
            "ok": True, "total_return": 999.0, "sharpe": -500.0, "max_drawdown": 0.99, "num_trades": 1, "win_rate": 0.0,
        },
    )

    result = backtester_crosscheck.run_crosscheck("SYN1", "Trend Following", {"SYN1": df}, config, LOGGER, data_provenance="synthetic_fixture")

    assert result is not None
    assert result["all_agree_within_tolerance"] is False
    assert result["comparisons"]["total_return_pct"]["agree"] is False


def test_crosscheck_returns_none_when_the_replay_task_itself_fails(monkeypatch):
    config = fresh_config()
    df = generate_synthetic_ohlcv(seed=1)

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        backtester_crosscheck.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {"ok": False, "error": "simulated failure"},
    )

    result = backtester_crosscheck.run_crosscheck("SYN1", "Trend Following", {"SYN1": df}, config, LOGGER, data_provenance="synthetic_fixture")
    assert result is None
