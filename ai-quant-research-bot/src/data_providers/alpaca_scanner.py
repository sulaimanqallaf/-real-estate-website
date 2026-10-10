"""Scalable historical scanner for Alpaca's free Basic/IEX feed (Sprint 3,
"Free Real Market Data" milestone, Task D2: "Build a scalable historical
scanner for 500-1,000 stocks/ETFs").

Batches an arbitrary symbol list (the caller's universe - this module makes
no claim about which real tickers belong in it; see `src/universe.py` for
this project's own curated candidate pool) through `alpaca_provider.
fetch_bars()`, respecting the real Basic-plan rate limit via ONE SHARED
`RateLimiter` across the WHOLE scan (every batch, not one limiter per
batch) - see `alpaca_provider.shared_rate_limiter()`. This is what makes it
scale to hundreds/low-thousands of symbols safely: batching bounds each
individual HTTP request's size, and the shared limiter bounds the whole
run's real request rate regardless of how many batches that takes.

Each symbol's bars are cached to their own file, in a directory clearly
separated from every other data source's cache:

- yfinance consolidated daily bars: `data/raw/{symbol}_daily.csv` (existing,
  `data_collector.py`, untouched by this module).
- Alpaca free IEX-only bars: `data/raw_iex/{symbol}_{timeframe}.csv` (this
  module) - a DIFFERENT directory, a DIFFERENT filename shape (the
  timeframe is part of the name, since unlike the yfinance cache this one
  may hold several timeframes per symbol), and every result's `source` is
  `alpaca_provider.SOURCE_ALPACA_IEX` ("alpaca_iex"), never a generic
  "alpaca" or "market_data" - so this free, IEX-exchange-only feed can
  never be conflated with the full consolidated tape by a later caller,
  dashboard panel, or report. See Task D3 for explicit validation of this
  separation.

A batch that comes back `unavailable`/`error` as a whole never aborts the
rest of the scan - `ScanReport` records every symbol in it as unavailable
and the scan continues with the next batch, the same "skip and keep going"
convention as `data_collector.fetch_all_price_history()`. Nothing here ever
reports a symbol as succeeded unless real bars were actually returned and
(when `cache=True`) written to disk for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import alpaca_provider, base
from ..utils import resolve_path

# A practical choice to keep each HTTP request's URL/response reasonably
# sized - NOT a documented Alpaca hard limit (their docs are blocked in
# this sandbox; see alpaca_provider's module docstring for what was
# directly verified against real SDK source instead of assumed).
DEFAULT_BATCH_SIZE = 100


def iex_raw_dir(config: dict[str, Any]) -> Path:
    configured = config.get("data", {}).get("iex_raw_dir")
    return resolve_path(configured) if configured else resolve_path("data/raw_iex")


def _cache_path(symbol: str, timeframe: str, config: dict[str, Any]) -> Path:
    return iex_raw_dir(config) / f"{symbol}_{timeframe}.csv"


def _save_symbol_bars(symbol: str, timeframe: str, df: pd.DataFrame, config: dict[str, Any]) -> Path:
    out_dir = iex_raw_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _cache_path(symbol, timeframe, config)
    df.to_csv(out_path)
    return out_path


@dataclass
class ScanReport:
    """Summary of one `scan_symbols()` run. `succeeded` only ever lists a
    symbol that real bars were actually returned (and, when caching,
    actually written) for - never inferred or assumed."""

    timeframe: str
    requested: list[str]
    succeeded: list[str] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)  # symbol -> reason
    bar_counts: dict[str, int] = field(default_factory=dict)
    batches_fetched: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def failed(self) -> list[str]:
        return sorted(set(self.requested) - set(self.succeeded))


def _batched(symbols: list[str], batch_size: int) -> list[list[str]]:
    return [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]


def scan_symbols(
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime | None,
    config: dict[str, Any],
    logger: logging.Logger,
    batch_size: int = DEFAULT_BATCH_SIZE,
    rate_limiter: alpaca_provider.RateLimiter | None = None,
    cache: bool = True,
) -> ScanReport:
    """Fetch (and by default cache) real Alpaca/IEX historical bars for up
    to hundreds/low-thousands of symbols in one run.

    De-duplicates and sorts `symbols` first (so a caller's universe list
    with accidental duplicates, or a different ordering, always scans
    deterministically). Splits into batches of `batch_size`, calling
    `alpaca_provider.fetch_bars()` once per batch with a rate limiter
    SHARED across every batch (defaults to `alpaca_provider.
    shared_rate_limiter()`) so the whole run - not just each individual
    call - stays under the real per-minute ceiling.
    """
    limiter = rate_limiter or alpaca_provider.shared_rate_limiter()
    deduped = sorted(dict.fromkeys(symbols))
    report = ScanReport(timeframe=timeframe, requested=deduped, started_at=base.utcnow())

    for batch in _batched(deduped, max(1, batch_size)):
        result = alpaca_provider.fetch_bars(batch, timeframe, start, end, config, logger, rate_limiter=limiter)
        report.batches_fetched += 1

        if result.status != base.STATUS_OK:
            for symbol in batch:
                report.unavailable[symbol] = result.error or "no data"
            logger.warning("Alpaca/IEX scan batch of %d symbol(s) returned %s: %s", len(batch), result.status, result.error)
            continue

        bars_by_symbol = result.data or {}
        for symbol in batch:
            df = bars_by_symbol.get(symbol)
            if df is None or df.empty:
                report.unavailable[symbol] = "no bars returned for this symbol"
                continue
            if cache:
                _save_symbol_bars(symbol, timeframe, df, config)
            report.succeeded.append(symbol)
            report.bar_counts[symbol] = len(df)

    report.finished_at = base.utcnow()
    logger.info(
        "Alpaca/IEX scan (%s): %d/%d symbols succeeded across %d batch(es) in %.1fs.",
        timeframe,
        len(report.succeeded),
        len(report.requested),
        report.batches_fetched,
        report.duration_seconds or 0.0,
    )
    return report


def main() -> int:
    """`python -m src.data_providers.alpaca_scanner [--timeframe TF] [--days N]
    [symbol ...]` - a standalone, read-only CLI for running a historical
    scan without wiring it into `main.py`. Symbols default to `src.
    universe.DEFAULT_CANDIDATE_POOL` (this project's own curated starting
    pool) when none are given on the command line. Never touches a
    broker, never places an order - this is a market-data fetch only."""
    import argparse
    from datetime import timedelta, timezone

    from .. import universe
    from ..utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Scalable historical scanner for Alpaca's free Basic/IEX feed.")
    parser.add_argument("symbols", nargs="*", help="Symbols to scan (default: this project's curated candidate pool).")
    parser.add_argument("--timeframe", default="1Day", choices=sorted(alpaca_provider.VALID_TIMEFRAMES))
    parser.add_argument("--days", type=int, default=30, help="How many days of history to request (default: 30).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="alpaca_scan.log")

    symbols = args.symbols or universe.DEFAULT_CANDIDATE_POOL
    start = datetime.now(timezone.utc) - timedelta(days=args.days)

    report = scan_symbols(symbols, args.timeframe, start, None, config, logger, batch_size=args.batch_size)

    print(f"Alpaca/IEX scan ({args.timeframe}): {len(report.succeeded)}/{len(report.requested)} symbols succeeded "
          f"across {report.batches_fetched} batch(es) in {report.duration_seconds or 0.0:.1f}s.")
    if report.failed:
        print(f"Unavailable ({len(report.failed)}): " + ", ".join(f"{s} ({report.unavailable.get(s, '?')})" for s in report.failed[:20]))
        if len(report.failed) > 20:
            print(f"  ... and {len(report.failed) - 20} more.")
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
