"""Crypto data provider - INTERFACE ONLY, no real feed wired up.

AI Quant Trading Platform sprint, deliverable A: "extensible... crypto
data interfaces." Mirrors `forex_provider.py` exactly - see that
module's docstring for the full rationale (same pattern, same honesty
about what is and isn't implemented). See
`docs/platform/BLOCKERS.md` item 3 for what a real integration needs.

Gated entirely on `CRYPTO_DATA_API_KEY` (unset by default). Every
function here returns `base.unavailable()` until a real vendor key is
configured AND a real implementation is added - neither exists today.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from . import base

SOURCE_CRYPTO = "crypto"

# BTC/USD, ETH/USDT, etc - 2-10 alnum chars, slash, 2-10 alnum chars.
_CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z0-9]{2,10}$")


def crypto_configured() -> bool:
    return bool(os.environ.get("CRYPTO_DATA_API_KEY"))


class CryptoProvider:
    """Satisfies `data_providers.base.DataProvider`."""

    name = SOURCE_CRYPTO

    def is_configured(self) -> bool:
        return crypto_configured()


def is_valid_pair(symbol: str) -> bool:
    return bool(_CRYPTO_PAIR_RE.match(symbol))


def fetch_ohlcv(symbol: str, config: dict[str, Any], logger: logging.Logger) -> base.ProviderResult[Any]:
    if not is_valid_pair(symbol):
        return base.provider_error(SOURCE_CRYPTO, f"{symbol!r} is not a recognized crypto pair (expected e.g. 'BTC/USD')")
    if not crypto_configured():
        return base.unavailable(SOURCE_CRYPTO, "CRYPTO_DATA_API_KEY not configured - no crypto data vendor is wired up yet (interface only)")
    logger.warning("CryptoProvider.fetch_ohlcv(%s) called with a configured key but no real vendor implementation exists yet.", symbol)
    return base.unavailable(SOURCE_CRYPTO, "No crypto data vendor implementation exists yet")
