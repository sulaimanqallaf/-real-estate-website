"""data_providers/forex_provider.py (still an interface-only stub - the
point under test is that it NEVER returns STATUS_OK, since no real
vendor is wired up) and data_providers/crypto_provider.py (now a REAL
CCXT-backed implementation, Phase 6 - see that module's docstring for
why its gate is "is the isolated oss_quant venv set up," not an API
key). Crypto's real-network tests mock
`src.analytics.oss_quant_adapter` the same way
test_analytics_backtester_crosscheck.py does, so they run without the
real isolated venv or real network access."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import oss_quant_adapter
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


def test_crypto_provider_unavailable_when_the_oss_quant_venv_is_not_set_up(tmp_path):
    config = {"analytics": {"oss_quant": {"python_executable": str(tmp_path / "nonexistent" / "python")}}}
    assert crypto_provider.crypto_configured(config) is False
    result = crypto_provider.fetch_ohlcv("BTC/USD", config, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.ok is False


def test_crypto_provider_configured_check_uses_default_venv_path_with_no_config():
    """CryptoProvider.is_configured() takes no args (the shared
    DataProvider protocol) - it must check the real default venv path,
    not silently report False/True regardless of what's on disk."""
    from pathlib import Path

    expected = oss_quant_adapter.resolve_python_executable({}).exists()
    assert crypto_provider.CryptoProvider().is_configured() == expected
    assert isinstance(expected, bool)


def test_crypto_rejects_a_malformed_pair():
    result = crypto_provider.fetch_ohlcv("$", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_crypto_fetch_ohlcv_reports_a_real_network_failure_as_provider_error(monkeypatch):
    """A task-level ccxt failure (network/exchange error) must be
    STATUS_ERROR - distinct from STATUS_UNAVAILABLE - never fabricated
    as STATUS_OK."""
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        crypto_provider.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {"ok": False, "error": "binance GET https://api.binance.com/api/v3/exchangeInfo"},
    )
    result = crypto_provider.fetch_ohlcv("BTC/USDT", {}, LOGGER)
    assert result.status == base.STATUS_ERROR
    assert "binance" in result.error


def test_crypto_fetch_ohlcv_returns_none_response_as_provider_error(monkeypatch):
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "run_task", lambda *a, **k: None)
    result = crypto_provider.fetch_ohlcv("BTC/USDT", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_crypto_fetch_ohlcv_unavailable_on_zero_rows(monkeypatch):
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        crypto_provider.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {"ok": True, "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1d", "rows": []},
    )
    result = crypto_provider.fetch_ohlcv("BTC/USDT", {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE


def test_crypto_fetch_ohlcv_returns_real_rows_shaped_like_fetch_symbol_history(monkeypatch):
    fake_rows = [
        {"timestamp_ms": 1700000000000, "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1234.5},
        {"timestamp_ms": 1700086400000, "open": 103.0, "high": 108.0, "low": 102.0, "close": 107.0, "volume": 2345.6},
    ]
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        crypto_provider.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {"ok": True, "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1d", "rows": fake_rows},
    )

    result = crypto_provider.fetch_ohlcv("BTC/USDT", {}, LOGGER)

    assert result.status == base.STATUS_OK
    assert result.ok is True
    df = result.data
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "date"
    assert len(df) == 2
    assert df["close"].iloc[-1] == 107.0
    assert result.available_at is not None


def test_crypto_fetch_ohlcv_passes_configured_exchange_timeframe_and_limit(monkeypatch):
    captured = {}

    def fake_run_task(task, payload, cfg, logger, timeout_seconds=None):
        captured.update(payload)
        return {"ok": True, "rows": []}

    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(crypto_provider.oss_quant_adapter, "run_task", fake_run_task)

    config = {"providers": {"crypto": {"exchange": "coinbase", "timeframe": "4h", "limit": 50}}}
    crypto_provider.fetch_ohlcv("ETH/USD", config, LOGGER)

    assert captured == {"exchange": "coinbase", "symbol": "ETH/USD", "timeframe": "4h", "limit": 50}


def test_both_providers_satisfy_the_dataprovider_protocol_shape():
    for provider in (forex_provider.ForexProvider(), crypto_provider.CryptoProvider()):
        assert isinstance(provider.name, str) and provider.name
        assert isinstance(provider.is_configured(), bool)
