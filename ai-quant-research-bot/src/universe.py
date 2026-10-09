"""Configurable, screened symbol universe (AI Quant Trading Platform
sprint, deliverable A: "configurable initial 100-symbol universe,
expandable to 500-1,000+ instruments... US stock and ETF screening...
delisted-symbol safeguards").

**Backward-compatible by construction.** `universe.enabled` defaults to
`false`, in which case `resolve_universe()` returns `config["tickers"]`
completely unchanged - merging/importing this module changes nothing
about any existing deployment's behavior until the user explicitly
opts in via config. This is deliberate: a 15-symbol universe silently
becoming a 100-symbol one would 6x a real deployment's yfinance call
volume and completely change which candidates get traded, without the
user ever asking for that.

**Screening never fetches anything itself** - that stays
`data_collector.py`'s job, unchanged. This module only ever reads
already-cached daily bars (`data/raw/*.csv`) to decide whether a
candidate from `universe.candidate_pool` passes the price/liquidity
filters. A candidate that has never been fetched yet is EXCLUDED from
a screened universe (not included with a guessed price) until it has
real cached data - the same "unknown -> excluded, never fabricated"
convention as every other data source in this codebase. The practical
workflow: add a symbol to `candidate_pool`, let one `main.py` run fetch
it (that symbol just won't pass screening that first time if it has no
history yet), and it becomes screenable from the next run onward.

**Delisted-symbol safeguard.** A symbol that fails to fetch
`max_consecutive_failures_before_exclusion` times in a row (tracked in
a small JSON file colocated with `data.journal_dir` - same convention
as every other piece of durable state in this codebase, see
`execution/circuit_breaker.py`'s reconciliation-state docstring for why
that convention exists) is excluded from the NEXT resolved universe -
not retried forever on every run. This is a soft, observable, and
reversible exclusion: it's visible in the state file and in the log,
and a human can re-include a symbol at any time (fix the underlying
issue and it naturally clears on its next successful fetch, or delete
its entry from the state file to force an immediate retry).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import resolve_path

_FAILURE_STATE_FILENAME = "universe_fetch_failures.json"

# A curated, static starting pool for `universe.candidate_pool` when a
# deployment wants to opt into screening but hasn't written its own list
# yet - liquid, well-known US equities/ETFs spanning sectors. This is
# NOT a live screener result (no market-data-vendor license is wired up
# for that - see docs/platform/BLOCKERS.md item 2) - it's a hand-curated
# starting point the user can freely edit/replace in config.
DEFAULT_CANDIDATE_POOL: list[str] = sorted(set([
    # Broad market / factor ETFs
    "SPY", "VOO", "QQQ", "DIA", "IWM", "VTI", "VUG", "VTV", "SCHD", "RSP",
    # Sector ETFs
    "VGT", "SMH", "SOXX", "XLF", "XLE", "XLV", "XLI", "XLK", "XLY", "XLP",
    "XLU", "XLB", "XLC", "XLRE", "KRE", "XBI", "IYR",
    # Commodities / macro ETFs
    "GLD", "SLV", "USO", "UNG", "TLT", "IEF", "HYG", "LQD",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO",
    "ORCL", "ADBE", "CRM", "CSCO", "AMD", "QCOM", "INTC", "IBM", "NOW", "INTU",
    # Communication / consumer discretionary
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "HD", "MCD", "NKE", "SBUX",
    "LOW", "BKNG", "TJX", "ABNB",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V", "MA",
    "PYPL",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD",
    # Industrials / energy
    "XOM", "CVX", "CAT", "DE", "HON", "UPS", "BA", "GE", "LMT", "RTX", "UNP",
    "COP", "SLB",
    # Consumer staples
    "PG", "KO", "PEP", "WMT", "COST", "MDLZ", "CL",
]))


def _failure_state_path(config: dict[str, Any]) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / _FAILURE_STATE_FILENAME
    return resolve_path("data/journal") / _FAILURE_STATE_FILENAME


def _read_failure_state(config: dict[str, Any]) -> dict[str, int]:
    path = _failure_state_path(config)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_failure_state(config: dict[str, Any], state: dict[str, int]) -> None:
    path = _failure_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def record_fetch_outcomes(config: dict[str, Any], attempted_symbols: list[str], failed_symbols: list[str]) -> None:
    """Called once per `main.py` run, right after
    `data_collector.fetch_all_price_history()` - updates each attempted
    symbol's consecutive-failure count (reset to 0 on success,
    incremented on failure). `attempted_symbols` must be the FULL list
    passed to the fetch call, not just the failures - a symbol that
    isn't in `failed_symbols` is assumed to have succeeded."""
    state = _read_failure_state(config)
    for symbol in attempted_symbols:
        if symbol in failed_symbols:
            state[symbol] = state.get(symbol, 0) + 1
        else:
            state.pop(symbol, None)  # a clean fetch clears any prior streak
    _write_failure_state(config, state)


