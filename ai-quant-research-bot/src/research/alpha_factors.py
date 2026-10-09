"""A small, lightweight library of Qlib Alpha158-family technical
factors (AI Quant Trading Platform sprint, Phase 2: "a few publicly-
documented Alpha158-family formulas as small native functions").

**No Qlib/`pyqlib` code is imported, vendored, or copied anywhere in
this module.** `docs/platform/OSS_INTEGRATION_AUDIT.md`'s verdict on
Qlib was CONCEPT, not a dependency. What's implemented here are small,
independently-written pandas functions for a handful of the standard
technical-factor FORMULAS that Qlib's Alpha158 feature set (and the
broader quant-finance literature) openly documents - K-bar shape
ratios, rolling price-momentum/dispersion/regression statistics,
rolling volume statistics, and up/down-day counting. These are generic
formulas (the same ones show up, with minor variations, across many
technical-analysis references), not Qlib's proprietary code.

**RESEARCH-ONLY, never wired into production.** Nothing here is added
to `dataset_builder.FEATURE_COLUMNS` / `ml/features.FEATURE_WHITELIST`
- that would silently change what the live ML pipeline trains and
predicts on, which is exactly the kind of "automatically update
existing production strategies" this sprint's rules forbid. These
factors exist for `src/research/sandbox.py` hypotheses to compute and
score candidates against; promoting one into the real feature set is a
separate, deliberate, human-reviewed edit to `dataset_builder.py`,
same boundary `hypothesis_ledger.py` already draws for strategy
results.

**Causal by construction.** Every factor uses only rolling/trailing
windows ending at the current row (`pandas .rolling(window)`, never
`center=True`, never a negative `.shift()`) - see
`tests/test_research_alpha_factors.py`'s no-lookahead regression test
(the same technique `tests/test_dataset_builder.py` already uses for
the production feature set).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_WINDOWS = (5, 10, 20)


# --- K-bar shape ratios (no rolling window - one bar at a time) -------------------------


def kmid(df: pd.DataFrame) -> pd.Series:
    """Candle body, signed, as a fraction of the open - positive means
    the bar closed above where it opened."""
    return (df["close"] - df["open"]) / df["open"]


def klen(df: pd.DataFrame) -> pd.Series:
    """Candle total range (high - low) as a fraction of the open."""
    return (df["high"] - df["low"]) / df["open"]


def kup(df: pd.DataFrame) -> pd.Series:
    """Upper wick length as a fraction of the open."""
    return (df["high"] - df[["open", "close"]].max(axis=1)) / df["open"]


def klow(df: pd.DataFrame) -> pd.Series:
    """Lower wick length as a fraction of the open."""
    return (df[["open", "close"]].min(axis=1) - df["low"]) / df["open"]


def ksft(df: pd.DataFrame) -> pd.Series:
    """Where the close landed within the bar's own high-low range,
    centered at zero (+1 = closed at the high, -1 = closed at the
    low), as a fraction of the open."""
    return (2.0 * df["close"] - df["high"] - df["low"]) / df["open"]


# --- rolling price factors, parameterized by trailing window `d` ------------------------


def roc(df: pd.DataFrame, d: int) -> pd.Series:
    """Rate of change: today's close vs. the close `d` bars ago."""
    return df["close"] / df["close"].shift(d) - 1.0


def ma_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar simple moving average, as a ratio to today's close."""
    return df["close"].rolling(d, min_periods=d).mean() / df["close"]


def std_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar standard deviation of close, as a ratio to today's close."""
    return df["close"].rolling(d, min_periods=d).std() / df["close"]


