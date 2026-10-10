"""src/data_providers/alpaca_provider.py - real Alpaca Basic/IEX market
data provider (Sprint 3, Free Real Market Data milestone). Mocked
`requests.get` responses match the REAL response schema verified
directly against alpaca-py's own source (see the module's docstring)
- never a guessed shape."""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

import pandas as pd
import pytest
import requests

from src.data_providers import alpaca_provider as ap
from src.data_providers import base

LOGGER = logging.getLogger("test")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _disable_retry_backoff(monkeypatch):
    """Most of these tests aren't about retry timing (reliability.py's
    own suite covers that) - disabling it keeps a persistent-failure
    test from paying real backoff."""
    from src import reliability

    monkeypatch.setattr(ap.reliability, "retrying", lambda *a, **k: (lambda f: f))


# --- configuration gating ------------------------------------------------------------


def test_alpaca_configured_requires_both_env_vars(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert ap.alpaca_configured() is False

    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    assert ap.alpaca_configured() is False  # secret still missing

    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    assert ap.alpaca_configured() is True


def test_provider_protocol_shape():
    provider = ap.AlpacaProvider()
    assert provider.name == "alpaca_iex"
    assert isinstance(provider.is_configured(), bool)


def test_fetch_bars_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None


def test_fetch_bars_rejects_an_unsupported_timeframe(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    result = ap.fetch_bars(["AAPL"], "3Min", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_fetch_bars_rejects_an_empty_symbol_list(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    result = ap.fetch_bars([], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER)
    assert result.status == base.STATUS_ERROR


# --- real schema: single page, single symbol ------------------------------------------


def _configure_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "test-secret")


def test_fetch_bars_parses_the_real_verified_response_schema(monkeypatch):
    _configure_credentials(monkeypatch)
    captured_requests = []

    def fake_get(url, params=None, headers=None, timeout=None):
        captured_requests.append({"url": url, "params": dict(params), "headers": dict(headers)})
        return _FakeResponse({
            "bars": {
                "AAPL": [
                    {"t": "2026-01-02T05:00:00Z", "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0, "v": 1_000_000, "n": 5000, "vw": 100.4},
                    {"t": "2026-01-03T05:00:00Z", "o": 101.0, "h": 102.0, "l": 100.5, "c": 101.8, "v": 900_000, "n": 4800, "vw": 101.3},
                ]
            },
            "next_page_token": None,
        })

    monkeypatch.setattr(requests, "get", fake_get)

    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())

    assert result.status == base.STATUS_OK
    df = result.data["AAPL"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "trade_count", "vwap"]
    assert len(df) == 2
    assert df["close"].iloc[0] == 101.0
    assert df["trade_count"].iloc[0] == 5000
    assert df["vwap"].iloc[1] == 101.3
    assert df.index.tz is not None  # real UTC-aware timestamps, never naive

    # The ONE non-negotiable safety property: every real request must
    # ask for the free iex feed, never sip.
    assert len(captured_requests) == 1
    assert captured_requests[0]["params"]["feed"] == "iex"
    assert captured_requests[0]["headers"]["APCA-API-KEY-ID"] == "test-key-id"
    assert captured_requests[0]["headers"]["APCA-API-SECRET-KEY"] == "test-secret"
    assert captured_requests[0]["url"] == f"{ap.ALPACA_DATA_BASE_URL}{ap.BARS_ENDPOINT}"


def test_fetch_bars_missing_optional_fields_are_none_not_fabricated(monkeypatch):
    """A real bar missing n/vw (Alpaca's docs note these can be absent
    for some symbols/venues) must come back as None, never a guessed
    0 or an index/column error."""
    _configure_credentials(monkeypatch)

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse({"bars": {"AAPL": [{"t": "2026-01-02T05:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 500_000}]}, "next_page_token": None})

    monkeypatch.setattr(requests, "get", fake_get)
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())

    assert result.status == base.STATUS_OK
    df = result.data["AAPL"]
    assert df["trade_count"].iloc[0] is None or pd.isna(df["trade_count"].iloc[0])
    assert df["vwap"].iloc[0] is None or pd.isna(df["vwap"].iloc[0])


# --- pagination: the REAL next_page_token/page_token contract -------------------------


def test_fetch_bars_follows_pagination_until_next_page_token_is_null(monkeypatch):
    _configure_credentials(monkeypatch)
    pages = [
        {"bars": {"AAPL": [{"t": "2026-01-02T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": "page2token"},
        {"bars": {"AAPL": [{"t": "2026-01-03T05:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}]}, "next_page_token": None},
    ]
    call_count = {"n": 0}
    seen_page_tokens = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen_page_tokens.append(params.get("page_token"))
        page = pages[call_count["n"]]
        call_count["n"] += 1
        return _FakeResponse(page)

    monkeypatch.setattr(requests, "get", fake_get)
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())

    assert result.status == base.STATUS_OK
    assert len(result.data["AAPL"]) == 2  # both pages' bars merged
    assert seen_page_tokens == [None, "page2token"]  # first request has no page_token; second carries the prior response's next_page_token


def test_fetch_bars_stops_at_max_pages_defensively(monkeypatch):
    """A misbehaving/malicious response that always returns a
    next_page_token must never cause an infinite loop."""
    _configure_credentials(monkeypatch)
    call_count = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        call_count["n"] += 1
        return _FakeResponse({"bars": {"AAPL": [{"t": "2026-01-02T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": "always-more"})

    monkeypatch.setattr(requests, "get", fake_get)
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter(), max_pages=3)

    assert result.status == base.STATUS_OK
    assert call_count["n"] == 3


# --- multi-symbol split ----------------------------------------------------------------


def test_fetch_bars_splits_results_per_symbol(monkeypatch):
    _configure_credentials(monkeypatch)

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["symbols"] == "AAPL,MSFT"
        return _FakeResponse({
            "bars": {
                "AAPL": [{"t": "2026-01-02T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
                "MSFT": [{"t": "2026-01-02T05:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}],
            },
            "next_page_token": None,
        })

    monkeypatch.setattr(requests, "get", fake_get)
    result = ap.fetch_bars(["AAPL", "MSFT"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())

    assert set(result.data.keys()) == {"AAPL", "MSFT"}
    assert result.data["AAPL"]["close"].iloc[0] == 1
    assert result.data["MSFT"]["close"].iloc[0] == 2


def test_fetch_bars_unavailable_when_no_symbol_has_any_bars(monkeypatch):
    _configure_credentials(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"bars": {}, "next_page_token": None}))
    result = ap.fetch_bars(["DELISTED"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())
    assert result.status == base.STATUS_UNAVAILABLE


# --- network failure handling (retry already proven generically in test_reliability.py) --


def test_fetch_bars_network_failure_returns_provider_error(monkeypatch):
    _configure_credentials(monkeypatch)
    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("simulated outage")))
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())
    assert result.status == base.STATUS_ERROR
    assert "simulated outage" in result.error


def test_fetch_bars_recovers_from_a_transient_failure(monkeypatch):
    _configure_credentials(monkeypatch)
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("transient blip")
        return _FakeResponse({"bars": {"AAPL": [{"t": "2026-01-02T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": None})

    monkeypatch.setattr(requests, "get", fake_get)
    result = ap.fetch_bars(["AAPL"], "1Day", datetime(2026, 1, 1, tzinfo=timezone.utc), None, {}, LOGGER, rate_limiter=ap.RateLimiter())
    assert result.status == base.STATUS_OK
    assert calls["n"] == 2


# --- rate limiter: never exceed the real 200/min Basic-plan ceiling --------------------


def test_rate_limiter_does_not_sleep_when_under_budget(monkeypatch):
    limiter = ap.RateLimiter(max_per_minute=200)
    slept = {"called": False}
    monkeypatch.setattr(time, "sleep", lambda s: slept.__setitem__("called", True))

    for _ in range(50):
        limiter.wait_if_needed()

    assert slept["called"] is False


def test_rate_limiter_sleeps_once_the_per_minute_budget_is_exhausted(monkeypatch):
    limiter = ap.RateLimiter(max_per_minute=5)
    fake_now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: fake_now["t"])
    sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    for _ in range(5):
        limiter.wait_if_needed()
    assert sleep_calls == []  # exactly at budget, not over yet

    limiter.wait_if_needed()  # the 6th call within the same instant must wait
    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0
