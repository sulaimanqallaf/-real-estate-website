"""Unit tests for indicators.py using small, deterministic synthetic series."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import indicators


def test_sma_matches_plain_mean():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    sma = indicators.compute_sma(close, window=3)
    assert sma.iloc[2] == (1.0 + 2.0 + 3.0) / 3
    assert sma.iloc[4] == (3.0 + 4.0 + 5.0) / 3
    assert pd.isna(sma.iloc[0])  # not enough history yet


def test_ema_reacts_faster_than_sma_after_a_price_jump():
    # 30 bars at 10.0, then a jump to 20.0. A few bars after the jump, a window-10
    # EMA (which weights recent bars more heavily) should sit above the equally-
    # windowed SMA (a plain average still dragged down by pre-jump bars).
    close = pd.Series([10.0] * 30 + [20.0] * 5)
    ema = indicators.compute_ema(close, window=10)
    sma = indicators.compute_sma(close, window=10)
    assert ema.iloc[-1] > sma.iloc[-1]


def test_rsi_is_100_when_only_gains():
    close = pd.Series(np.arange(1.0, 30.0))  # strictly increasing
    rsi = indicators.compute_rsi(close, period=14)
    assert rsi.iloc[-1] == 100.0


def test_rsi_is_0_when_only_losses():
    close = pd.Series(np.arange(30.0, 1.0, -1.0))  # strictly decreasing
    rsi = indicators.compute_rsi(close, period=14)
    assert rsi.iloc[-1] == 0.0


def test_atr_on_flat_series_is_zero():
    n = 20
    close = pd.Series([100.0] * n)
    high = pd.Series([100.0] * n)
    low = pd.Series([100.0] * n)
    atr = indicators.compute_atr(high, low, close, period=14)
    assert atr.iloc[-1] == 0.0


def test_bollinger_bands_bracket_price_within_std_multiplier():
    close = pd.Series(np.random.default_rng(1).normal(100, 2, 60))
    bb = indicators.compute_bollinger_bands(close, window=20, std_multiplier=2.0)
    last = bb.iloc[-1]
    assert last["bb_upper"] > last["bb_mid"] > last["bb_lower"]
    assert last["bb_upper"] == last["bb_mid"] + 2.0 * last["bb_std"]
    assert last["bb_lower"] == last["bb_mid"] - 2.0 * last["bb_std"]


def test_momentum_percent_change():
    close = pd.Series([100.0] * 20 + [110.0])
    momentum = indicators.compute_momentum(close, days=20)
    assert round(momentum.iloc[-1], 4) == 10.0  # +10% over 20 bars


def test_relative_volume_above_and_below_average():
    volume = pd.Series([1_000_000.0] * 20 + [2_000_000.0])
    rel_vol = indicators.compute_relative_volume(volume, window=20)
    assert round(rel_vol.iloc[-1], 4) == 2.0


def test_rolling_high_excludes_current_bar():
    high = pd.Series([10.0, 20.0, 15.0, 100.0])  # today's 100 must not count for today's own rolling high
    rolling_high = indicators.compute_rolling_high(high, window=3)
    assert rolling_high.iloc[3] == 20.0  # max of the prior 3 bars (10, 20, 15), not today's 100


def test_snapshot_from_row_returns_nan_for_missing_fields():
    row = pd.Series({"close": 100.0})
    snapshot = indicators.snapshot_from_row(row)
    assert snapshot["close"] == 100.0
    assert snapshot["sma_200"] != snapshot["sma_200"]  # NaN
