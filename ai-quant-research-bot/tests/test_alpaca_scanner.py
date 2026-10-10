"""src/data_providers/alpaca_scanner.py - scalable historical scanner for
Alpaca Basic/IEX bars across many symbols (Sprint 3, Free Real Market
Data milestone, Task D2). `alpaca_provider.fetch_bars()` itself is fully
covered by test_alpaca_provider.py - these tests fake it out entirely and
focus on this module's own job: batching, de-duplication, per-symbol
caching into a directory separate from the yfinance cache, never
aborting the whole scan on one bad batch, and sharing one rate limiter
across the whole run."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.data_providers import alpaca_provider as ap
from src.data_providers import alpaca_scanner as scanner
from src.data_providers import base

LOGGER = logging.getLogger("test")

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _config(tmp_path):
    return {"data": {"iex_raw_dir": str(tmp_path / "raw_iex")}}


def _bars_df(symbol: str, n: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [2.0] * n, "low": [0.5] * n, "close": [1.5] * n, "volume": [100] * n, "trade_count": [10] * n, "vwap": [1.4] * n},
        index=idx,
    )


def _make_fake_fetch_bars(calls, by_batch=None, default_ok=True):
    """Records every call (as a dict) into `calls`, and returns a
    ProviderResult per batch. `by_batch` (optional) maps a tuple of the
    batch's symbols to a ProviderResult to return instead of the default
    all-succeed behaviour."""

    def _fake(symbols, timeframe, start, end, config, logger, limit_per_page=10_000, rate_limiter=None, max_pages=500):
        calls.append({"symbols": list(symbols), "timeframe": timeframe, "rate_limiter": rate_limiter})
        key = tuple(symbols)
        if by_batch and key in by_batch:
            return by_batch[key]
        if not default_ok:
            return base.unavailable(ap.SOURCE_ALPACA_IEX, "no data")
        data = {s: _bars_df(s) for s in symbols}
        return base.ok(ap.SOURCE_ALPACA_IEX, data, available_at=end or start)

    return _fake


# --- batching + de-duplication --------------------------------------------------------


def test_scan_splits_symbols_into_batches_of_the_requested_size(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    symbols = [f"SYM{i:04d}" for i in range(250)]
    report = scanner.scan_symbols(symbols, "1Day", START, END, _config(tmp_path), LOGGER, batch_size=100)

    assert [len(c["symbols"]) for c in calls] == [100, 100, 50]
    assert report.batches_fetched == 3
    assert sorted(report.succeeded) == sorted(symbols)


def test_scan_deduplicates_and_sorts_symbols_before_batching(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    report = scanner.scan_symbols(["MSFT", "AAPL", "MSFT", "AAPL"], "1Day", START, END, _config(tmp_path), LOGGER)

    assert report.requested == ["AAPL", "MSFT"]
    assert calls[0]["symbols"] == ["AAPL", "MSFT"]


# --- caching, clearly separated from the yfinance cache -------------------------------


def test_scan_caches_each_symbol_to_its_own_file_under_iex_raw_dir(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    config = _config(tmp_path)
    report = scanner.scan_symbols(["AAPL", "MSFT"], "1Day", START, END, config, LOGGER)

    aapl_path = tmp_path / "raw_iex" / "AAPL_1Day.csv"
    msft_path = tmp_path / "raw_iex" / "MSFT_1Day.csv"
    assert aapl_path.exists()
    assert msft_path.exists()
    cached = pd.read_csv(aapl_path, index_col=0)
    assert len(cached) == 3
    assert report.bar_counts == {"AAPL": 3, "MSFT": 3}
    assert report.succeeded == ["AAPL", "MSFT"]


def test_scan_cache_false_fetches_but_never_writes_to_disk(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    config = _config(tmp_path)
    report = scanner.scan_symbols(["AAPL"], "1Day", START, END, config, LOGGER, cache=False)

    assert report.succeeded == ["AAPL"]
    assert not (tmp_path / "raw_iex").exists()


def test_default_iex_raw_dir_is_different_from_the_yfinance_raw_dir():
    config = {"data": {"raw_dir": "data/raw"}}  # no iex_raw_dir override
    assert scanner.iex_raw_dir(config).name == "raw_iex"
    assert scanner.iex_raw_dir(config) != scanner.iex_raw_dir(config).parent / "raw"


# --- one bad batch never aborts the whole scan -----------------------------------------


def test_a_failed_batch_is_recorded_unavailable_and_the_scan_continues(tmp_path, monkeypatch):
    calls = []
    failing_batch = ("BAD1", "BAD2")
    by_batch = {failing_batch: base.provider_error(ap.SOURCE_ALPACA_IEX, "simulated network failure")}
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls, by_batch=by_batch))

    symbols = ["BAD1", "BAD2", "GOOD1", "GOOD2"]
    report = scanner.scan_symbols(symbols, "1Day", START, END, _config(tmp_path), LOGGER, batch_size=2)

    assert report.batches_fetched == 2
    assert report.succeeded == ["GOOD1", "GOOD2"]
    assert report.unavailable == {"BAD1": "simulated network failure", "BAD2": "simulated network failure"}
    assert report.failed == ["BAD1", "BAD2"]


def test_a_symbol_missing_from_the_returned_data_is_unavailable_not_fabricated(tmp_path, monkeypatch):
    def _fake(symbols, timeframe, start, end, config, logger, limit_per_page=10_000, rate_limiter=None, max_pages=500):
        return base.ok(ap.SOURCE_ALPACA_IEX, {"AAPL": _bars_df("AAPL")}, available_at=end)

    monkeypatch.setattr(ap, "fetch_bars", _fake)

    report = scanner.scan_symbols(["AAPL", "DELISTED"], "1Day", START, END, _config(tmp_path), LOGGER)

    assert report.succeeded == ["AAPL"]
    assert report.unavailable == {"DELISTED": "no bars returned for this symbol"}
    assert not (tmp_path / "raw_iex" / "DELISTED_1Day.csv").exists()


# --- rate limiting shared across the whole run, not per batch -------------------------


def test_scan_shares_one_rate_limiter_across_every_batch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    symbols = [f"SYM{i:03d}" for i in range(30)]
    scanner.scan_symbols(symbols, "1Day", START, END, _config(tmp_path), LOGGER, batch_size=10)

    assert len(calls) == 3
    limiters_used = {id(c["rate_limiter"]) for c in calls}
    assert len(limiters_used) == 1, "every batch must reuse the same RateLimiter instance"


def test_scan_uses_the_rate_limiter_passed_in_explicitly(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    my_limiter = ap.RateLimiter(max_per_minute=5)
    scanner.scan_symbols(["AAPL"], "1Day", START, END, _config(tmp_path), LOGGER, rate_limiter=my_limiter)

    assert calls[0]["rate_limiter"] is my_limiter


# --- report shape / timing -------------------------------------------------------------


def test_report_records_start_and_finish_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars([]))
    report = scanner.scan_symbols(["AAPL"], "1Day", START, END, _config(tmp_path), LOGGER)

    assert report.started_at is not None
    assert report.finished_at is not None
    assert report.finished_at >= report.started_at
    assert report.duration_seconds is not None and report.duration_seconds >= 0.0


def test_scan_report_duration_is_none_before_it_has_run():
    report = scanner.ScanReport(timeframe="1Day", requested=["AAPL"])
    assert report.duration_seconds is None


# --- scalability: handles the full 500-1000 symbol target without special-casing ------


def test_scan_handles_one_thousand_symbols_across_many_batches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_bars", _make_fake_fetch_bars(calls))

    symbols = [f"SYM{i:04d}" for i in range(1000)]
    report = scanner.scan_symbols(symbols, "1Day", START, END, _config(tmp_path), LOGGER, batch_size=100)

    assert report.batches_fetched == 10
    assert len(report.succeeded) == 1000
    assert report.failed == []


# --- CLI entry point --------------------------------------------------------------------


def test_main_defaults_to_the_curated_candidate_pool_when_no_symbols_given(tmp_path, monkeypatch):
    from src import universe

    captured = {}

    def _fake_scan_symbols(symbols, timeframe, start, end, config, logger, batch_size=scanner.DEFAULT_BATCH_SIZE, **kwargs):
        captured["symbols"] = symbols
        captured["timeframe"] = timeframe
        return scanner.ScanReport(timeframe=timeframe, requested=list(symbols), succeeded=list(symbols), started_at=base.utcnow(), finished_at=base.utcnow())

    monkeypatch.setattr(scanner, "scan_symbols", _fake_scan_symbols)
    monkeypatch.setattr(sys, "argv", ["alpaca_scanner.py"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"iex_raw_dir": str(tmp_path / "raw_iex")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    exit_code = scanner.main()

    assert exit_code == 0
    assert captured["symbols"] == universe.DEFAULT_CANDIDATE_POOL
    assert captured["timeframe"] == "1Day"


def test_main_returns_nonzero_exit_code_when_nothing_succeeded(tmp_path, monkeypatch):
    def _fake_scan_symbols(symbols, timeframe, start, end, config, logger, batch_size=scanner.DEFAULT_BATCH_SIZE, **kwargs):
        return scanner.ScanReport(timeframe=timeframe, requested=list(symbols), succeeded=[], started_at=base.utcnow(), finished_at=base.utcnow())

    monkeypatch.setattr(scanner, "scan_symbols", _fake_scan_symbols)
    monkeypatch.setattr(sys, "argv", ["alpaca_scanner.py", "AAPL"])
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: {"data": {"iex_raw_dir": str(tmp_path / "raw_iex")}, "logging": {"log_dir": str(tmp_path / "logs")}})

    assert scanner.main() == 1


# --- iex_cache_freshness_report (Sprint 3 Task A1: dashboard data freshness) -----------


def test_freshness_report_is_all_empty_when_the_cache_dir_does_not_exist(tmp_path):
    config = _config(tmp_path)
    report = scanner.iex_cache_freshness_report(config, LOGGER)
    assert report == {"stale": [], "check_failed": [], "ok": [], "cached_file_count": 0}


def test_freshness_report_is_all_empty_when_the_cache_dir_is_empty(tmp_path):
    config = _config(tmp_path)
    scanner.iex_raw_dir(config).mkdir(parents=True)
    report = scanner.iex_cache_freshness_report(config, LOGGER)
    assert report == {"stale": [], "check_failed": [], "ok": [], "cached_file_count": 0}


def test_freshness_report_flags_a_fresh_file_as_ok(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 4, tzinfo=timezone.utc)  # 1 day after the cached bar - within the default 3-day threshold
    scanner._save_symbol_bars("AAPL", "1Day", _bars_df("AAPL"), config)  # latest bar 2026-01-03

    report = scanner.iex_cache_freshness_report(config, LOGGER, now=now)

    assert report["cached_file_count"] == 1
    assert report["ok"] == ["AAPL_1Day"]
    assert report["stale"] == [] and report["check_failed"] == []


def test_freshness_report_flags_an_old_file_as_stale(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)  # ~2 months after the cached bar
    scanner._save_symbol_bars("AAPL", "1Day", _bars_df("AAPL"), config)

    report = scanner.iex_cache_freshness_report(config, LOGGER, now=now)

    assert report["stale"] == ["AAPL_1Day"]
    assert report["ok"] == []


def test_freshness_report_respects_the_configured_staleness_threshold(tmp_path):
    config = _config(tmp_path)
    config["data"]["max_bar_age_days_warning"] = 30  # much more lenient than the default
    now = datetime(2026, 1, 20, tzinfo=timezone.utc)  # ~17 days after the cached bar - stale by default (3d), not with this override
    scanner._save_symbol_bars("AAPL", "1Day", _bars_df("AAPL"), config)

    report = scanner.iex_cache_freshness_report(config, LOGGER, now=now)

    assert report["ok"] == ["AAPL_1Day"]
    assert report["stale"] == []


def test_freshness_report_flags_an_unreadable_file_as_check_failed_not_fresh(tmp_path):
    config = _config(tmp_path)
    out_dir = scanner.iex_raw_dir(config)
    out_dir.mkdir(parents=True)
    (out_dir / "CORRUPT_1Day.csv").write_text("not,valid,csv,\x00\x01", encoding="utf-8")

    report = scanner.iex_cache_freshness_report(config, LOGGER)

    assert report["cached_file_count"] == 1
    assert report["check_failed"] == ["CORRUPT_1Day"]
    assert report["ok"] == [] and report["stale"] == []


def test_freshness_report_flags_an_empty_file_as_check_failed(tmp_path):
    config = _config(tmp_path)
    out_dir = scanner.iex_raw_dir(config)
    out_dir.mkdir(parents=True)
    (out_dir / "EMPTY_1Day.csv").write_text("open,high,low,close,volume\n", encoding="utf-8")

    report = scanner.iex_cache_freshness_report(config, LOGGER)

    assert report["check_failed"] == ["EMPTY_1Day"]


def test_freshness_report_covers_multiple_cached_files_independently(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 4, tzinfo=timezone.utc)
    scanner._save_symbol_bars("AAPL", "1Day", _bars_df("AAPL"), config)  # fresh
    old_df = _bars_df("MSFT")
    old_df.index = old_df.index - pd.Timedelta(days=365)
    scanner._save_symbol_bars("MSFT", "1Day", old_df, config)  # stale

    report = scanner.iex_cache_freshness_report(config, LOGGER, now=now)

    assert report["cached_file_count"] == 2
    assert report["ok"] == ["AAPL_1Day"]
    assert report["stale"] == ["MSFT_1Day"]
