"""src/universe.py - configurable, screened symbol universe (AI Quant
Trading Platform sprint, deliverable A)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import universe

LOGGER = logging.getLogger("test")


def make_config(tmp_path, **overrides):
    config = {
        "tickers": ["SPY", "QQQ"],
        "data": {"journal_dir": str(tmp_path / "journal"), "raw_dir": str(tmp_path / "raw")},
    }
    config.update(overrides)
    return config


def write_bars(raw_dir: Path, symbol: str, price: float, volume: float, days: int = 20) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    for i in range(days):
        lines.append(f"2026-09-{i + 1:02d} 00:00:00-04:00,{price},{price},{price},{price},{volume}")
    (raw_dir / f"{symbol}_daily.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- backward compatibility: disabled by default ----------------------------


def test_resolve_universe_returns_tickers_unchanged_when_disabled(tmp_path):
    config = make_config(tmp_path, tickers=["SPY", "QQQ", "AAPL"])
    assert universe.resolve_universe(config, LOGGER) == ["SPY", "QQQ", "AAPL"]


def test_resolve_universe_returns_tickers_unchanged_when_universe_section_absent(tmp_path):
    config = make_config(tmp_path)
    assert "universe" not in config
    assert universe.resolve_universe(config, LOGGER) == config["tickers"]


# --- screening once enabled ---------------------------------------------------


def test_screening_excludes_a_symbol_with_no_cached_data_at_all(tmp_path):
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["AAPL"]})
    assert universe.resolve_universe(config, LOGGER) == []


def test_screening_passes_a_symbol_meeting_price_and_volume_floors(tmp_path):
    raw_dir = Path(tmp_path / "raw")
    write_bars(raw_dir, "AAPL", price=150.0, volume=1_000_000)
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["AAPL"], "min_price": 5.0, "min_avg_dollar_volume": 10_000_000})
    assert universe.resolve_universe(config, LOGGER) == ["AAPL"]


def test_screening_excludes_a_symbol_below_the_price_floor(tmp_path):
    raw_dir = Path(tmp_path / "raw")
    write_bars(raw_dir, "PENNY", price=1.0, volume=50_000_000)
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["PENNY"], "min_price": 5.0, "min_avg_dollar_volume": 1000})
    assert universe.resolve_universe(config, LOGGER) == []


def test_screening_excludes_a_symbol_below_the_liquidity_floor(tmp_path):
    raw_dir = Path(tmp_path / "raw")
    write_bars(raw_dir, "ILLIQUID", price=50.0, volume=10)
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["ILLIQUID"], "min_price": 5.0, "min_avg_dollar_volume": 10_000_000})
    assert universe.resolve_universe(config, LOGGER) == []


def test_screening_caps_at_max_symbols_deterministically(tmp_path):
    raw_dir = Path(tmp_path / "raw")
    for symbol in ["ZZZ", "AAA", "MMM"]:
        write_bars(raw_dir, symbol, price=50.0, volume=1_000_000)
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["ZZZ", "AAA", "MMM"], "min_price": 5.0, "min_avg_dollar_volume": 1000, "max_symbols": 2})
    assert universe.resolve_universe(config, LOGGER) == ["AAA", "MMM"]  # sorted, capped


def test_candidate_pool_falls_back_to_the_default_pool_when_omitted(tmp_path):
    config = make_config(tmp_path, universe={"enabled": True})
    # No cached data for any default-pool symbol -> all excluded, but must not crash:
    assert universe.resolve_universe(config, LOGGER) == []


# --- delisted-symbol safeguard -------------------------------------------------


def test_record_fetch_outcomes_tracks_consecutive_failures(tmp_path):
    config = make_config(tmp_path)
    universe.record_fetch_outcomes(config, ["AAPL", "MSFT"], failed_symbols=["AAPL"])
    universe.record_fetch_outcomes(config, ["AAPL", "MSFT"], failed_symbols=["AAPL"])
    state = universe._read_failure_state(config)
    assert state["AAPL"] == 2
    assert "MSFT" not in state  # a clean fetch is never recorded as a failure streak


def test_a_success_clears_a_prior_failure_streak(tmp_path):
    config = make_config(tmp_path)
    universe.record_fetch_outcomes(config, ["AAPL"], failed_symbols=["AAPL"])
    universe.record_fetch_outcomes(config, ["AAPL"], failed_symbols=[])
    state = universe._read_failure_state(config)
    assert "AAPL" not in state


def test_excluded_by_safeguard_only_lists_symbols_past_the_threshold(tmp_path):
    config = make_config(tmp_path, universe={"max_consecutive_failures_before_exclusion": 3})
    for _ in range(2):
        universe.record_fetch_outcomes(config, ["BARELY"], failed_symbols=["BARELY"])
    for _ in range(3):
        universe.record_fetch_outcomes(config, ["DELISTED"], failed_symbols=["DELISTED"])
    assert universe.excluded_by_safeguard(config) == ["DELISTED"]


def test_resolve_universe_excludes_a_safeguarded_symbol_even_if_it_would_otherwise_pass(tmp_path):
    raw_dir = Path(tmp_path / "raw")
    write_bars(raw_dir, "DELISTED", price=50.0, volume=1_000_000)
    config = make_config(tmp_path, universe={"enabled": True, "candidate_pool": ["DELISTED"], "min_price": 5.0, "min_avg_dollar_volume": 1000, "max_consecutive_failures_before_exclusion": 2})
    universe.record_fetch_outcomes(config, ["DELISTED"], failed_symbols=["DELISTED"])
    universe.record_fetch_outcomes(config, ["DELISTED"], failed_symbols=["DELISTED"])

    assert universe.resolve_universe(config, LOGGER) == []
