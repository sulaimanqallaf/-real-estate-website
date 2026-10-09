"""Fetch daily OHLCV and (best-effort) options chain data via yfinance.

Version 1 uses yfinance for both price and options data. Options IV data on free
Yahoo Finance is inconsistent (missing/zero IV on illiquid strikes, no guarantee of
data for every ticker/expiration). The options fetch is therefore isolated from the
price fetch: a ticker's price history load never fails because its options chain was
unavailable, and options_skew.py is written to degrade to "Data Unavailable" rather
than raising when this happens.

To swap in a paid provider later (Polygon, Tradier, ORATS, Intrinio, CBOE LiveVol):
implement a fetch_option_chain_snapshot(symbol, config) with the same return shape
used here (see options_skew.OptionChainSnapshot) and point options_skew.py at it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from . import reliability
from .utils import resolve_path


def _fetch_history_with_retry(symbol: str, period: str, interval: str, logger: logging.Logger) -> pd.DataFrame:
    """Isolated so ONLY the network call itself is retried (Sprint 3:
    "test internet outages... use Tenacity where beneficial") - a
    transient ConnectionError/Timeout gets up to 3 attempts with
    backoff; the empty-dataframe check below (a legitimate "no data
    for this symbol" result, not a network blip) is deliberately
    OUTSIDE this retried call and never retried."""

    @reliability.retrying(logger)
    def _do_fetch() -> pd.DataFrame:
        return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)

    return _do_fetch()


def fetch_symbol_history(symbol: str, config: dict[str, Any], logger: logging.Logger) -> pd.DataFrame:
    """Download daily history for one symbol and cache it locally."""
    period = config["data"]["history_period"]
    interval = config["data"]["interval"]

    logger.info("Fetching daily history for %s (%s, %s)", symbol, period, interval)
    df = _fetch_history_with_retry(symbol, period, interval, logger)

    if df is None or df.empty:
        raise ValueError(f"yfinance returned no price data for {symbol}")

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df.index.name = "date"
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])

    _save_raw_csv(symbol, df, config)
    return df


def _save_raw_csv(symbol: str, df: pd.DataFrame, config: dict[str, Any]) -> Path:
    raw_dir = resolve_path(config["data"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{symbol}_daily.csv"
    df.to_csv(out_path)
    return out_path


def fetch_all_price_history(
    symbols: list[str], config: dict[str, Any], logger: logging.Logger
) -> dict[str, pd.DataFrame]:
    """Fetch daily history for every symbol, skipping (and logging) any that fail."""
    from .utils import safe_run

    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = safe_run(logger, symbol, lambda s=symbol: fetch_symbol_history(s, config, logger))
        if df is not None:
            results[symbol] = df
    return results


def fetch_current_price(symbol: str, logger: logging.Logger) -> float | None:
    """Best-effort near-real-time last price for one symbol via yfinance's
    `fast_info` - used only for the Phase 7 execution layer's pre-
    submission slippage check (`pretrade_checks.check_slippage`), never
    for sizing or any other decision. Returns None (never raises) on any
    failure - callers must treat None as "price unavailable," which
    `check_slippage()` already treats as a hard block rather than
    submitting blind."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = info.get("lastPrice") if hasattr(info, "get") else getattr(info, "last_price", None)
        return float(price) if price is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch a current price for %s: %s", symbol, exc)
        return None


def bar_freshness(symbol: str, config: dict[str, Any], logger: logging.Logger, now: Any = None) -> dict[str, Any]:
    """Age in days of the most recent cached daily bar for `symbol`
    (`data/raw/{symbol}_daily.csv`, written by `fetch_symbol_history()`),
    for `circuit_breaker.check_all()`'s `latest_bar_age_days` input -
    GitHub Issue #1: "never assume delayed market data is real-time" -
    plus a `status` field so callers (notably `execution/run_health.py`'s
    health command) can tell "no cached data yet" apart from "a parsing
    problem means we genuinely don't know" instead of both collapsing
    into the same `None` and being treated as indistinguishable from
    "fresh."

    Returns `{"age_days": int | None, "status": "ok" | "no_data" |
    "invalid_timestamp", "detail": str | None}`. `age_days` is only ever
    a real number when `status == "ok"` - never fabricated as fresh on
    `no_data`/`invalid_timestamp`.

    **Deliberately does NOT use `pandas.read_csv(..., parse_dates=True)`
    on the whole index column.** yfinance's cached index is in the
    EXCHANGE timezone (e.g. `America/New_York`), so a cache file whose
    history spans a DST transition has rows with two different UTC
    offsets (`-04:00` then `-05:00`). pandas silently gives up turning a
    mixed-offset column like that into a `DatetimeIndex` and leaves it as
    plain Python strings instead - with NO error raised at read time -
    which is exactly what produced the `'str' object has no attribute
    'tzinfo'` failure seen on every ticker once the real cache crossed a
    DST boundary. Parsing only the single value actually needed, with
    `pandas.to_datetime(..., errors="coerce")`, sidesteps that column-wide
    inference (and its silent-fallback failure mode) entirely, and also
    correctly handles a plain date-only value (`2026-10-08`, no time or
    offset at all - treated as already being that calendar date, since
    there is no timezone on record to convert) alongside a genuinely
    unparseable value (NaT -> `invalid_timestamp`, never swallowed into a
    fabricated age)."""
    from datetime import datetime, timezone

    raw_dir = config.get("data", {}).get("raw_dir")
    if not raw_dir:
        return {"age_days": None, "status": "no_data", "detail": "data.raw_dir not configured"}
    raw_path = resolve_path(raw_dir) / f"{symbol}_daily.csv"
    if not raw_path.exists():
        return {"age_days": None, "status": "no_data", "detail": "no cached file"}

    try:
        df = pd.read_csv(raw_path, index_col=0)
    except Exception as exc:  # noqa: BLE001 - an unreadable cache file is a real, reportable problem, not "fresh"
        logger.warning("Could not read cached bars for %s: %s", symbol, exc)
        return {"age_days": None, "status": "invalid_timestamp", "detail": f"unreadable cache file: {exc}"}

    if df.empty:
        return {"age_days": None, "status": "no_data", "detail": "cached file is empty"}

    raw_value = df.index[-1]
    parsed = pd.to_datetime(raw_value, errors="coerce")
    if parsed is None or pd.isna(parsed):
        logger.warning("Could not parse latest bar timestamp for %s: %r", symbol, raw_value)
        return {"age_days": None, "status": "invalid_timestamp", "detail": f"unparseable timestamp: {raw_value!r}"}

    reference = now or datetime.now(timezone.utc)
    if parsed.tzinfo is not None:
        bar_date = parsed.tz_convert("UTC").date()
    else:
        bar_date = parsed.date()  # naive/date-only value: no timezone on record to convert, used as-is

    return {"age_days": (reference.date() - bar_date).days, "status": "ok", "detail": None}


def latest_bar_age_days(symbol: str, config: dict[str, Any], logger: logging.Logger, now: Any = None) -> int | None:
    """Thin `age_days`-only view of `bar_freshness()`, kept for existing
    callers (`circuit_breaker.check_all()`, `execution/approval_bridge.py`,
    `main.py`) that only ever treated `None` as "unknown, fail open" and
    have no use for the richer status - `None` here now means EITHER
    `no_data` OR `invalid_timestamp`; callers that need to tell those
    apart (see `execution/run_health.py`'s stale-data check) should call
    `bar_freshness()` directly instead."""
    return bar_freshness(symbol, config, logger, now=now)["age_days"]


def fetch_raw_option_chain(symbol: str, expiration: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (calls, puts) DataFrames for one expiration. Raises on any failure."""
    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiration)
    return chain.calls, chain.puts


def list_expirations(symbol: str) -> list[str]:
    """Return available option expiration date strings for a symbol. Raises on failure."""
    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No option expirations available for {symbol}")
    return list(expirations)
