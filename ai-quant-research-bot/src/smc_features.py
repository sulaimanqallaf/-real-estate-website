"""Smart Money Concepts (SMC) structural price features - Fair Value Gaps,
swing highs/lows, order blocks, Break of Structure (BOS), Change of Character
(CHoCH), and liquidity sweeps.

**Mandatory rule, enforced by construction, not just by convention: NO
LOOKAHEAD LEAKAGE.** Many public SMC implementations detect a swing high/low
by looking `right` bars into the future to confirm it, then attach the
resulting feature to the swing bar's OWN timestamp - which silently leaks
future information into a point-in-time feature row. Every function in this
module instead tracks two separate timestamps per feature:

- `bar_time`: the bar the structure structurally "belongs to" (e.g. the swing
  candle itself, the first candle of a Fair Value Gap).
- `available_at`: the EARLIEST bar at which the feature could actually have
  been computed using only bars up to and including that bar. For a feature
  that needs `N` confirming bars after `bar_time`, `available_at` is always
  `>= bar_time` by exactly that confirmation lag - never equal to `bar_time`
  when confirmation requires later bars.

Every detector here is also causal BY CONSTRUCTION: each one only ever reads
`df.iloc[j]` for `j <= i` when deciding whether bar `i` (or an earlier bar
that bar `i` finally confirms) produces a feature - never `df.iloc[j]` for
`j > i` while positioned at `i`. Concretely, this means a detector run on
`df.iloc[:T]` (bars 0..T-1 only) MUST produce, for every bar_time < T, exactly
the same features `build_causal_feature_table(df)` produces for that
bar_time - see `tests/test_smc_features.py`'s
`test_truncated_recompute_matches_full_history_filtered_by_available_at`,
which is exactly that comparison and is the single most important test in
this module.

`get_features_as_of()` is the enforcement point every consumer (the dataset
builder, any future model) is expected to use: it filters a feature table to
`available_at <= as_of`, which is the only way to safely ask "what did SMC
structure look like as of this bar" without smuggling in the future.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

FEATURE_FVG_BULLISH = "fvg_bullish"
FEATURE_FVG_BEARISH = "fvg_bearish"
FEATURE_SWING_HIGH = "swing_high"
FEATURE_SWING_LOW = "swing_low"
FEATURE_ORDER_BLOCK_BULLISH = "order_block_bullish"
FEATURE_ORDER_BLOCK_BEARISH = "order_block_bearish"
FEATURE_BOS_BULLISH = "bos_bullish"
FEATURE_BOS_BEARISH = "bos_bearish"
FEATURE_CHOCH_BULLISH = "choch_bullish"
FEATURE_CHOCH_BEARISH = "choch_bearish"
FEATURE_LIQUIDITY_SWEEP_HIGH = "liquidity_sweep_high"
FEATURE_LIQUIDITY_SWEEP_LOW = "liquidity_sweep_low"


@dataclass(frozen=True)
class SmcFeature:
    feature_type: str
    bar_time: pd.Timestamp
    available_at: pd.Timestamp
    price_level: float | None
    details: dict[str, Any] = field(default_factory=dict)


def detect_fair_value_gaps(df: pd.DataFrame) -> list[SmcFeature]:
    """3-candle Fair Value Gap: a bullish FVG is a gap between candle i-2's
    high and candle i's low (candle i-1 in between); a bearish FVG is the
    mirror image. This needs NO future bars beyond `i` itself to detect - the
    gap is fully knowable the moment candle `i` prints, so `available_at ==
    df.index[i]`, the same bar it's detected on (never earlier, never a
    fabricated later confirmation either - there's nothing left to confirm)."""
    features: list[SmcFeature] = []
    for i in range(2, len(df)):
        bar0 = df.iloc[i - 2]
        bar2 = df.iloc[i]
        t = df.index[i]

        if bar2["low"] > bar0["high"]:
            features.append(
                SmcFeature(
                    FEATURE_FVG_BULLISH,
                    bar_time=df.index[i - 1],
                    available_at=t,
                    price_level=(bar0["high"] + bar2["low"]) / 2,
                    details={"gap_bottom": float(bar0["high"]), "gap_top": float(bar2["low"])},
                )
            )
        if bar2["high"] < bar0["low"]:
            features.append(
                SmcFeature(
                    FEATURE_FVG_BEARISH,
                    bar_time=df.index[i - 1],
                    available_at=t,
                    price_level=(bar0["low"] + bar2["high"]) / 2,
                    details={"gap_bottom": float(bar2["high"]), "gap_top": float(bar0["low"])},
                )
            )
    return features


