"""Tests for src/data_providers/macro_provider.py: FRED-gated macro context,
clean unavailable behavior with no key, and point-in-time release-date
handling."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base, macro_provider as mp


def test_missing_fred_key_returns_unavailable_cleanly(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    result = mp.fetch_series_latest(mp.SERIES_FED_FUNDS_RATE)
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None


def test_fred_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert mp.fred_configured() is False
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    assert mp.fred_configured() is True


def test_parse_fred_observations_skips_missing_value_sentinel():
    payload = {
        "observations": [
            {"date": "2026-08-01", "value": ".", "realtime_start": "2026-08-05"},
            {"date": "2026-07-01", "value": "5.33", "realtime_start": "2026-07-05"},
        ]
    }
    observations = mp.parse_fred_observations(payload, "FEDFUNDS")
    assert len(observations) == 1
    assert observations[0].value == 5.33


def test_observation_available_at_prefers_release_date_over_observation_date():
    payload = {"observations": [{"date": "2026-06-30", "value": "3.1", "realtime_start": "2026-08-14"}]}
    observations = mp.parse_fred_observations(payload, "CPIAUCSL")
    obs = observations[0]
    assert obs.observation_date == date(2026, 6, 30)
    assert obs.available_at == date(2026, 8, 14)
    assert obs.available_at != obs.observation_date


def test_observation_falls_back_to_observation_date_when_no_release_date():
    payload = {"observations": [{"date": "2026-06-30", "value": "3.1"}]}
    observations = mp.parse_fred_observations(payload, "CPIAUCSL")
    assert observations[0].release_date is None
    assert observations[0].available_at == date(2026, 6, 30)


def test_yield_curve_spread_computed_correctly():
    ten = mp.MacroObservation("DGS10", 4.2, date(2026, 8, 1), date(2026, 8, 1))
    two = mp.MacroObservation("DGS2", 3.6, date(2026, 8, 1), date(2026, 8, 1))
    assert mp.compute_yield_curve_spread(ten, two) == 0.6


def test_yield_curve_spread_none_when_either_leg_missing():
    ten = mp.MacroObservation("DGS10", 4.2, date(2026, 8, 1), date(2026, 8, 1))
    assert mp.compute_yield_curve_spread(ten, None) is None
    assert mp.compute_yield_curve_spread(None, None) is None


def test_stale_or_all_missing_observations_does_not_crash_and_reports_unavailable(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key-for-test")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"observations": [{"date": "2026-08-01", "value": "."}]}

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
    result = mp.fetch_series_latest(mp.SERIES_CPI)
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None


def test_network_failure_returns_provider_error_not_an_exception(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key-for-test")

    import requests

    def _boom(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(requests, "get", _boom)
    result = mp.fetch_series_latest(mp.SERIES_FED_FUNDS_RATE)
    assert result.status == base.STATUS_ERROR
    assert "simulated network failure" in result.error