def beta(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar linear-regression slope of close vs. bar index,
    as a ratio to today's close - a smoother, OLS-based alternative to
    a plain d-bar return."""
    x = np.arange(d, dtype=float)
    x_centered = x - x.mean()
    denom = float((x_centered**2).sum())

    def _slope(window: np.ndarray) -> float:
        y_centered = window - window.mean()
        return float((x_centered * y_centered).sum() / denom)

    return df["close"].rolling(d, min_periods=d).apply(_slope, raw=True) / df["close"]


def rsqr(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar R^2 of close regressed on bar index - how
    cleanly linear (trending) vs. choppy the window has been."""
    x = np.arange(d, dtype=float)
    x_centered = x - x.mean()
    ss_xx = float((x_centered**2).sum())

    def _r_squared(window: np.ndarray) -> float:
        y = window
        y_centered = y - y.mean()
        ss_yy = float((y_centered**2).sum())
        if ss_yy == 0.0:
            return 0.0
        ss_xy = float((x_centered * y_centered).sum())
        return float((ss_xy**2) / (ss_xx * ss_yy))

    return df["close"].rolling(d, min_periods=d).apply(_r_squared, raw=True)


def max_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar highest high, as a ratio to today's close."""
    return df["high"].rolling(d, min_periods=d).max() / df["close"]


def min_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar lowest low, as a ratio to today's close."""
    return df["low"].rolling(d, min_periods=d).min() / df["close"]


def qtl_ratio(df: pd.DataFrame, d: int, quantile: float) -> pd.Series:
    """Trailing `d`-bar close quantile (e.g. 0.8 or 0.2), as a ratio to
    today's close."""
    return df["close"].rolling(d, min_periods=d).quantile(quantile) / df["close"]


def rank(df: pd.DataFrame, d: int) -> pd.Series:
    """Today's close's percentile RANK within the trailing `d`-bar
    window (0 = lowest close in the window, 1 = highest)."""
    return df["close"].rolling(d, min_periods=d).apply(lambda w: pd.Series(w).rank(pct=True).iloc[-1], raw=False)


# --- rolling volume factors --------------------------------------------------------------


def vma_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar mean volume, as a ratio to today's volume."""
    return df["volume"].rolling(d, min_periods=d).mean() / df["volume"].replace(0.0, np.nan)


def vstd_ratio(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar volume standard deviation, as a ratio to today's volume."""
    return df["volume"].rolling(d, min_periods=d).std() / df["volume"].replace(0.0, np.nan)


def corr(df: pd.DataFrame, d: int) -> pd.Series:
    """Trailing `d`-bar correlation between close and log(volume) - a
    standard Alpha158-family price/volume co-movement factor."""
    log_volume = np.log(df["volume"].replace(0.0, np.nan))
    return df["close"].rolling(d, min_periods=d).corr(log_volume)


# --- up/down-day counting -----------------------------------------------------------------


def cntp(df: pd.DataFrame, d: int) -> pd.Series:
    """Fraction of up-days (close > prior close) within the trailing `d`-bar window."""
    up = (df["close"].diff() > 0).astype(float)
    return up.rolling(d, min_periods=d).mean()


def cntn(df: pd.DataFrame, d: int) -> pd.Series:
    """Fraction of down-days (close < prior close) within the trailing `d`-bar window."""
    down = (df["close"].diff() < 0).astype(float)
    return down.rolling(d, min_periods=d).mean()


def cntd(df: pd.DataFrame, d: int) -> pd.Series:
    """CNTP - CNTN: net up-day/down-day balance, in [-1, 1]."""
    return cntp(df, d) - cntn(df, d)


_KBAR_FACTORS = {"kmid": kmid, "klen": klen, "kup": kup, "klow": klow, "ksft": ksft}

_ROLLING_FACTORS = {
    "roc": roc, "ma_ratio": ma_ratio, "std_ratio": std_ratio, "beta": beta, "rsqr": rsqr,
    "max_ratio": max_ratio, "min_ratio": min_ratio, "rank": rank,
    "vma_ratio": vma_ratio, "vstd_ratio": vstd_ratio, "corr": corr,
    "cntp": cntp, "cntn": cntn, "cntd": cntd,
}


def build_factor_table(df: pd.DataFrame, windows: tuple[int, ...] = DEFAULT_WINDOWS) -> pd.DataFrame:
    """Computes every K-bar factor once, and every rolling factor at
    each window in `windows`, returning a new DataFrame (never mutates
    `df`) indexed identically to it. Column names: K-bar factors keep
    their plain name (`"kmid"`); rolling factors are suffixed with
    their window (`"roc_20"`). NaN wherever a window hasn't filled yet
    - never forward-filled or zeroed, same "never fabricate" rule as
    the rest of this codebase's feature code.
    """
    out = pd.DataFrame(index=df.index)
    for name, fn in _KBAR_FACTORS.items():
        out[name] = fn(df)
    for name, fn in _ROLLING_FACTORS.items():
        for d in windows:
            out[f"{name}_{d}"] = fn(df, d)
    for d in windows:
        out[f"qtlu_{d}"] = qtl_ratio(df, d, 0.8)
        out[f"qtld_{d}"] = qtl_ratio(df, d, 0.2)
    return out