def detect_swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[SmcFeature]:
    """A swing high at bar `i` requires its high to be the strict max over
    `[i-left, i+right]`; a swing low, the strict min. Confirming this
    inherently needs `right` bars AFTER `i` - `available_at = df.index[i +
    right]`. The loop bound `len(df) - right` means a swing near the end of
    whatever slice of `df` is passed in is simply never produced until enough
    future bars exist in that slice - this is what makes truncation-safety
    automatic rather than a separate rule to remember."""
    features: list[SmcFeature] = []
    n = len(df)
    for i in range(left, n - right):
        window_high = df["high"].iloc[i - left : i + right + 1]
        window_low = df["low"].iloc[i - left : i + right + 1]
        high_i = df["high"].iloc[i]
        low_i = df["low"].iloc[i]

        if high_i == window_high.max() and (window_high == high_i).sum() == 1:
            features.append(
                SmcFeature(
                    FEATURE_SWING_HIGH,
                    bar_time=df.index[i],
                    available_at=df.index[i + right],
                    price_level=float(high_i),
                    details={"left": left, "right": right},
                )
            )
        if low_i == window_low.min() and (window_low == low_i).sum() == 1:
            features.append(
                SmcFeature(
                    FEATURE_SWING_LOW,
                    bar_time=df.index[i],
                    available_at=df.index[i + right],
                    price_level=float(low_i),
                    details={"left": left, "right": right},
                )
            )
    return features


def detect_order_blocks(
    df: pd.DataFrame, lookahead_bars: int = 3, impulse_threshold_pct: float = 1.5
) -> list[SmcFeature]:
    """Simplified V1 order block: the last down-close candle before an
    impulsive rally (bullish OB) or the last up-close candle before an
    impulsive selloff (bearish OB), where "impulsive" means the confirming
    move over the next `lookahead_bars` bars clears `impulse_threshold_pct`.
    This is a documented simplification of the full ICT order-block
    definition (no structure-break requirement, no mitigation tracking) -
    deliberately kept simple so its causal contract is easy to verify:
    `available_at = df.index[i + lookahead_bars]`, and the loop bound `len(df)
    - lookahead_bars` again makes truncation-safety automatic."""
    features: list[SmcFeature] = []
    n = len(df)
    for i in range(0, n - lookahead_bars):
        bar = df.iloc[i]
        confirm_window = df.iloc[i + 1 : i + 1 + lookahead_bars]
        if confirm_window.empty or bar["close"] == 0:
            continue
        available_at = df.index[i + lookahead_bars]

        move_up_pct = (confirm_window["high"].max() - bar["close"]) / bar["close"] * 100.0
        move_down_pct = (bar["close"] - confirm_window["low"].min()) / bar["close"] * 100.0

        if bar["close"] < bar["open"] and move_up_pct >= impulse_threshold_pct:
            features.append(
                SmcFeature(
                    FEATURE_ORDER_BLOCK_BULLISH,
                    bar_time=df.index[i],
                    available_at=available_at,
                    price_level=float(bar["low"]),
                    details={"confirming_move_pct": round(float(move_up_pct), 2), "lookahead_bars": lookahead_bars},
                )
            )
        if bar["close"] > bar["open"] and move_down_pct >= impulse_threshold_pct:
            features.append(
                SmcFeature(
                    FEATURE_ORDER_BLOCK_BEARISH,
                    bar_time=df.index[i],
                    available_at=available_at,
                    price_level=float(bar["high"]),
                    details={"confirming_move_pct": round(float(move_down_pct), 2), "lookahead_bars": lookahead_bars},
                )
            )
    return features


def detect_bos_choch(df: pd.DataFrame, swings: list[SmcFeature]) -> list[SmcFeature]:
    """Break of Structure (BOS): close breaks beyond the most recent
    ALREADY-CONFIRMED opposite-extreme swing, continuing the prevailing
    trend. Change of Character (CHoCH): the first such break in the opposite
    direction of the prevailing trend - a potential reversal signal.

    Causal by construction: at bar `i` (time `t = df.index[i]`), only swings
    with `available_at <= t` AND `bar_time < t` are eligible to be broken -
    a swing this same detector hasn't "seen" yet (per its own available_at)
    cannot be referenced, and a swing can't be broken by its own bar. Because
    every referenced swing already satisfies `available_at <= t`, the
    resulting BOS/CHoCH event's own `available_at` is simply `t` - the
    breakout bar itself needs no further confirmation lag.
    """
    swing_highs = sorted([s for s in swings if s.feature_type == FEATURE_SWING_HIGH], key=lambda s: s.bar_time)
    swing_lows = sorted([s for s in swings if s.feature_type == FEATURE_SWING_LOW], key=lambda s: s.bar_time)

    events: list[SmcFeature] = []
    trend: str | None = None
    last_broken_high_bar_time = None
    last_broken_low_bar_time = None

    for i in range(len(df)):
        t = df.index[i]
        close = df["close"].iloc[i]

        eligible_highs = [s for s in swing_highs if s.available_at <= t and s.bar_time < t]
        if eligible_highs:
            most_recent_high = max(eligible_highs, key=lambda s: s.bar_time)
            if close > most_recent_high.price_level and most_recent_high.bar_time != last_broken_high_bar_time:
                is_choch = trend == "down"
                events.append(
                    SmcFeature(
                        FEATURE_CHOCH_BULLISH if is_choch else FEATURE_BOS_BULLISH,
                        bar_time=t,
                        available_at=t,
                        price_level=most_recent_high.price_level,
                        details={"broken_swing_bar_time": most_recent_high.bar_time},
                    )
                )
                last_broken_high_bar_time = most_recent_high.bar_time
                trend = "up"

        eligible_lows = [s for s in swing_lows if s.available_at <= t and s.bar_time < t]
        if eligible_lows:
            most_recent_low = max(eligible_lows, key=lambda s: s.bar_time)
            if close < most_recent_low.price_level and most_recent_low.bar_time != last_broken_low_bar_time:
                is_choch = trend == "up"
                events.append(
                    SmcFeature(
                        FEATURE_CHOCH_BEARISH if is_choch else FEATURE_BOS_BEARISH,
                        bar_time=t,
                        available_at=t,
                        price_level=most_recent_low.price_level,
                        details={"broken_swing_bar_time": most_recent_low.bar_time},
                    )
                )
                last_broken_low_bar_time = most_recent_low.bar_time
                trend = "down"

    return events


