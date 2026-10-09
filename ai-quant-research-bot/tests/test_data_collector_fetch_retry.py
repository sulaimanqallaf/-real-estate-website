"""src/data_collector.py's fetch_symbol_history() - Sprint 3's retry
wiring specifically (real tenacity backoff around the yfinance call
only, never around the empty-dataframe "no data" check). Separate from
test_data_collector.py (which covers latest_bar_age_days() only)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import data_collector, reliability

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {"data": {"history_period": "1y", "interval": "1d", "raw_dir": str(tmp_path)}}


def _fake_history_df():
    return pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-01-01"], name="Date"),
    )


def test_recovers_after_a_transient_connection_error(tmp_path, monkeypatch):
    """Real wiring proof - uses the real default backoff, so this one
    test genuinely sleeps briefly."""
    calls = {"count": 0}

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ConnectionError("transient blip")
            return _fake_history_df()

    monkeypatch.setattr(data_collector.yf, "Ticker", _FakeTicker)
    df = data_collector.fetch_symbol_history("AMD", _config(tmp_path), LOGGER)

    assert calls["count"] == 2
    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_a_persistent_connection_error_still_raises_after_retrying(tmp_path, monkeypatch):
    """Retry disabled (not what this test is about) so a persistently
    flaky mock doesn't pay the real backoff on every run."""
    monkeypatch.setattr(reliability, "retrying", lambda *a, **k: (lambda f: f))

    class _AlwaysFailsTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            raise ConnectionError("persistent outage")

    monkeypatch.setattr(data_collector.yf, "Ticker", _AlwaysFailsTicker)
    with pytest.raises(ConnectionError, match="persistent outage"):
        data_collector.fetch_symbol_history("AMD", _config(tmp_path), LOGGER)


def test_an_empty_result_is_never_retried_it_is_a_real_no_data_outcome(tmp_path, monkeypatch):
    """The empty-dataframe check is OUTSIDE the retried call - an empty
    result (a legitimate "no data for this symbol," not a network
    blip) must raise on the FIRST attempt, never be retried."""
    calls = {"count": 0}

    class _EmptyTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            calls["count"] += 1
            return pd.DataFrame()

    monkeypatch.setattr(data_collector.yf, "Ticker", _EmptyTicker)
    with pytest.raises(ValueError, match="no price data"):
        data_collector.fetch_symbol_history("DELISTED", _config(tmp_path), LOGGER)
    assert calls["count"] == 1
