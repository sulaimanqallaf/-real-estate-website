"""Real-data validation for Alpaca Basic/IEX bars (Sprint 3, "Free Real
Market Data" milestone, Task D3: "Validate real 1-minute, 5-minute and
15-minute price data. Keep IEX-only and consolidated data clearly
separated.").

**What this module actually proves, and what it can't from here.** The
checks below (`validate_bar_schema`, `validate_ohlc_consistency`,
`validate_bar_spacing`, `validate_cross_timeframe_consistency`) are real
validation logic, exercised in `tests/test_iex_validation.py` against
synthetic-but-structurally-genuine fixtures (built the same way real
exchange bars are structured: a continuous intraday session resampled
the same way production code would). That proves the VALIDATOR is
correct - it catches out-of-order timestamps, impossible OHLC ordering,
overlapping/sub-duration bars, and 1-minute-vs-5-minute/15-minute
disagreement.

It does **not** prove Alpaca's real feed passes these checks, because
this sandbox's egress proxy blocks `data.alpaca.markets` (confirmed
directly: `curl` to the real bars endpoint returns a `403 Forbidden
organization policy` CONNECT-tunnel rejection, the same class of block
already hit on every other financial-data-vendor host this project
touches - see `docs/platform/BLOCKERS.md`). Running this validator for
real, against real market data, requires real `ALPACA_API_KEY_ID`/
`ALPACA_API_SECRET_KEY` credentials and real outbound network access -
neither of which this sandbox has. `run_validation()`'s own report makes
this distinction explicit: a symbol is only ever reported `PASS`/`FAIL`
when real bars were actually fetched and checked; anything else is
`UNVALIDATED`, with the reason stated, never a fabricated pass.

**IEX-only vs consolidated separation.** This module's checks never
touch `data/raw/*.csv` (the yfinance consolidated-tape cache) at all -
they operate only on bars already fetched via `alpaca_provider.
fetch_bars()` (always `source="alpaca_iex"`) or already cached under
`data/raw_iex/` by `alpaca_scanner.py`. `test_iex_validation.py` asserts
this directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from . import alpaca_provider, base

# Alpaca bar timestamps mark the START of the bar's window (verified
# against alpaca-py's own BAR_MAPPING - see alpaca_provider's module
# docstring), so the nominal duration below is both "how far apart
# consecutive bars must be at minimum" and the pandas resample rule used
# to roll a finer timeframe up into a coarser one.
_TIMEFRAME_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15}

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

# Close-price tolerance for cross-timeframe agreement - OHLC values
# should match to the cent; this just guards against float round-trip
# noise, not real disagreement.
_PRICE_TOLERANCE = 1e-6


@dataclass
class SchemaViolations:
    missing_columns: list[str] = field(default_factory=list)
    nan_rows: int = 0
    not_utc_indexed: bool = False
    not_monotonic_increasing: bool = False
    duplicate_timestamps: int = 0

    @property
    def ok(self) -> bool:
        return not (self.missing_columns or self.nan_rows or self.not_utc_indexed or self.not_monotonic_increasing or self.duplicate_timestamps)


def validate_bar_schema(df: pd.DataFrame) -> SchemaViolations:
    """Structural sanity that has nothing to do with WHICH timeframe this
    is - every bar frame this codebase produces must satisfy this
    regardless of source."""
    violations = SchemaViolations()
    violations.missing_columns = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if violations.missing_columns:
        return violations  # can't check anything else without the columns

    violations.nan_rows = int(df[list(_REQUIRED_COLUMNS)].isna().any(axis=1).sum())
    violations.not_utc_indexed = not (isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None)
    violations.not_monotonic_increasing = not df.index.is_monotonic_increasing
    violations.duplicate_timestamps = int(df.index.duplicated().sum())
    return violations


@dataclass
class OhlcViolations:
    bad_ordering_timestamps: list[Any] = field(default_factory=list)   # low>min(o,c) or high<max(o,c)
    negative_volume_timestamps: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.bad_ordering_timestamps or self.negative_volume_timestamps)


def validate_ohlc_consistency(df: pd.DataFrame) -> OhlcViolations:
    """For every bar: low <= min(open, close) <= max(open, close) <= high,
    and volume >= 0 - true of any real exchange bar, synthetic or not."""
    violations = OhlcViolations()
    lo = df[["open", "close"]].min(axis=1)
    hi = df[["open", "close"]].max(axis=1)
    bad_ordering = (df["low"] > lo) | (df["high"] < hi) | (df["low"] > df["high"])
    violations.bad_ordering_timestamps = list(df.index[bad_ordering])
    violations.negative_volume_timestamps = list(df.index[df["volume"] < 0])
    return violations


@dataclass
class SpacingViolations:
    sub_duration_gaps: list[Any] = field(default_factory=list)  # timestamp of the bar whose gap to the PREVIOUS bar was too small
    largest_gap_minutes: float | None = None

    @property
    def ok(self) -> bool:
        return not self.sub_duration_gaps


def validate_bar_spacing(df: pd.DataFrame, timeframe: str) -> SpacingViolations:
    """No two consecutive bars may be closer together than the
    timeframe's own nominal duration - that would mean overlapping or
    duplicate bars, which real exchange data never produces. LARGER gaps
    (market closed overnight/weekend/holiday) are expected and fine -
    this never flags those as a violation, only reports the largest one
    seen, for visibility."""
    nominal_minutes = _TIMEFRAME_MINUTES[timeframe]
    violations = SpacingViolations()
    if len(df) < 2:
        return violations

    gaps = df.index.to_series().diff().dropna()
    gap_minutes = gaps.dt.total_seconds() / 60.0
    too_small = gap_minutes < (nominal_minutes - 1e-9)
    violations.sub_duration_gaps = list(df.index[1:][too_small.to_numpy()])
    violations.largest_gap_minutes = float(gap_minutes.max()) if not gap_minutes.empty else None
    return violations


@dataclass
class CrossTimeframeResult:
    compared_bars: int
    max_close_deviation: float | None = None
    mismatched_timestamps: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compared_bars > 0 and not self.mismatched_timestamps


def resample_bars(fine_df: pd.DataFrame, coarse_timeframe: str) -> pd.DataFrame:
    """Roll finer bars (e.g. 1Min) up into the given coarser timeframe's
    nominal window, the same left-closed/left-labeled convention Alpaca
    itself uses for bar start timestamps (see module docstring) - so the
    result's index lines up with a real `coarse_timeframe` fetch's
    index, bucket for bucket."""
    rule = f"{_TIMEFRAME_MINUTES[coarse_timeframe]}min"
    agg = fine_df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open", "high", "low", "close"])


def validate_cross_timeframe_consistency(fine_df: pd.DataFrame, coarse_df: pd.DataFrame, coarse_timeframe: str) -> CrossTimeframeResult:
    """Resamples `fine_df` (e.g. real 1Min bars) up to `coarse_timeframe`
    and compares it against `coarse_df` (e.g. real 5Min bars fetched
    independently) on every timestamp both have. If these came from the
    same real underlying tape, OHLC must agree to the cent - this is the
    strongest evidence available that the two timeframes are genuinely
    the same market data at different granularities, not independently
    wrong or mismatched."""
    resampled = resample_bars(fine_df, coarse_timeframe)
    common_index = resampled.index.intersection(coarse_df.index)
    if len(common_index) == 0:
        return CrossTimeframeResult(compared_bars=0)

    a = resampled.loc[common_index, ["open", "high", "low", "close"]]
    b = coarse_df.loc[common_index, ["open", "high", "low", "close"]]
    deviation = (a - b).abs()
    max_deviation = float(deviation.to_numpy().max())
    mismatched = deviation.index[(deviation > _PRICE_TOLERANCE).any(axis=1)]
    return CrossTimeframeResult(compared_bars=len(common_index), max_close_deviation=max_deviation, mismatched_timestamps=list(mismatched))


# --- the guided, end-to-end report (real data only when real credentials/network exist) ---

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNVALIDATED = "UNVALIDATED"

# A small, liquid default set - validation is about data QUALITY, not
# universe breadth (that's Task D2's job).
DEFAULT_VALIDATION_SYMBOLS = ["SPY", "AAPL", "MSFT"]


@dataclass
class SymbolValidationResult:
    symbol: str
    status: str
    detail: str
    bar_counts: dict[str, int] = field(default_factory=dict)
    max_cross_timeframe_deviation: dict[str, float] = field(default_factory=dict)


@dataclass
class ValidationReport:
    results: list[SymbolValidationResult] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.status == STATUS_PASS for r in self.results)


def _validate_one_symbol(symbol: str, start: datetime, end: datetime, config: dict[str, Any], logger: logging.Logger) -> SymbolValidationResult:
    bars: dict[str, pd.DataFrame] = {}
    for timeframe in ("1Min", "5Min", "15Min"):
        result = alpaca_provider.fetch_bars([symbol], timeframe, start, end, config, logger)
        if result.status != base.STATUS_OK:
            return SymbolValidationResult(symbol, STATUS_UNVALIDATED, f"{timeframe} fetch returned {result.status}: {result.error}")
        df = (result.data or {}).get(symbol)
        if df is None or df.empty:
            return SymbolValidationResult(symbol, STATUS_UNVALIDATED, f"no {timeframe} bars returned for {symbol}")
        bars[timeframe] = df

    bar_counts = {tf: len(df) for tf, df in bars.items()}
    problems: list[str] = []

    for timeframe, df in bars.items():
        schema = validate_bar_schema(df)
        if not schema.ok:
            problems.append(f"{timeframe} schema violations: {schema}")
        ohlc = validate_ohlc_consistency(df)
        if not ohlc.ok:
            problems.append(f"{timeframe} OHLC violations at {ohlc.bad_ordering_timestamps[:5]}")
        spacing = validate_bar_spacing(df, timeframe)
        if not spacing.ok:
            problems.append(f"{timeframe} sub-duration gaps at {spacing.sub_duration_gaps[:5]}")

    deviations: dict[str, float] = {}
    for coarse_timeframe in ("5Min", "15Min"):
        cross = validate_cross_timeframe_consistency(bars["1Min"], bars[coarse_timeframe], coarse_timeframe)
        if cross.compared_bars == 0:
            problems.append(f"1Min/{coarse_timeframe} had no overlapping bars to compare")
        elif not cross.ok:
            problems.append(f"1Min/{coarse_timeframe} disagree at {cross.mismatched_timestamps[:5]} (max deviation {cross.max_close_deviation})")
        if cross.max_close_deviation is not None:
            deviations[coarse_timeframe] = cross.max_close_deviation

    if problems:
        return SymbolValidationResult(symbol, STATUS_FAIL, "; ".join(problems), bar_counts, deviations)
    return SymbolValidationResult(symbol, STATUS_PASS, "all schema/OHLC/spacing/cross-timeframe checks passed", bar_counts, deviations)


def run_validation(
    symbols: list[str], start: datetime, end: datetime, config: dict[str, Any], logger: logging.Logger
) -> ValidationReport:
    """The guided D3 validation workflow: for each symbol, fetches REAL
    1Min/5Min/15Min bars (requires real Alpaca credentials + network -
    see module docstring) and runs every check above. Never fabricates a
    PASS: a symbol whose data couldn't be fetched is `UNVALIDATED`, not
    silently skipped or assumed good."""
    report = ValidationReport(started_at=base.utcnow())
    if not alpaca_provider.alpaca_configured():
        for symbol in symbols:
            report.results.append(SymbolValidationResult(symbol, STATUS_UNVALIDATED, "ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY not configured"))
        report.finished_at = base.utcnow()
        return report

    for symbol in symbols:
        from ..utils import safe_run

        result = safe_run(logger, symbol, lambda s=symbol: _validate_one_symbol(s, start, end, config, logger))
        report.results.append(result or SymbolValidationResult(symbol, STATUS_UNVALIDATED, "validation raised an unexpected error - see logs"))

    report.finished_at = base.utcnow()
    return report


def format_report_text(report: ValidationReport) -> str:
    lines = [f"Alpaca/IEX 1m/5m/15m bar validation - {len(report.results)} symbol(s):"]
    for r in report.results:
        counts = ", ".join(f"{tf}={n}" for tf, n in r.bar_counts.items()) or "no bars fetched"
        lines.append(f"  [{r.status}] {r.symbol}: {counts} - {r.detail}")
    lines.append(f"Overall: {'ALL PASSED' if report.all_passed else 'NOT all passed - see above'}")
    return "\n".join(lines)


def main() -> int:
    """`python -m src.data_providers.iex_validation [symbol ...]` - run
    this on a machine with real `ALPACA_API_KEY_ID`/
    `ALPACA_API_SECRET_KEY` credentials and real network access (this
    sandbox has neither - see module docstring and
    `docs/platform/BLOCKERS.md`). Read-only; never touches a broker."""
    import argparse
    from datetime import timedelta, timezone

    from ..utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Validate real Alpaca/IEX 1m/5m/15m bar data.")
    parser.add_argument("symbols", nargs="*", default=DEFAULT_VALIDATION_SYMBOLS)
    parser.add_argument("--days", type=int, default=5, help="How many recent days of intraday bars to validate (default: 5).")
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="iex_validation.log")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    report = run_validation(args.symbols, start, end, config, logger)

    print(format_report_text(report))
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
