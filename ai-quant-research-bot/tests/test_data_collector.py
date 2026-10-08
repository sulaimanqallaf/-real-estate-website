"""GitHub Issue #1: "never assume delayed market data is real-time."
`latest_bar_age_days()` is the execution layer's one way to know its
cached daily history is stale BEFORE submitting an order against it -
these are unit tests for that function alone; its wiring into
`circuit_breaker.check_all()` via `approval_bridge.execute_approved_trade()`
is covered end-to-end in test_execution_full_lifecycle.py.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import data_collector

logger = logging.getLogger("test")


def _config(raw_dir):
    return {"data": {"raw_dir": str(raw_dir)}}


def _write_raw_csv(raw_dir, symbol, last_date):
    raw_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.DatetimeIndex([last_date], name="date"),
    )
    df.to_csv(raw_dir / f"{symbol}_daily.csv")


def test_returns_none_when_raw_dir_is_not_configured_at_all():
    assert data_collector.latest_bar_age_days("AMD", {}, logger) is None


def test_returns_none_when_no_cached_file_exists_yet(tmp_path):
    config = _config(tmp_path / "raw")
    assert data_collector.latest_bar_age_days("AMD", config, logger) is None


def test_returns_zero_for_a_bar_dated_today(tmp_path):
    raw_dir = tmp_path / "raw"
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
    _write_raw_csv(raw_dir, "AMD", now.date())
    config = _config(raw_dir)
    assert data_collector.latest_bar_age_days("AMD", config, logger, now=now) == 0


def test_returns_a_positive_age_for_a_stale_bar(tmp_path):
    raw_dir = tmp_path / "raw"
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)
    _write_raw_csv(raw_dir, "AMD", (now - timedelta(days=5)).date())
    config = _config(raw_dir)
    assert data_collector.latest_bar_age_days("AMD", config, logger, now=now) == 5


def test_returns_none_rather_than_raising_on_a_corrupt_cache_file(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "AMD_daily.csv").write_text("not,a,valid,csv\n???", encoding="utf-8")
    config = _config(raw_dir)
    assert data_collector.latest_bar_age_days("AMD", config, logger) is None
