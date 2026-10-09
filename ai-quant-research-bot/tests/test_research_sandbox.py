"""src/research/sandbox.py - Phase 2's RD-Agent-concept isolated research
sandbox. Runs the REAL src/backtester.py strategy loop against a real
(seeded, clearly-labeled synthetic) price series - no mocks for the
backtesting logic itself, only the data source is synthetic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

import pytest

from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.research import sandbox
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
SANDBOX_SOURCE = Path(__file__).resolve().parent.parent / "src" / "research" / "sandbox.py"


def fresh_config():
    return load_config(CONFIG_PATH)


# --- the hard safety boundary itself, pinned as a regression test -----------------------


def test_sandbox_module_never_imports_the_execution_package():
    """The core safety guarantee for Phase 2's "strategies CANNOT submit
    broker orders": grep the module's OWN source for any import of
    src.execution (order_manager, ibkr_client, broker, ...) - if a
    future edit ever adds one, this test fails the build rather than
    silently opening a path to a real order."""
    source = SANDBOX_SOURCE.read_text()
    forbidden_import_patterns = ("from ..execution", "from .execution", "from src.execution", "import src.execution", "import execution")
    for pattern in forbidden_import_patterns:
        assert pattern not in source, f"sandbox.py must never import src.execution (found {pattern!r}) - see its own module docstring's safety boundary"


def test_sandbox_module_makes_no_network_calls():
    """Static guardrail: no import of any networking/broker/exchange
    library anywhere in sandbox.py's source - it only ever consumes
    price_history the caller already loaded."""
    source = SANDBOX_SOURCE.read_text()
    for forbidden in ("requests", "yfinance", "ccxt", "ib_insync", "urllib", "httpx", "socket"):
        assert forbidden not in source, f"sandbox.py must never import {forbidden!r} - it must not reach the network"


def test_sandbox_has_no_promote_function():
    source = SANDBOX_SOURCE.read_text()
    assert "def promote" not in source


# --- config override allowlist -----------------------------------------------------------


def test_disallowed_override_section_is_rejected():
    hyp = sandbox.ResearchHypothesis(
        hypothesis_id="h1", description="test", strategy_name="Trend Following", symbol="SYN1",
        config_overrides={"execution": {"mode": "live"}},
    )
    df = generate_synthetic_ohlcv(seed=1)
    with pytest.raises(ValueError, match="disallowed"):
        sandbox.run_hypothesis(hyp, {"SYN1": df}, fresh_config(), LOGGER)


def test_allowed_override_section_is_applied():
    config = fresh_config()
    base_multiplier = config["strategies"]["trend_following"]["atr_stop_multiplier"]
    hyp = sandbox.ResearchHypothesis(
        hypothesis_id="h2", description="wider stop", strategy_name="Trend Following", symbol="SYN1",
        config_overrides={"strategies": {"trend_following": {"atr_stop_multiplier": base_multiplier * 2}}},
    )
    df = generate_synthetic_ohlcv(seed=1)
    result = sandbox.run_hypothesis(hyp, {"SYN1": df}, config, LOGGER)
    assert result is not None
    # base config itself must be untouched by the override (no mutation leak).
    assert config["strategies"]["trend_following"]["atr_stop_multiplier"] == base_multiplier


def test_non_dict_section_override_rejected():
    hyp = sandbox.ResearchHypothesis(
        hypothesis_id="h3", description="bad", strategy_name="Trend Following", symbol="SYN1",
        config_overrides={"strategies": "not a dict"},
    )
    df = generate_synthetic_ohlcv(seed=1)
    with pytest.raises(ValueError):
        sandbox.run_hypothesis(hyp, {"SYN1": df}, fresh_config(), LOGGER)


# --- running a real hypothesis through the real backtester ------------------------------


def test_run_hypothesis_returns_real_stats_for_a_known_good_symbol():
    config = fresh_config()
    hyp = sandbox.ResearchHypothesis(
        hypothesis_id="h4", description="baseline trend following", strategy_name="Trend Following", symbol="SYN1",
        data_provenance="synthetic_fixture",
    )
    df = generate_synthetic_ohlcv(seed=42)
    result = sandbox.run_hypothesis(hyp, {"SYN1": df}, config, LOGGER)

    assert result is not None
    assert result["trade_count"] > 0
    assert result["stats"]["total_trades"] == result["trade_count"]
    assert result["data_provenance"] == "synthetic_fixture"


def test_run_hypothesis_returns_none_for_missing_symbol():
    hyp = sandbox.ResearchHypothesis(hypothesis_id="h5", description="x", strategy_name="Trend Following", symbol="MISSING")
    assert sandbox.run_hypothesis(hyp, {}, fresh_config(), LOGGER) is None


def test_run_hypothesis_reports_zero_trades_honestly_not_as_an_error():
    import pandas as pd

    dates = pd.bdate_range("2023-01-02", periods=550)
    flat = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000.0}, index=dates)
    flat.index.name = "date"
    hyp = sandbox.ResearchHypothesis(hypothesis_id="h6", description="flat series", strategy_name="Trend Following", symbol="FLAT")

    result = sandbox.run_hypothesis(hyp, {"FLAT": flat}, fresh_config(), LOGGER)
    assert result is not None
    assert result["trade_count"] == 0
    assert result["stats"] is None


def test_unknown_strategy_name_raises():
    hyp = sandbox.ResearchHypothesis(hypothesis_id="h7", description="x", strategy_name="Not A Real Strategy", symbol="SYN1")
    df = generate_synthetic_ohlcv(seed=1)
    with pytest.raises(ValueError, match="unknown strategy"):
        sandbox.run_hypothesis(hyp, {"SYN1": df}, fresh_config(), LOGGER)
