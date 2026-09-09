"""Point-in-time ML dataset builder. No model is trained here - this only
ever produces a Parquet-backed feature store for a future ML step.

Rows are keyed by (timestamp, ticker). The critical structural rule (Part I -
"one of the most important requirements"): every FEATURE column is computed
using ONLY information knowable at that row's timestamp - technical
indicators via truncating `price_df` to bars up to and including `i`
(`price_df.iloc[:i+1]`), SMC structure via `smc_features.get_features_as_of()`
(`available_at <= t`), and institutional/insider/macro context via
already-point-in-time-filtered inputs the caller supplies. Every LABEL column
(`forward_5d_return`/`forward_10d_return`/`forward_20d_return`) is the ONE
place this module is allowed - and required - to look forward, because a
label is not a feature: a model trained on these rows only ever sees features
it could have seen "live," scored against outcomes that hadn't happened yet.

`FEATURE_COLUMNS` and `LABEL_COLUMNS` are kept as explicit, separate lists so
this separation is structural, not just a comment - see
`tests/test_dataset_builder.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from . import indicators, smc_features
from .utils import resolve_path

KEY_COLUMNS = ["timestamp", "ticker"]

FEATURE_COLUMNS = [
    "close",
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_50",
    "ema_200",
    "rsi_14",
    "atr_14",
    "momentum_20d",
    "momentum_60d",
    "relative_volume",
    "daily_volatility_pct",
    "smc_fvg_bullish_count",
    "smc_fvg_bearish_count",
    "smc_swing_high_count",
    "smc_swing_low_count",
    "smc_bos_bullish_count",
    "smc_bos_bearish_count",
    "smc_choch_bullish_count",
    "smc_choch_bearish_count",
    "institutional_score",
    "insider_score",
    "options_flow_score",
    "big_money_composite_score",
    "big_money_data_quality_score",
    "macro_fed_funds_rate",
    "macro_10y_2y_spread",
]

LABEL_COLUMNS = ["forward_5d_return", "forward_10d_return", "forward_20d_return"]

FORWARD_RETURN_HORIZONS = (5, 10, 20)


def compute_forward_returns(price_df: pd.DataFrame, i: int, horizons: tuple[int, ...] = FORWARD_RETURN_HORIZONS) -> dict[str, float | None]:
    """LABELS ONLY. This is the single function in this module allowed to
    read `price_df` AHEAD of row `i` - never call it while building a feature
    column. Returns `None` (never a fabricated value) for a horizon that
    hasn't happened yet within `price_df`."""
    close_t = price_df["close"].iloc[i]
    out: dict[str, float | None] = {}
    for h in horizons:
        j = i + h
        if j < len(price_df) and close_t:
            out[f"forward_{h}d_return"] = float((price_df["close"].iloc[j] - close_t) / close_t)
        else:
            out[f"forward_{h}d_return"] = None
    return out


