"""Market data provider - a thin `ProviderResult` wrapper around the existing
`data_collector` price fetch, so market data carries the same
source/fetched_at/status metadata as every other provider in this package
(useful for `dataset_builder.py`'s provenance tracking). This does not change
how `main.py` fetches prices for the daily run - `data_collector` is
untouched and remains the single source of truth for that; this module is an
adapter for callers (the dataset builder, big-money context) that want price
history alongside the other providers' uniform result shape.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from . import base

SOURCE_MARKET = "market_data"


def fetch_price_history(symbol: str, config: dict[str, Any], logger: logging.Logger) -> base.ProviderResult[pd.DataFrame]:
    from .. import data_collector
    from ..utils import safe_run

    df = safe_run(logger, symbol, lambda: data_collector.fetch_symbol_history(symbol, config, logger))
    if df is None or df.empty:
        return base.unavailable(SOURCE_MARKET, f"no price history available for {symbol}")

    latest_date = df.index[-1]
    return base.ok(SOURCE_MARKET, df, available_at=latest_date, freshness="daily_bar")
