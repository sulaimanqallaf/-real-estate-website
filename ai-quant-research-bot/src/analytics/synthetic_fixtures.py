"""Deterministic, CLEARLY-LABELED synthetic OHLCV fixtures - used only
where real market data genuinely cannot be fetched (this sandbox's
egress proxy returns a 403 "organization policy" block on
query1.finance.yahoo.com, identical to the CCXT crypto-exchange block
already documented in docs/platform/OSS_INTEGRATION_AUDIT.md - verified
directly, not assumed; see that doc's CCXT section for the same proxy
diagnostic). Every caller of `generate_synthetic_ohlcv` MUST label
results built from it as `"synthetic_fixture"` (never
`"real_market_data"`) wherever that distinction is reported - see
`backtester_crosscheck.run_crosscheck`'s `data_provenance` parameter.

This is NOT a realistic market simulator (no regime changes, no
volatility clustering, no fat tails) - it exists only to deterministically
exercise strategy/backtester/VectorBT code paths end-to-end, never to
support a claim about real-world strategy performance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def generate_synthetic_ohlcv(
    periods: int = 550,
    start: str = "2023-01-02",
    seed: int = 42,
    daily_drift_pct: float = 0.0006,
    daily_vol_pct: float = 0.012,
) -> pd.DataFrame:
    """A seeded geometric random walk with positive drift, business-day
    indexed, columns open/high/low/close/volume (lowercase, matching
    `data_collector.fetch_symbol_history`'s exact shape) - deterministic
    given the same `seed`, so tests and reports built from it are
    reproducible byte-for-byte.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=periods)

    daily_returns = rng.normal(loc=daily_drift_pct, scale=daily_vol_pct, size=periods)
    close = 100.0 * np.cumprod(1.0 + daily_returns)

    # Open = prior close (no overnight gap modeled); high/low bracket the
    # open-close range with a small seeded intrabar range.
    open_ = np.roll(close, 1)
    open_[0] = 100.0
    intrabar_range_pct = np.abs(rng.normal(loc=0.006, scale=0.003, size=periods))
    high = np.maximum(open_, close) * (1.0 + intrabar_range_pct)
    low = np.minimum(open_, close) * (1.0 - intrabar_range_pct)
    volume = rng.integers(1_000_000, 5_000_000, size=periods).astype(float)

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)
    df.index.name = "date"
    return df
