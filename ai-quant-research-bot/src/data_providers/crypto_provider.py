"""Crypto OHLCV data provider - real implementation via CCXT (AI Quant
Trading Platform sprint, Phase 6: "integrate eligible free historical
data; investigate CCXT for future public crypto-market data
collection... keep forex/crypto TRADING disabled").

Unlike `forex_provider.py` (still interface-only - no free public
forex OHLCV vendor is wired up), crypto public OHLCV genuinely does not
need a paid vendor key: CCXT's `fetch_ohlcv` on a public exchange
endpoint (e.g. Binance's `/api/v3/klines`) is unauthenticated market
data. The real gate here is therefore NOT an API key - it's whether
the isolated `.venvs/oss_quant/` environment (where `ccxt` lives, see
`docs/platform/OSS_INTEGRATION_AUDIT.md` - CCXT is Apache-2.0/MIT,
INTEGRATE verdict, isolated the same way as quantstats/vectorbt because
it is a separate, optional, subprocess-bridged dependency, not because
of a license conflict) has actually been set up
(`scripts/setup_oss_quant_env.sh`). Without it, every function here
returns `base.unavailable()`, same honesty contract as before.

**This module fetches DATA only.** It has no connection to
`execution/order_state.py`'s `OrderIntent` (still hard-scoped to
equity/ETF) or any broker - crypto TRADING stays fully disabled
regardless of whether this provider can fetch a candle. Nothing in
`src/` calls this module from a live pipeline today; it exists to be
called explicitly (tests, a future research hypothesis, the
dashboard's data-provider-health panel).

**Real network access to a crypto exchange is blocked in this
project's CI/dev sandbox** (the egress proxy returns a 403
"organization policy" CONNECT-tunnel rejection on every crypto exchange
host tried during the OSS audit - `api.binance.com`, `api.coinbase.com`
- confirmed directly, not assumed). `fetch_ohlcv` still makes the real
call every time (there is no fallback/fixture path baked into this
module) - in that sandbox it will come back as a real, honest
`STATUS_ERROR` (a genuine `ccxt.NetworkError`), never a fabricated
`STATUS_OK`. A deployment with real outbound network access should see
`STATUS_OK` with real candles.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

from . import base
from ..analytics import oss_quant_adapter

SOURCE_CRYPTO = "crypto"
DEFAULT_EXCHANGE = "binance"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_LIMIT = 500

# BTC/USD, ETH/USDT, etc - 2-10 alnum chars, slash, 2-10 alnum chars.
_CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z0-9]{2,10}$")


def _provider_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Reads from `config.providers.crypto` - the SAME top-level
    `providers:` block `settings.yaml` already uses for sec/fred/
    options_flow, not a separate, inconsistent key."""
    return config.get("providers", {}).get("crypto", {})


def crypto_configured(config: dict[str, Any] | None = None) -> bool:
    """True once the isolated oss_quant venv (where ccxt lives) exists -
    see this module's docstring for why that, rather than an API key,
    is the real gate for crypto's PUBLIC OHLCV data."""
    return oss_quant_adapter.is_available(config or {})


class CryptoProvider:
    """Satisfies `data_providers.base.DataProvider`."""

    name = SOURCE_CRYPTO

    def is_configured(self) -> bool:
        return crypto_configured()


def is_valid_pair(symbol: str) -> bool:
    return bool(_CRYPTO_PAIR_RE.match(symbol))


def fetch_ohlcv(symbol: str, config: dict[str, Any], logger: logging.Logger) -> base.ProviderResult[Any]:
    """Real CCXT OHLCV fetch, subprocess-bridged into the isolated
    `.venvs/oss_quant/` environment via
    `tools/oss_quant_runner.py`'s `ccxt_ohlcv` task (`enableRateLimit`
    is always on - see that task's implementation). Returns a
    DataFrame in the SAME shape `data_collector.fetch_symbol_history()`
    returns (index named `"date"`, columns `open`/`high`/`low`/
    `close`/`volume`) so the research pipeline can treat a crypto pair
    like any other instrument's price history. Never fabricates a
    candle: `STATUS_UNAVAILABLE` if the venv isn't set up,
    `STATUS_ERROR` if the real network call fails, `STATUS_OK` only
    with real rows from the exchange.
    """
    if not is_valid_pair(symbol):
        return base.provider_error(SOURCE_CRYPTO, f"{symbol!r} is not a recognized crypto pair (expected e.g. 'BTC/USD')")
    if not crypto_configured(config):
        return base.unavailable(SOURCE_CRYPTO, "ccxt not installed (run scripts/setup_oss_quant_env.sh) - no crypto vendor wired up yet")

    settings = _provider_settings(config)
    exchange = settings.get("exchange", DEFAULT_EXCHANGE)
    timeframe = settings.get("timeframe", DEFAULT_TIMEFRAME)
    limit = int(settings.get("limit", DEFAULT_LIMIT))

    result = oss_quant_adapter.run_task(
        "ccxt_ohlcv", {"exchange": exchange, "symbol": symbol, "timeframe": timeframe, "limit": limit}, config, logger,
    )
    if result is None:
        return base.provider_error(SOURCE_CRYPTO, "ccxt subprocess call did not return a response (see logs)")
    if not result.get("ok"):
        return base.provider_error(SOURCE_CRYPTO, result.get("error") or "ccxt fetch_ohlcv failed")

    rows = result.get("rows") or []
    if not rows:
        return base.unavailable(SOURCE_CRYPTO, f"{exchange} returned zero {timeframe} candles for {symbol!r}")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
    df.index.name = "date"

    return base.ok(
        SOURCE_CRYPTO, df,
        available_at=df.index[-1].to_pydatetime(),
        freshness=f"{timeframe}_bars_from_{exchange}",
    )
