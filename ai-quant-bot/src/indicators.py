"""Quantitative indicator calculations: momentum, moving averages, RSI, ATR, volume, volatility."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_momentum(close: pd.Series, days: int) -> pd.Series:
    """Percent change over `days` trading days."""
    return close.pct_change(periods=days) * 100.0


def compute_sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Classic Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Where average loss is 0 (all gains), RSI is 100.
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder-style rolling mean of true range)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def compute_volume_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    """Current volume divided by its trailing rolling average."""
    avg_volume = volume.rolling(window=window, min_periods=window).mean()
    return volume / avg_volume


def compute_daily_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling standard deviation of daily returns (%), a proxy for daily volatility."""
    daily_returns = close.pct_change() * 100.0
    return daily_returns.rolling(window=window, min_periods=window).std()


def compute_all_indicators(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Return a copy of df with all indicator columns appended."""
    cfg = config["indicators"]
    out = df.copy()

    out["momentum_20d"] = compute_momentum(out["close"], cfg["momentum_short_days"])
    out["momentum_60d"] = compute_momentum(out["close"], cfg["momentum_long_days"])
    out["sma_50"] = compute_sma(out["close"], cfg["sma_short"])
    out["sma_200"] = compute_sma(out["close"], cfg["sma_long"])
    out["rsi_14"] = compute_rsi(out["close"], cfg["rsi_period"])
    out["atr_14"] = compute_atr(out["high"], out["low"], out["close"], cfg["atr_period"])
    out["volume_ratio_20d"] = compute_volume_ratio(out["volume"], cfg["volume_avg_period"])
    out["daily_volatility_pct"] = compute_daily_volatility(out["close"], cfg["volatility_window"])

    return out


def latest_snapshot(df_with_indicators: pd.DataFrame) -> dict[str, float]:
    """Extract the most recent row of indicator values as a plain dict of floats."""
    last = df_with_indicators.iloc[-1]
    fields = [
        "close",
        "high",
        "low",
        "volume",
        "momentum_20d",
        "momentum_60d",
        "sma_50",
        "sma_200",
        "rsi_14",
        "atr_14",
        "volume_ratio_20d",
        "daily_volatility_pct",
    ]
    snapshot = {}
    for field in fields:
        value = last.get(field)
        snapshot[field] = float(value) if pd.notna(value) else float("nan")
    return snapshot
