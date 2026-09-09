"""Quantitative indicator calculations used across all strategies and scoring."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def compute_ema(close: pd.Series, window: int) -> pd.Series:
    return close.ewm(span=window, min_periods=window, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Classic Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
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


def compute_bollinger_bands(close: pd.Series, window: int = 20, std_multiplier: float = 2.0) -> pd.DataFrame:
    mid = compute_sma(close, window)
    std = close.rolling(window=window, min_periods=window).std()
    upper = mid + std_multiplier * std
    lower = mid - std_multiplier * std
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_std": std})


def compute_momentum(close: pd.Series, days: int) -> pd.Series:
    """Percent change over `days` trading days."""
    return close.pct_change(periods=days) * 100.0


def compute_relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Today's volume vs. the average of the PRIOR `window` days (today excluded).

    Excluding today avoids a volume spike diluting the very average it's being
    compared against - the same reasoning as compute_rolling_high's shift(1).
    """
    avg_volume = volume.shift(1).rolling(window=window, min_periods=window).mean()
    return volume / avg_volume


def compute_daily_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling standard deviation of daily returns (%)."""
    daily_returns = close.pct_change() * 100.0
    return daily_returns.rolling(window=window, min_periods=window).std()


def compute_rolling_high(high: pd.Series, window: int) -> pd.Series:
    """Prior N-day high, excluding today (used for breakout detection)."""
    return high.shift(1).rolling(window=window, min_periods=window).max()


def compute_all_indicators(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Return a copy of df with every indicator column appended."""
    cfg = config["indicators"]
    out = df.copy()

    for window in cfg["sma_windows"]:
        out[f"sma_{window}"] = compute_sma(out["close"], window)

    for window in cfg["ema_windows"]:
        out[f"ema_{window}"] = compute_ema(out["close"], window)

    out["rsi_14"] = compute_rsi(out["close"], cfg["rsi_period"])
    out["atr_14"] = compute_atr(out["high"], out["low"], out["close"], cfg["atr_period"])

    bb = compute_bollinger_bands(out["close"], cfg["bollinger_window"], cfg["bollinger_std_multiplier"])
    out = out.join(bb)

    for days in cfg["momentum_days"]:
        out[f"momentum_{days}d"] = compute_momentum(out["close"], days)

    out["return_1m"] = compute_momentum(out["close"], cfg["one_month_trading_days"])
    out["relative_volume"] = compute_relative_volume(out["volume"], cfg["relative_volume_window"])
    out["daily_volatility_pct"] = compute_daily_volatility(out["close"], cfg["volatility_window"])
    out["rolling_high_20"] = compute_rolling_high(out["high"], config["strategies"]["momentum_breakout"]["breakout_window"])

    return out


SNAPSHOT_FIELDS = [
    "close",
    "high",
    "low",
    "volume",
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_50",
    "ema_200",
    "rsi_14",
    "atr_14",
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "bb_std",
    "momentum_20d",
    "momentum_60d",
    "return_1m",
    "relative_volume",
    "daily_volatility_pct",
    "rolling_high_20",
]


def snapshot_from_row(row: pd.Series) -> dict[str, float]:
    """Extract one row of indicator values as a plain dict of floats.

    Used both for the latest bar (live runs) and for every historical bar
    (backtesting) - strategies only ever consume this shape, never a raw DataFrame.
    """
    snapshot: dict[str, float] = {}
    for field in SNAPSHOT_FIELDS:
        value = row.get(field)
        snapshot[field] = float(value) if pd.notna(value) else float("nan")
    return snapshot


def latest_snapshot(df_with_indicators: pd.DataFrame) -> dict[str, float]:
    """Extract the most recent row of indicator values as a plain dict of floats."""
    return snapshot_from_row(df_with_indicators.iloc[-1])
