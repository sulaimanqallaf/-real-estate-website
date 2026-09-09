"""Fetch daily OHLCV and (best-effort) options chain data via yfinance.

Version 1 uses yfinance for both price and options data. Options IV data on free
Yahoo Finance is inconsistent (missing/zero IV on illiquid strikes, no guarantee of
data for every ticker/expiration). The options fetch is therefore isolated from the
price fetch: a ticker's price history load never fails because its options chain was
unavailable, and options_skew.py is written to degrade to "Data Unavailable" rather
than raising when this happens.

To swap in a paid provider later (Polygon, Tradier, ORATS, Intrinio, CBOE LiveVol):
implement a fetch_option_chain_snapshot(symbol, config) with the same return shape
used here (see options_skew.OptionChainSnapshot) and point options_skew.py at it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from .utils import resolve_path


def fetch_symbol_history(symbol: str, config: dict[str, Any], logger: logging.Logger) -> pd.DataFrame:
    """Download daily history for one symbol and cache it locally."""
    period = config["data"]["history_period"]
    interval = config["data"]["interval"]

    logger.info("Fetching daily history for %s (%s, %s)", symbol, period, interval)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)

    if df is None or df.empty:
        raise ValueError(f"yfinance returned no price data for {symbol}")

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df.index.name = "date"
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])

    _save_raw_csv(symbol, df, config)
    return df


def _save_raw_csv(symbol: str, df: pd.DataFrame, config: dict[str, Any]) -> Path:
    raw_dir = resolve_path(config["data"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{symbol}_daily.csv"
    df.to_csv(out_path)
    return out_path


def fetch_all_price_history(
    symbols: list[str], config: dict[str, Any], logger: logging.Logger
) -> dict[str, pd.DataFrame]:
    """Fetch daily history for every symbol, skipping (and logging) any that fail."""
    from .utils import safe_run

    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = safe_run(logger, symbol, lambda s=symbol: fetch_symbol_history(s, config, logger))
        if df is not None:
            results[symbol] = df
    return results


def fetch_raw_option_chain(symbol: str, expiration: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (calls, puts) DataFrames for one expiration. Raises on any failure."""
    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiration)
    return chain.calls, chain.puts


def list_expirations(symbol: str) -> list[str]:
    """Return available option expiration date strings for a symbol. Raises on failure."""
    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No option expirations available for {symbol}")
    return list(expirations)