def detect_liquidity_sweeps(df: pd.DataFrame, swings: list[SmcFeature]) -> list[SmcFeature]:
    """A liquidity sweep: the current bar's wick pierces an already-confirmed
    swing extreme, but the bar CLOSES back on the other side of it (a stop
    hunt that reverses within the same bar). Causal for the same reason as
    `detect_bos_choch`: only swings already `available_at <= t` are eligible,
    so the sweep event's own `available_at` is simply `t`."""
    swing_highs = [s for s in swings if s.feature_type == FEATURE_SWING_HIGH]
    swing_lows = [s for s in swings if s.feature_type == FEATURE_SWING_LOW]

    events: list[SmcFeature] = []
    for i in range(len(df)):
        t = df.index[i]
        bar = df.iloc[i]

        eligible_highs = [s for s in swing_highs if s.available_at <= t and s.bar_time < t]
        if eligible_highs:
            most_recent_high = max(eligible_highs, key=lambda s: s.bar_time)
            if bar["high"] > most_recent_high.price_level and bar["close"] < most_recent_high.price_level:
                events.append(
                    SmcFeature(
                        FEATURE_LIQUIDITY_SWEEP_HIGH,
                        bar_time=t,
                        available_at=t,
                        price_level=most_recent_high.price_level,
                        details={"swept_swing_bar_time": most_recent_high.bar_time},
                    )
                )

        eligible_lows = [s for s in swing_lows if s.available_at <= t and s.bar_time < t]
        if eligible_lows:
            most_recent_low = max(eligible_lows, key=lambda s: s.bar_time)
            if bar["low"] < most_recent_low.price_level and bar["close"] > most_recent_low.price_level:
                events.append(
                    SmcFeature(
                        FEATURE_LIQUIDITY_SWEEP_LOW,
                        bar_time=t,
                        available_at=t,
                        price_level=most_recent_low.price_level,
                        details={"swept_swing_bar_time": most_recent_low.bar_time},
                    )
                )

    return events


def compute_all_smc_features(df: pd.DataFrame, config: dict[str, Any] | None = None) -> list[SmcFeature]:
    """Run every detector in this module over `df` and return one combined,
    unsorted list of `SmcFeature`. This is the single function whose output
    the "no lookahead" regression test compares between a truncated slice and
    a filtered full run - see the module docstring."""
    cfg = (config or {}).get("smc", {})
    left = cfg.get("swing_left_bars", 2)
    right = cfg.get("swing_right_bars", 2)
    ob_lookahead = cfg.get("order_block_lookahead_bars", 3)
    ob_threshold = cfg.get("order_block_impulse_threshold_pct", 1.5)

    swings = detect_swing_points(df, left=left, right=right)
    features = list(swings)
    features.extend(detect_fair_value_gaps(df))
    features.extend(detect_order_blocks(df, lookahead_bars=ob_lookahead, impulse_threshold_pct=ob_threshold))
    features.extend(detect_bos_choch(df, swings))
    features.extend(detect_liquidity_sweeps(df, swings))
    return features


def features_to_dataframe(features: list[SmcFeature]) -> pd.DataFrame:
    if not features:
        return pd.DataFrame(columns=["feature_type", "bar_time", "available_at", "price_level", "details"])
    return pd.DataFrame(
        [
            {
                "feature_type": f.feature_type,
                "bar_time": f.bar_time,
                "available_at": f.available_at,
                "price_level": f.price_level,
                "details": f.details,
            }
            for f in features
        ]
    )


def build_causal_feature_table(df: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Convenience wrapper: `compute_all_smc_features()` -> a flat DataFrame,
    the shape `dataset_builder.py` consumes."""
    return features_to_dataframe(compute_all_smc_features(df, config))


def get_features_as_of(feature_table: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """THE enforcement point: only features already knowable at `as_of`
    (`available_at <= as_of`) are returned. Every consumer that wants
    "what did SMC structure look like at bar T" must go through this, never
    filter on `bar_time` alone - a swing's `bar_time` can be well before its
    `available_at`, and using `bar_time` here would reintroduce exactly the
    lookahead leakage this module exists to prevent."""
    if feature_table.empty:
        return feature_table
    return feature_table[feature_table["available_at"] <= as_of]
