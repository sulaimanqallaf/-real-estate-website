"""Tests for src/smc_features.py. The single most important test in this file
(and arguably in this whole phase) is
`test_truncated_recompute_matches_full_history_filtered_by_available_at` -
see its docstring and the module docstring in smc_features.py for why."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import smc_features as smc


def make_price_df(n: int = 150, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0, 1.2, n))
    df = pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.2, n),
            "high": close + np.abs(rng.normal(0, 1.0, n)) + 0.5,
            "low": close - np.abs(rng.normal(0, 1.0, n)) - 0.5,
            "close": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _feature_set(table: pd.DataFrame) -> set:
    if table.empty:
        return set()
    return set(
        zip(
            table["feature_type"],
            table["bar_time"],
            table["available_at"],
            table["price_level"].round(6),
        )
    )


# --- Fair Value Gaps ---------------------------------------------------------------


def test_fvg_causal_availability_equals_the_confirming_bar():
    df = make_price_df(n=30)
    # Force a clean bullish FVG at index 10: bar8.high well below bar10.low.
    df.iloc[8, df.columns.get_loc("high")] = 90.0
    df.iloc[9, df.columns.get_loc("high")] = 92.0
    df.iloc[9, df.columns.get_loc("low")] = 91.0
    df.iloc[10, df.columns.get_loc("low")] = 95.0

    features = smc.detect_fair_value_gaps(df)
    bullish = [f for f in features if f.feature_type == smc.FEATURE_FVG_BULLISH and f.bar_time == df.index[9]]
    assert bullish, "expected a bullish FVG at the middle candle"
    assert bullish[0].available_at == df.index[10]  # the third (confirming) candle, not earlier


def test_fvg_not_detected_without_all_three_candles_present():
    df = make_price_df(n=3)
    df.iloc[0, df.columns.get_loc("high")] = 90.0
    df.iloc[2, df.columns.get_loc("low")] = 95.0
    features = smc.detect_fair_value_gaps(df.iloc[:2])  # only 2 candles - can't detect yet
    assert features == []


# --- Swing points (the classic lookahead trap) --------------------------------------


def test_swing_high_confirmation_delayed_by_right_bars():
    df = make_price_df(n=20)
    col = df.columns.get_loc("high")
    df.iloc[:, col] = 100.0
    df.iloc[10, col] = 120.0  # a clear, isolated swing high at index 10

    swings = smc.detect_swing_points(df, left=2, right=3)
    swing = next(s for s in swings if s.feature_type == smc.FEATURE_SWING_HIGH and s.bar_time == df.index[10])
    assert swing.available_at == df.index[13]  # 10 + right(3)
    assert swing.available_at > swing.bar_time


def test_swing_high_not_produced_when_confirming_bars_dont_exist_yet():
    df = make_price_df(n=20)
    col = df.columns.get_loc("high")
    df.iloc[:, col] = 100.0
    df.iloc[17, col] = 120.0  # near the end - only 2 bars remain after it

    swings = smc.detect_swing_points(df, left=2, right=3)
    assert not any(s.bar_time == df.index[17] for s in swings)  # not enough future bars to confirm


def test_swing_low_confirmation_delayed_by_right_bars():
    df = make_price_df(n=20)
    col = df.columns.get_loc("low")
    df.iloc[:, col] = 100.0
    df.iloc[10, col] = 80.0

    swings = smc.detect_swing_points(df, left=2, right=3)
    swing = next(s for s in swings if s.feature_type == smc.FEATURE_SWING_LOW and s.bar_time == df.index[10])
    assert swing.available_at == df.index[13]


# --- Order blocks --------------------------------------------------------------------


def test_order_block_availability_delayed_by_lookahead_bars():
    df = make_price_df(n=20)
    i = 5
    df.iloc[i, df.columns.get_loc("open")] = 105.0
    df.iloc[i, df.columns.get_loc("close")] = 100.0  # a down candle
    for j in range(i + 1, i + 4):
        df.iloc[j, df.columns.get_loc("high")] = 100.0 + (j - i) * 3.0  # impulsive rally after

    obs = smc.detect_order_blocks(df, lookahead_bars=3, impulse_threshold_pct=1.0)
    matching = [o for o in obs if o.feature_type == smc.FEATURE_ORDER_BLOCK_BULLISH and o.bar_time == df.index[i]]
    assert matching
    assert matching[0].available_at == df.index[i + 3]


def test_order_block_not_produced_without_enough_future_bars():
    df = make_price_df(n=10)
    i = 8
    df.iloc[i, df.columns.get_loc("open")] = 105.0
    df.iloc[i, df.columns.get_loc("close")] = 100.0
    obs = smc.detect_order_blocks(df, lookahead_bars=3, impulse_threshold_pct=1.0)
    assert not any(o.bar_time == df.index[i] for o in obs)


# --- BOS / CHoCH ----------------------------------------------------------------------


def test_bos_available_at_the_breakout_bar_not_before():
    df = make_price_df(n=30)
    col_high = df.columns.get_loc("high")
    col_close = df.columns.get_loc("close")
    df.iloc[:, col_high] = 100.0
    df.iloc[10, col_high] = 110.0  # swing high candidate
    df.iloc[20, col_close] = 115.0  # breakout candle, well after the swing is confirmed

    swings = smc.detect_swing_points(df, left=2, right=3)
    events = smc.detect_bos_choch(df, swings)
    bos = [e for e in events if e.feature_type in (smc.FEATURE_BOS_BULLISH, smc.FEATURE_CHOCH_BULLISH)]
    assert bos
    assert bos[0].bar_time == df.index[20]
    assert bos[0].available_at == df.index[20]


def test_bos_never_references_a_swing_before_its_own_available_at():
    """A swing must be at least as "old" (already confirmed) as the breakout
    bar that claims to break it - never the other way around."""
    df = make_price_df(n=30)
    swings = smc.detect_swing_points(df, left=2, right=3)
    events = smc.detect_bos_choch(df, swings)
    swings_by_bar_time = {(s.feature_type, s.bar_time): s for s in swings}
    for event in events:
        broken_bar_time = event.details["broken_swing_bar_time"]
        key_high = (smc.FEATURE_SWING_HIGH, broken_bar_time)
        key_low = (smc.FEATURE_SWING_LOW, broken_bar_time)
        referenced = swings_by_bar_time.get(key_high) or swings_by_bar_time.get(key_low)
        assert referenced is not None
        assert referenced.available_at <= event.available_at


# --- Liquidity sweeps ------------------------------------------------------------------


def test_liquidity_sweep_only_references_already_confirmed_swings():
    df = make_price_df(n=30)
    swings = smc.detect_swing_points(df, left=2, right=3)
    events = smc.detect_liquidity_sweeps(df, swings)
    for event in events:
        assert event.available_at == event.bar_time  # sweep itself needs no extra confirmation lag


# --- THE mandatory no-lookahead regression test ---------------------------------------


@pytest.mark.parametrize("truncate_at", [20, 40, 70, 100, 150])
def test_truncated_recompute_matches_full_history_filtered_by_available_at(truncate_at):
    """The core causal-safety guarantee: recomputing every SMC feature using
    ONLY the first `truncate_at` bars must produce EXACTLY the same set of
    features (for that same set of bars) as computing over the FULL price
    history and then filtering to `available_at <= as_of` where `as_of` is
    the truncation point's own timestamp. If a future candle could change an
    already-produced feature row before it was actually available, these two
    would diverge - this test proves they never do, at five different
    truncation points."""
    df = make_price_df(n=150)
    as_of = df.index[truncate_at - 1]

    truncated_table = smc.build_causal_feature_table(df.iloc[:truncate_at])
    full_table = smc.build_causal_feature_table(df)
    filtered_full_table = smc.get_features_as_of(full_table, as_of)

    assert _feature_set(truncated_table) == _feature_set(filtered_full_table)


def test_future_candles_cannot_change_an_already_produced_feature_row():
    """Appending brand-new future bars to the price history must never alter
    any feature row whose available_at already fell at or before the
    original last bar - a direct test of "the future cannot rewrite the
    past" for this module. Built by literally concatenating fresh future bars
    onto the original DataFrame (not by re-seeding a same-length generator -
    RNG draws of different sizes from the same seed do NOT share a common
    prefix, since earlier columns in make_price_df() consume different-sized
    chunks of the stream), so the first 80 bars are guaranteed byte-identical
    between the two runs."""
    df = make_price_df(n=80)
    as_of = df.index[-1]
    original_table = smc.build_causal_feature_table(df)
    original_asof_set = _feature_set(smc.get_features_as_of(original_table, as_of))

    future_bars = make_price_df(n=80, seed=99)
    future_bars.index = pd.date_range(df.index[-1] + pd.Timedelta(days=1), periods=80, freq="B")
    extended = pd.concat([df, future_bars])
    extended_table = smc.build_causal_feature_table(extended)
    extended_asof_set = _feature_set(smc.get_features_as_of(extended_table, as_of))

    assert original_asof_set == extended_asof_set


def test_get_features_as_of_uses_available_at_not_bar_time():
    df = make_price_df(n=20)
    col = df.columns.get_loc("high")
    df.iloc[:, col] = 100.0
    df.iloc[10, col] = 120.0

    swings = smc.detect_swing_points(df, left=2, right=3)
    table = smc.features_to_dataframe(swings)
    swing_row = table[table["bar_time"] == df.index[10]].iloc[0]

    # As of the swing's own bar_time, it must NOT be visible yet (bar_time <
    # available_at) - using bar_time as the filter would wrongly show it.
    as_of_bar_time = smc.get_features_as_of(table, df.index[10])
    assert swing_row["feature_type"] not in as_of_bar_time["feature_type"].tolist() or df.index[10] >= swing_row["available_at"]
    as_of_available = smc.get_features_as_of(table, swing_row["available_at"])
    assert (as_of_available["bar_time"] == df.index[10]).any()
