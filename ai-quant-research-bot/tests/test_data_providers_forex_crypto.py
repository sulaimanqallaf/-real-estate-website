"""data_providers/{forex,crypto}_provider.py - interface-only extension
stubs (AI Quant Trading Platform sprint, deliverable A). The entire
point under test: these NEVER return STATUS_OK, because no real vendor
is wired up - a regression here would mean "fabricated OK data,"
exactly what base.py's contract forbids."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base, crypto_provider, forex_provider

LOGGER = logging.getLogger("test")


def test_forex_provider_is_never_configured_without_the_env_var(monkeypatch):
    monkeypatch.delenv("FOREX_DATA_API_KEY", raising=False)
    assert forex_provider.forex_configured() is False
    assert forex_provider.ForexProvider().is_configured() is False


def test_forex_fetch_ohlcv_never_returns_ok(monkeypatch):
    monkeypatch.delenv("FOREX_DATA_API_KEY", raising=False)
    result = forex_provider.fetch_ohlcv("EUR/USD", {}, LOGGER)
    assert result.status != base.STATUS_OK
    assert result.ok is False
    assert result.data is None


def test_forex_fetch_ohlcv_still_never_returns_ok_even_if_a_key_is_set(monkeypatch):
    """Proves there's no real vendor call lurking behind the gate - a
    key alone must never be enough to get fabricated OK data."""
    monkeypatch.setenv("FOREX_DATA_API_KEY", "fake-key-for-test")
    result = forex_provider.fetch_ohlcv("EUR/USD", {}, LOGGER)
    assert result.status != base.STATUS_OK


def test_forex_rejects_a_malformed_pair():
    result = forex_provider.fetch_ohlcv("NOTAPAIR", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_forex_is_valid_pair():
    assert forex_provider.is_valid_pair("EUR/USD") is True
    assert forex_provider.is_valid_pair("AAPL") is False


def test_crypto_provider_is_never_configured_without_the_env_var(monkeypatch):
    monkeypatch.delenv("CRYPTO_DATA_API_KEY", raising=False)
    assert crypto_provider.crypto_configured() is False
    assert crypto_provider.CryptoProvider().is_configured() is False


def test_crypto_fetch_ohlcv_never_returns_ok(monkeypatch):
    monkeypatch.delenv("CRYPTO_DATA_API_KEY", raising=False)
    result = crypto_provider.fetch_ohlcv("BTC/USD", {}, LOGGER)
    assert result.status != base.STATUS_OK
    assert result.ok is False


def test_crypto_fetch_ohlcv_still_never_returns_ok_even_if_a_key_is_set(monkeypatch):
    monkeypatch.setenv("CRYPTO_DATA_API_KEY", "fake-key-for-test")
    result = crypto_provider.fetch_ohlcv("BTC/USD", {}, LOGGER)
    assert result.status != base.STATUS_OK


def test_crypto_rejects_a_malformed_pair():
    result = crypto_provider.fetch_ohlcv("$", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_both_providers_satisfy_the_dataprovider_protocol_shape():
    for provider in (forex_provider.ForexProvider(), crypto_provider.CryptoProvider()):
        assert isinstance(provider.name, str) and provider.name
        assert isinstance(provider.is_configured(), bool)