def build_feature_row(
    ticker: str,
    price_df: pd.DataFrame,
    i: int,
    config: dict[str, Any],
    smc_table: pd.DataFrame | None = None,
    institutional_facts: dict[str, Any] | None = None,
    insider_features: dict[str, Any] | None = None,
    macro_snapshot: dict[str, Any] | None = None,
    big_money_score: Any | None = None,
) -> dict[str, Any]:
    """FEATURES ONLY for row `i`. Every value here is computed from
    `price_df.iloc[:i+1]` (bars up to and including `i`) or from inputs the
    caller has already point-in-time-filtered - never from `price_df.iloc[j]`
    for `j > i`. This holds regardless of how much history `price_df` (or
    `smc_table`) actually contains beyond row `i` - extra future rows never
    change this row's feature values, which is exactly what
    `tests/test_dataset_builder.py`'s point-in-time tests verify."""
    t = price_df.index[i]
    truncated = price_df.iloc[: i + 1]

    ind_df = indicators.compute_all_indicators(truncated, config)
    snapshot = indicators.latest_snapshot(ind_df)

    smc_counts: dict[str, int] = {}
    if smc_table is not None and not smc_table.empty:
        as_of_smc = smc_features.get_features_as_of(smc_table, t)
        if not as_of_smc.empty:
            smc_counts = as_of_smc["feature_type"].value_counts().to_dict()

    row = {
        "timestamp": t,
        "ticker": ticker,
        "close": snapshot.get("close"),
        "sma_20": snapshot.get("sma_20"),
        "sma_50": snapshot.get("sma_50"),
        "sma_200": snapshot.get("sma_200"),
        "ema_50": snapshot.get("ema_50"),
        "ema_200": snapshot.get("ema_200"),
        "rsi_14": snapshot.get("rsi_14"),
        "atr_14": snapshot.get("atr_14"),
        "momentum_20d": snapshot.get("momentum_20d"),
        "momentum_60d": snapshot.get("momentum_60d"),
        "relative_volume": snapshot.get("relative_volume"),
        "daily_volatility_pct": snapshot.get("daily_volatility_pct"),
        "smc_fvg_bullish_count": smc_counts.get(smc_features.FEATURE_FVG_BULLISH, 0),
        "smc_fvg_bearish_count": smc_counts.get(smc_features.FEATURE_FVG_BEARISH, 0),
        "smc_swing_high_count": smc_counts.get(smc_features.FEATURE_SWING_HIGH, 0),
        "smc_swing_low_count": smc_counts.get(smc_features.FEATURE_SWING_LOW, 0),
        "smc_bos_bullish_count": smc_counts.get(smc_features.FEATURE_BOS_BULLISH, 0),
        "smc_bos_bearish_count": smc_counts.get(smc_features.FEATURE_BOS_BEARISH, 0),
        "smc_choch_bullish_count": smc_counts.get(smc_features.FEATURE_CHOCH_BULLISH, 0),
        "smc_choch_bearish_count": smc_counts.get(smc_features.FEATURE_CHOCH_BEARISH, 0),
        "institutional_score": (big_money_score.components.get("institutional_accumulation_score") if big_money_score else None),
        "insider_score": (big_money_score.components.get("insider_score") if big_money_score else None),
        "options_flow_score": (big_money_score.components.get("options_flow_score") if big_money_score else None),
        "big_money_composite_score": (big_money_score.composite_score if big_money_score else None),
        "big_money_data_quality_score": (big_money_score.data_quality_score if big_money_score else None),
        "macro_fed_funds_rate": (macro_snapshot or {}).get("fed_funds_rate"),
        "macro_10y_2y_spread": (macro_snapshot or {}).get("yield_curve_spread"),
    }
    return row


def build_dataset_rows(
    ticker: str,
    price_df: pd.DataFrame,
    config: dict[str, Any],
    institutional_facts: dict[str, Any] | None = None,
    insider_features: dict[str, Any] | None = None,
    macro_snapshot: dict[str, Any] | None = None,
    big_money_score: Any | None = None,
    min_history_bars: int = 200,
) -> pd.DataFrame:
    """One row per bar from `min_history_bars` onward - consistent with the
    rest of this codebase already requiring enough history for a trustworthy
    SMA200 (see `config.data.history_period`); this isn't a new rule, just
    applied here too. Computes the SMC feature table ONCE over the full
    `price_df` for efficiency and relies on `get_features_as_of()`'s
    `available_at` filter (proven equivalent to a per-row truncated
    recompute - see `tests/test_smc_features.py`) rather than recomputing SMC
    structure from scratch for every row.

    Labels are `None` for the most recent bars where a horizon hasn't
    happened yet within `price_df` - never fabricated; a caller re-running
    this once those future bars exist will get the label filled in then."""
    smc_table = smc_features.build_causal_feature_table(price_df, config)

    rows = []
    for i in range(min_history_bars, len(price_df)):
        row = build_feature_row(
            ticker,
            price_df,
            i,
            config,
            smc_table=smc_table,
            institutional_facts=institutional_facts,
            insider_features=insider_features,
            macro_snapshot=macro_snapshot,
            big_money_score=big_money_score,
        )
        row.update(compute_forward_returns(price_df, i))
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=KEY_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS)

    df = pd.DataFrame(rows)
    return df[KEY_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS]


def save_dataset(df: pd.DataFrame, ticker: str, config: dict[str, Any], report_date: str) -> Path:
    """Parquet output under `config.dataset.output_dir` (default
    `data/processed/ml/`), one file per ticker per run date."""
    out_dir = resolve_path(config.get("dataset", {}).get("output_dir", "data/processed/ml"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ticker}_{report_date}.parquet"
    df.to_parquet(path, index=False)
    return path
