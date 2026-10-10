"""Alpaca Market Data API - Basic (free) plan, IEX feed only (Sprint 3,
"Free Real Market Data" milestone).

**Verified directly against Alpaca's own `alpaca-py` SDK source** (not
assumed) before writing this - `docs.alpaca.markets` itself is blocked
by this sandbox's egress policy (same class of block already confirmed
for yfinance/SEC/FRED/CCXT in earlier sprints), so the exact REST
contract below was read from `alpaca-py`'s real code on GitHub:
- Base URL `https://data.alpaca.markets`, endpoint `GET /v2/stocks/bars`
  (`alpaca.common.enums.BaseURL.DATA` + `alpaca.data.historical.stock
  .StockHistoricalDataClient.get_stock_bars`'s `path="/stocks/bars"`).
- Auth: `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY` headers
  (`alpaca.common.rest.RESTClient._get_auth_headers`).
- Pagination: request param `page_token`, response field
  `next_page_token`, loop until absent/null
  (`alpaca.common.rest.RESTClient._get_marketdata`).
- Per-bar JSON field mapping (`alpaca.data.mappings.BAR_MAPPING`):
  `t`->timestamp, `o`->open, `h`->high, `l`->low, `c`->close,
  `v`->volume, `n`->trade_count, `vw`->vwap.
- Basic (free) plan limits, cross-confirmed by Alpaca's own docs
  comparison table AND a staff forum reply: **200 historical API calls
  per minute**, IEX feed only (the full consolidated "sip" feed needs
  the $99/mo Algo Trader Plus plan this project does not have and must
  never request), and the most recent ~15 minutes of data are not
  servable on the free feed (Alpaca's own documented recency
  embargo - this module does not try to replicate that client-side; a
  request inside that window simply comes back from the REAL API with
  whatever Alpaca itself reports, reported honestly, never guessed).

**No paid subscription, ever.** `REQUIRED_FEED = "iex"` is hardcoded,
never a config-overridable value - there is no code path in this
module that can request `feed=sip`.

**Data provenance never conflated.** Every result's `source` is
`"alpaca_iex"`, not a generic `"alpaca"` - specifically so a caller (or
the dashboard's data-provider-health panel) can never mistake this
free, IEX-only feed for a full consolidated-tape data source.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .. import reliability
from . import base

SOURCE_ALPACA_IEX = "alpaca_iex"

ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
BARS_ENDPOINT = "/v2/stocks/bars"

# Basic (free) plan ceiling - verified against Alpaca's docs comparison
# table and a staff forum reply confirming "200 calls per minute" ==
# the actual enforced limit (a 429 past it). This is the free plan's
# real ceiling, not a config knob - this project has no paid plan.
BASIC_PLAN_REQUESTS_PER_MINUTE = 200

# Hardcoded, never overridable via config - see module docstring.
REQUIRED_FEED = "iex"

VALID_TIMEFRAMES = {"1Min", "5Min", "15Min", "1Day"}

_BAR_FIELD_MAP = {"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "n": "trade_count", "vw": "vwap"}


def alpaca_configured() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY_ID")) and bool(os.environ.get("ALPACA_API_SECRET_KEY"))


class AlpacaProvider:
    """Satisfies `data_providers.base.DataProvider`."""

    name = SOURCE_ALPACA_IEX

    def is_configured(self) -> bool:
        return alpaca_configured()


class RateLimiter:
    """A plain sliding-window limiter - blocks (sleeps) the calling
    thread rather than ever exceeding `max_per_minute` real requests in
    any trailing 60-second window. Synchronous/blocking is the right
    choice here: this backs a batch historical scanner (Task D2), not
    an interactive request path, and "wait longer" is always safe where
    "send one more request than the free plan allows" is not. A fresh
    instance starts with no history - construct one per logical batch
    job, or share `shared_rate_limiter()`'s module-level singleton
    across an entire scanner run."""

    def __init__(self, max_per_minute: int = BASIC_PLAN_REQUESTS_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self._request_times: list[float] = []

    def wait_if_needed(self) -> None:
        now = time.monotonic()
        self._request_times = [t for t in self._request_times if now - t < 60.0]
        if len(self._request_times) >= self.max_per_minute:
            sleep_seconds = 60.0 - (now - self._request_times[0]) + 0.05
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            now = time.monotonic()
            self._request_times = [t for t in self._request_times if now - t < 60.0]
        self._request_times.append(now)


_shared_limiter = RateLimiter()


def shared_rate_limiter() -> RateLimiter:
    """The module-level limiter `fetch_bars()` uses by default - shared
    across calls in the SAME process so a batch scanner iterating many
    symbols/pages (Task D2) never collectively exceeds the real 200/min
    ceiling even though no single call does on its own."""
    return _shared_limiter


def _auth_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET_KEY", ""),
    }


def _bars_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns=_BAR_FIELD_MAP)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    for optional_col in ("trade_count", "vwap"):
        if optional_col not in df.columns:
            df[optional_col] = None
    return df[["open", "high", "low", "close", "volume", "trade_count", "vwap"]]


def fetch_bars(
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime | None,
    config: dict[str, Any],
    logger: logging.Logger,
    limit_per_page: int = 10_000,
    rate_limiter: RateLimiter | None = None,
    max_pages: int = 500,
) -> base.ProviderResult[dict[str, pd.DataFrame]]:
    """Real historical bars for one or more symbols from Alpaca's free
    Basic/IEX feed - `feed=iex` always, fully paginated (loops on
    `next_page_token` until it's absent or `max_pages` is hit, a
    defensive cap against ever looping forever on a misbehaving
    response). Returns `ProviderResult[dict[symbol, DataFrame]]` - a
    dict even for one symbol, so callers never special-case the
    single- vs. multi-symbol shape. Each DataFrame is indexed by UTC
    timestamp with columns `open/high/low/close/volume/trade_count/
    vwap` (the last two `None` when Alpaca's own response omits them -
    never fabricated).

    Rate-limited via `rate_limiter` (defaults to the shared module-
    level limiter) - blocks rather than ever exceeding
    `BASIC_PLAN_REQUESTS_PER_MINUTE` real requests per minute.
    """
    if timeframe not in VALID_TIMEFRAMES:
        return base.provider_error(SOURCE_ALPACA_IEX, f"unsupported timeframe {timeframe!r} - expected one of {sorted(VALID_TIMEFRAMES)}")
    if not symbols:
        return base.provider_error(SOURCE_ALPACA_IEX, "no symbols provided")
    if not alpaca_configured():
        return base.unavailable(SOURCE_ALPACA_IEX, "ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY not configured (see .env.example)")

    import requests

    limiter = rate_limiter or _shared_limiter

    params: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": start.astimezone(timezone.utc).isoformat(),
        "limit": limit_per_page,
        "feed": REQUIRED_FEED,
        "sort": "asc",
    }
    if end is not None:
        params["end"] = end.astimezone(timezone.utc).isoformat()

    bars_by_symbol: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    page_token: str | None = None
    pages_fetched = 0

    while True:
        request_params = dict(params)
        if page_token:
            request_params["page_token"] = page_token

        limiter.wait_if_needed()

        @reliability.retrying(logger)
        def _do_fetch() -> "requests.Response":
            resp = requests.get(f"{ALPACA_DATA_BASE_URL}{BARS_ENDPOINT}", params=request_params, headers=_auth_headers(), timeout=10)
            resp.raise_for_status()
            return resp

        try:
            payload = _do_fetch().json()
        except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary, same pattern as every other provider in this package
            return base.provider_error(SOURCE_ALPACA_IEX, f"failed to fetch Alpaca bars for {symbols} ({timeframe}): {exc}")

        raw_bars = payload.get("bars") or {}
        for symbol, rows in raw_bars.items():
            if symbol in bars_by_symbol and rows:
                bars_by_symbol[symbol].extend(rows)

        page_token = payload.get("next_page_token")
        pages_fetched += 1
        if not page_token or pages_fetched >= max_pages:
            break

    result: dict[str, pd.DataFrame] = {symbol: _bars_to_dataframe(rows) for symbol, rows in bars_by_symbol.items() if rows}

    if not result:
        return base.unavailable(SOURCE_ALPACA_IEX, f"no bars returned for {symbols} ({timeframe}, feed={REQUIRED_FEED})")

    latest = max(df.index.max() for df in result.values())
    return base.ok(SOURCE_ALPACA_IEX, result, available_at=latest.to_pydatetime(), freshness=f"{timeframe}_iex")
