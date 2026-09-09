"""Fetch daily OHLCV data from yfinance and persist it to data/raw/<symbol>.csv."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from .utils import resolve_path


def fetch_symbol_history(symbol: str, config: dict[str, Any], logger: logging.Logger) -> pd.DataFrame:
    """Download daily history for one symbol and cache it locally.

    Raises on failure so callers can decide how to isolate errors (see utils.safe_run).
    """
    period = config["data"]["history_period"]
    interval = config["data"]["interval"]

    logger.info("Fetching %s (%s, %s)", symbol, period, interval)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)

    if df is None or df.empty:
        raise ValueError(f"yfinance returned no data for {symbol}")

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
    out_path = raw_dir / f"{symbol}.csv"
    df.to_csv(out_path)
    return out_path


def fetch_all(symbols: list[str], config: dict[str, Any], logger: logging.Logger) -> dict[str, pd.DataFrame]:
    """Fetch history for every symbol, skipping (and logging) any that fail."""
    from .utils import safe_run  # local import avoids a circular import at module load time

    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = safe_run(logger, symbol, lambda s=symbol: fetch_symbol_history(s, config, logger))
        if df is not None:
            results[symbol] = df
    return results