def excluded_by_safeguard(config: dict[str, Any]) -> list[str]:
    """Symbols currently excluded by the delisted-symbol safeguard -
    read-only, for display in the health/status or dashboard scanner
    panel."""
    threshold = config.get("universe", {}).get("max_consecutive_failures_before_exclusion", 5)
    state = _read_failure_state(config)
    return sorted(symbol for symbol, count in state.items() if count >= threshold)


def _latest_price_and_dollar_volume(symbol: str, config: dict[str, Any], lookback_days: int = 20) -> tuple[float, float] | None:
    """Best-effort read of a symbol's latest cached close price and
    trailing average dollar volume, from `data/raw/{symbol}_daily.csv`
    (the same cache `data_collector.py` writes). Returns `None`
    (never a fabricated 0.0) if the file doesn't exist, is empty, or
    can't be parsed - a symbol with no readable cache is simply
    unscreenable, not "fails the filter with a 0.0 price."""
    raw_dir = config.get("data", {}).get("raw_dir")
    if not raw_dir:
        return None
    raw_path = resolve_path(raw_dir) / f"{symbol}_daily.csv"
    if not raw_path.exists():
        return None
    try:
        df = pd.read_csv(raw_path, index_col=0)
        if df.empty or "close" not in df.columns or "volume" not in df.columns:
            return None
        tail = df.tail(lookback_days)
        latest_close = float(tail["close"].iloc[-1])
        avg_dollar_volume = float((tail["close"] * tail["volume"]).mean())
        if pd.isna(latest_close) or pd.isna(avg_dollar_volume):
            return None
        return latest_close, avg_dollar_volume
    except Exception:  # noqa: BLE001 - screening must never crash the run over one unreadable cache file
        return None


def resolve_universe(config: dict[str, Any], logger: logging.Logger) -> list[str]:
    """The one entry point `main.py` calls in place of reading
    `config["tickers"]` directly. Returns `config["tickers"]` UNCHANGED
    when `universe.enabled` is falsy/missing (the default) - see module
    docstring. When enabled, screens `universe.candidate_pool` (falling
    back to `DEFAULT_CANDIDATE_POOL` if that key is absent) by cached
    price/liquidity, drops anything excluded by the delisted-symbol
    safeguard, and caps the result at `universe.max_symbols` -
    deterministically (sorted alphabetically, so the same cache state
    always produces the same universe)."""
    universe_cfg = config.get("universe", {})
    if not universe_cfg.get("enabled", False):
        return config["tickers"]

    pool = universe_cfg.get("candidate_pool") or DEFAULT_CANDIDATE_POOL
    min_price = universe_cfg.get("min_price", 5.0)
    min_avg_dollar_volume = universe_cfg.get("min_avg_dollar_volume", 10_000_000)
    max_symbols = universe_cfg.get("max_symbols", 100)

    excluded = set(excluded_by_safeguard(config))
    passed: list[str] = []
    for symbol in pool:
        if symbol in excluded:
            continue
        stats = _latest_price_and_dollar_volume(symbol, config)
        if stats is None:
            continue  # no cached data yet - unknown, excluded conservatively, not fabricated as passing
        price, dollar_volume = stats
        if price < min_price or dollar_volume < min_avg_dollar_volume:
            continue
        passed.append(symbol)

    final = sorted(passed)[:max_symbols]
    if excluded:
        logger.info("Universe: %d symbol(s) excluded by the delisted-symbol safeguard: %s", len(excluded), ", ".join(sorted(excluded)))
    logger.info("Universe: %d/%d candidates passed screening (capped at %d).", len(final), len(pool), max_symbols)
    return final
