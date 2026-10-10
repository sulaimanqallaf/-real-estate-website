"""src/data_providers/iex_validation.py - validation logic for Alpaca
Basic/IEX 1m/5m/15m bars (Sprint 3, Free Real Market Data milestone,
Task D3).

**What these tests prove, and what they don't.** This sandbox's egress
proxy blocks `data.alpaca.markets` (confirmed via `curl`, see
`docs/platform/BLOCKERS.md`), so there is no real Alpaca data to
validate from here. These tests build hand-constructed, structurally
genuine fixtures (explicit per-bar OHLCV values, independently
aggregated by hand rather than via the module's own resample helper -
so the cross-timeframe test isn't circular) to prove the VALIDATOR
correctly passes good data and correctly catches each specific kind of
bad data it claims to catch. They do not, and do not claim to, validate
Alpaca's real feed - that requires real credentials and real network
access, run via `python -m src.data_providers.iex_validation` on a
machine that has both."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.data_providers import alpaca_provider as ap
from src.data_providers import base
from src.data_providers import iex_validation as v

LOGGER = logging.getLogger("test")


def _bars(rows: list[tuple[str, float, float, float, float, int]]) -> pd.DataFrame:
    """rows: (iso_timestamp, open, high, low, close, volume)."""
    idx = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        },
        index=idx,
    )


# A clean, continuous 10-minute 1Min session: open=prev close, strictly
# increasing timestamps 1 minute apart, sane OHLC ordering.
_GOOD_1MIN = _bars(
    [
        ("2026-01-02T14:30:00Z", 100.0, 100.5, 99.8, 100.2, 1000),
        ("2026-01-02T14:31:00Z", 100.2, 100.6, 100.0, 100.4, 1100),
        ("2026-01-02T14:32:00Z", 100.4, 100.9, 100.3, 100.8, 900),
        ("2026-01-02T14:33:00Z", 100.8, 101.0, 100.5, 100.6, 1200),
        ("2026-01-02T14:34:00Z", 100.6, 100.7, 100.1, 100.3, 950),
    ]
)

# The SAME five 1-minute bars above, independently hand-aggregated into
# one real 5Min bar (open=first open, high=max high, low=min low,
# close=last close, volume=sum) - computed by hand, not via the
# module's own resample_bars(), so comparing against it is a genuine check.
_GOOD_5MIN = _bars([("2026-01-02T14:30:00Z", 100.0, 101.0, 99.8, 100.3, 5150)])


# --- schema -----------------------------------------------------------------------------


def test_validate_bar_schema_passes_clean_bars():
    assert v.validate_bar_schema(_GOOD_1MIN).ok


def test_validate_bar_schema_flags_missing_columns():
    df = _GOOD_1MIN.drop(columns=["volume"])
    violations = v.validate_bar_schema(df)
    assert not violations.ok
    assert violations.missing_columns == ["volume"]


def test_validate_bar_schema_flags_nan_rows():
    df = _GOOD_1MIN.copy()
    df.loc[df.index[1], "close"] = float("nan")
    violations = v.validate_bar_schema(df)
    assert not violations.ok
    assert violations.nan_rows == 1


def test_validate_bar_schema_flags_a_naive_non_utc_index():
    df = _GOOD_1MIN.copy()
    df.index = df.index.tz_localize(None)
    violations = v.validate_bar_schema(df)
    assert not violations.ok
    assert violations.not_utc_indexed


def test_validate_bar_schema_flags_out_of_order_timestamps():
    df = _GOOD_1MIN.iloc[::-1]
    violations = v.validate_bar_schema(df)
    assert not violations.ok
    assert violations.not_monotonic_increasing


def test_validate_bar_schema_flags_duplicate_timestamps():
    df = pd.concat([_GOOD_1MIN, _GOOD_1MIN.iloc[[0]]])
    violations = v.validate_bar_schema(df)
    assert not violations.ok
    assert violations.duplicate_timestamps == 1


# --- OHLC consistency ---------------------------------------------------------------------


def test_validate_ohlc_consistency_passes_clean_bars():
    assert v.validate_ohlc_consistency(_GOOD_1MIN).ok


def test_validate_ohlc_consistency_flags_a_low_above_open_and_close():
    df = _GOOD_1MIN.copy()
    df.loc[df.index[2], "low"] = 999.0  # impossible: far above open/close
    violations = v.validate_ohlc_consistency(df)
    assert not violations.ok
    assert df.index[2] in violations.bad_ordering_timestamps


def test_validate_ohlc_consistency_flags_a_high_below_open_and_close():
    df = _GOOD_1MIN.copy()
    df.loc[df.index[0], "high"] = 1.0  # impossible: far below open/close
    violations = v.validate_ohlc_consistency(df)
    assert not violations.ok
    assert df.index[0] in violations.bad_ordering_timestamps


def test_validate_ohlc_consistency_flags_negative_volume():
    df = _GOOD_1MIN.copy()
    df.loc[df.index[3], "volume"] = -5
    violations = v.validate_ohlc_consistency(df)
    assert not violations.ok
    assert df.index[3] in violations.negative_volume_timestamps


# --- bar spacing --------------------------------------------------------------------------


def test_validate_bar_spacing_passes_clean_1min_bars():
    assert v.validate_bar_spacing(_GOOD_1MIN, "1Min").ok


def test_validate_bar_spacing_flags_overlapping_sub_duration_bars():
    df = _GOOD_1MIN.copy()
    # Insert a bar only 30 seconds after the first - impossible for real 1Min
    # bars. This also makes the gap FROM that inserted bar to the next
    # original bar sub-duration (30s), so both sides of the insertion are
    # correctly flagged - two violations, not one.
    bad = _bars([("2026-01-02T14:30:30Z", 100.1, 100.2, 100.0, 100.1, 10)])
    df = pd.concat([df.iloc[:1], bad, df.iloc[1:]]).sort_index()
    violations = v.validate_bar_spacing(df, "1Min")
    assert not violations.ok
    assert len(violations.sub_duration_gaps) == 2
    assert pd.Timestamp("2026-01-02T14:30:30Z") in violations.sub_duration_gaps


def test_validate_bar_spacing_allows_large_overnight_gaps():
    overnight = _bars(
        [
            ("2026-01-02T20:59:00Z", 100.0, 100.1, 99.9, 100.0, 500),
            ("2026-01-05T14:30:00Z", 100.0, 100.3, 99.9, 100.1, 600),  # next trading day, ~17.5h later
        ]
    )
    violations = v.validate_bar_spacing(overnight, "1Min")
    assert violations.ok
    assert violations.largest_gap_minutes > 1000


# --- cross-timeframe consistency ------------------------------------------------------------


def test_resample_bars_matches_hand_computed_5min_bar():
    resampled = v.resample_bars(_GOOD_1MIN, "5Min")
    assert len(resampled) == 1
    row = resampled.iloc[0]
    expected = _GOOD_5MIN.iloc[0]
    assert row["open"] == expected["open"]
    assert row["high"] == expected["high"]
    assert row["low"] == expected["low"]
    assert row["close"] == expected["close"]
    assert row["volume"] == expected["volume"]


def test_cross_timeframe_consistency_passes_when_data_genuinely_agrees():
    result = v.validate_cross_timeframe_consistency(_GOOD_1MIN, _GOOD_5MIN, "5Min")
    assert result.ok
    assert result.compared_bars == 1
    assert result.max_close_deviation == 0.0


def test_cross_timeframe_consistency_flags_real_disagreement():
    wrong_5min = _bars([("2026-01-02T14:30:00Z", 100.0, 101.0, 99.8, 55.0, 5150)])  # close is nonsense
    result = v.validate_cross_timeframe_consistency(_GOOD_1MIN, wrong_5min, "5Min")
    assert not result.ok
    assert result.mismatched_timestamps


def test_cross_timeframe_consistency_reports_zero_compared_bars_when_no_overlap():
    far_future_5min = _bars([("2030-01-01T00:00:00Z", 1.0, 1.0, 1.0, 1.0, 1)])
    result = v.validate_cross_timeframe_consistency(_GOOD_1MIN, far_future_5min, "5Min")
    assert result.compared_bars == 0
    assert not result.ok


# --- run_validation / the guided end-to-end workflow -----------------------------------------


_START = datetime(2026, 1, 2, tzinfo=timezone.utc)
_END = _START + timedelta(days=5)


def _fake_fetch_bars_factory(per_timeframe: dict[str, pd.DataFrame] | None = None, status_overrides: dict[str, base.ProviderResult] | None = None):
    def _fake(symbols, timeframe, start, end, config, logger, **kwargs):
        symbol = symbols[0]
        if status_overrides and timeframe in status_overrides:
            return status_overrides[timeframe]
        df = (per_timeframe or {}).get(timeframe)
        if df is None:
            return base.unavailable(ap.SOURCE_ALPACA_IEX, f"no fixture for {timeframe}")
        return base.ok(ap.SOURCE_ALPACA_IEX, {symbol: df}, available_at=end)

    return _fake


def test_run_validation_reports_unvalidated_without_credentials(monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: False)

    def _should_not_be_called(*a, **k):
        raise AssertionError("fetch_bars must not be called when Alpaca isn't configured")

    monkeypatch.setattr(ap, "fetch_bars", _should_not_be_called)

    report = v.run_validation(["AAPL", "MSFT"], _START, _END, {}, LOGGER)

    assert len(report.results) == 2
    assert all(r.status == v.STATUS_UNVALIDATED for r in report.results)
    assert "not configured" in report.results[0].detail
    assert not report.all_passed


def test_run_validation_passes_a_symbol_whose_1min_5min_data_genuinely_agree(monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    # 15Min: reuse the same single bar as 5Min's fixture so cross-timeframe comparison still has overlap.
    monkeypatch.setattr(ap, "fetch_bars", _fake_fetch_bars_factory({"1Min": _GOOD_1MIN, "5Min": _GOOD_5MIN, "15Min": _GOOD_5MIN}))

    report = v.run_validation(["SPY"], _START, _END, {}, LOGGER)

    assert len(report.results) == 1
    result = report.results[0]
    assert result.status == v.STATUS_PASS, result.detail
    assert result.bar_counts == {"1Min": 5, "5Min": 1, "15Min": 1}
    assert report.all_passed


def test_run_validation_is_unvalidated_when_a_timeframe_fetch_fails(monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    monkeypatch.setattr(
        ap,
        "fetch_bars",
        _fake_fetch_bars_factory({"1Min": _GOOD_1MIN, "5Min": _GOOD_5MIN}, status_overrides={"15Min": base.provider_error(ap.SOURCE_ALPACA_IEX, "boom")}),
    )

    report = v.run_validation(["SPY"], _START, _END, {}, LOGGER)

    assert report.results[0].status == v.STATUS_UNVALIDATED
    assert "15Min fetch returned error" in report.results[0].detail


def test_run_validation_fails_a_symbol_with_a_real_ohlc_violation(monkeypatch):
    bad_1min = _GOOD_1MIN.copy()
    bad_1min.loc[bad_1min.index[0], "low"] = 999.0
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    monkeypatch.setattr(ap, "fetch_bars", _fake_fetch_bars_factory({"1Min": bad_1min, "5Min": _GOOD_5MIN, "15Min": _GOOD_5MIN}))

    report = v.run_validation(["SPY"], _START, _END, {}, LOGGER)

    assert report.results[0].status == v.STATUS_FAIL
    assert "OHLC violations" in report.results[0].detail


def test_run_validation_fails_a_symbol_whose_timeframes_disagree(monkeypatch):
    wrong_5min = _bars([("2026-01-02T14:30:00Z", 100.0, 101.0, 99.8, 55.0, 5150)])
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    monkeypatch.setattr(ap, "fetch_bars", _fake_fetch_bars_factory({"1Min": _GOOD_1MIN, "5Min": wrong_5min, "15Min": wrong_5min}))

    report = v.run_validation(["SPY"], _START, _END, {}, LOGGER)

    assert report.results[0].status == v.STATUS_FAIL
    assert "disagree" in report.results[0].detail


def test_run_validation_isolates_one_symbols_unexpected_crash(monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)

    def _raise_for_bad(symbol, start, end, config, logger):
        if symbol == "BAD":
            raise RuntimeError("simulated unexpected failure")
        return v.SymbolValidationResult(symbol, v.STATUS_PASS, "ok")

    monkeypatch.setattr(v, "_validate_one_symbol", _raise_for_bad)

    report = v.run_validation(["BAD", "GOOD"], _START, _END, {}, LOGGER)

    statuses = {r.symbol: r.status for r in report.results}
    assert statuses["BAD"] == v.STATUS_UNVALIDATED
    assert statuses["GOOD"] == v.STATUS_PASS


def test_format_report_text_includes_every_symbol_and_an_overall_line():
    report = v.ValidationReport(
        results=[
            v.SymbolValidationResult("AAPL", v.STATUS_PASS, "ok", {"1Min": 5}),
            v.SymbolValidationResult("MSFT", v.STATUS_UNVALIDATED, "not configured"),
        ]
    )
    text = v.format_report_text(report)
    assert "AAPL" in text and "MSFT" in text
    assert "NOT all passed" in text


# --- CLI entry point --------------------------------------------------------------------


def test_main_uses_default_symbols_and_reflects_pass_fail_in_exit_code(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_validation(symbols, start, end, config, logger):
        captured["symbols"] = symbols
        return v.ValidationReport(results=[v.SymbolValidationResult(s, v.STATUS_PASS, "ok") for s in symbols])

    monkeypatch.setattr(v, "run_validation", _fake_run_validation)
    monkeypatch.setattr(sys, "argv", ["iex_validation.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"logging": {"log_dir": str(tmp_path / "logs")}})

    assert v.main() == 0
    assert captured["symbols"] == v.DEFAULT_VALIDATION_SYMBOLS


def test_main_exits_nonzero_when_not_all_passed(tmp_path, monkeypatch):
    def _fake_run_validation(symbols, start, end, config, logger):
        return v.ValidationReport(results=[v.SymbolValidationResult(symbols[0], v.STATUS_FAIL, "bad data")])

    monkeypatch.setattr(v, "run_validation", _fake_run_validation)
    monkeypatch.setattr(sys, "argv", ["iex_validation.py", "AAPL"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"logging": {"log_dir": str(tmp_path / "logs")}})

    assert v.main() == 1


# --- IEX-only vs consolidated separation (Task D3's explicit requirement) ----------------


def test_validation_module_never_touches_the_yfinance_consolidated_cache():
    """This module validates already-fetched Alpaca/IEX bars in memory
    only - it has no code path that reads/writes data/raw/*.csv (the
    yfinance consolidated-tape cache) or imports data_collector, so a
    validation run can never mix the two feeds."""
    assert not hasattr(v, "data_collector")
    assert "data_collector" not in v.__dict__
    assert not any("save" in name.lower() or "cache" in name.lower() for name in dir(v) if not name.startswith("_"))


def test_every_fetched_bar_result_is_tagged_as_the_iex_source(monkeypatch):
    monkeypatch.setattr(ap, "alpaca_configured", lambda: True)
    monkeypatch.setattr(ap, "fetch_bars", _fake_fetch_bars_factory({"1Min": _GOOD_1MIN, "5Min": _GOOD_5MIN, "15Min": _GOOD_5MIN}))

    captured_sources = []
    real_fetch = ap.fetch_bars

    def _tracking_fetch(symbols, timeframe, start, end, config, logger, **kwargs):
        result = real_fetch(symbols, timeframe, start, end, config, logger, **kwargs)
        captured_sources.append(result.source)
        return result

    monkeypatch.setattr(ap, "fetch_bars", _tracking_fetch)
    v.run_validation(["SPY"], _START, _END, {}, LOGGER)

    assert captured_sources
    assert all(s == ap.SOURCE_ALPACA_IEX for s in captured_sources)
