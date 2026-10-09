"""Forex data provider - INTERFACE ONLY, no real feed wired up.

AI Quant Trading Platform sprint, deliverable A: "extensible forex...
data interfaces." This module exists to prove the extension point on
`base.ProviderResult`/`DataProvider` works for a non-equity asset class
without pretending there's a real feed behind it - see
`docs/platform/BLOCKERS.md` item 3 for what a real integration would
actually need (a forex-capable data vendor and, for execution, a
broker that trades forex - IBKR itself does, but `execution/
order_state.py`'s `OrderIntent` is deliberately scoped to equity/ETF
today, and widening that is a separate, reviewed safety decision, not
something this module does on its own).

Gated entirely on `FOREX_DATA_API_KEY` (unset by default, same
convention as `macro_provider.py`'s `FRED_API_KEY`). With no key
configured - which is every deployment today - every function here
returns `base.unavailable()`. There is no code path in this module
that can return `STATUS_OK` without a real key AND a real HTTP call
succeeding; nothing here fabricates a quote.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from . import base

SOURCE_FOREX = "forex"

# EUR/USD, GBP/JPY, etc - three letters, slash, three letters. Validated so a
# caller gets a clear "not a forex pair" error instead of this provider
# silently treating an equity ticker as one.
_FOREX_PAIR_RE = re.compile(r"^[A-Z]{3}/[A-Z]{3}$")


def forex_configured() -> bool:
    return bool(os.environ.get("FOREX_DATA_API_KEY"))


class ForexProvider:
    """Satisfies `data_providers.base.DataProvider`."""

    name = SOURCE_FOREX

    def is_configured(self) -> bool:
        return forex_configured()


def is_valid_pair(symbol: str) -> bool:
    return bool(_FOREX_PAIR_RE.match(symbol))


def fetch_ohlcv(symbol: str, config: dict[str, Any], logger: logging.Logger) -> base.ProviderResult[Any]:
    """Same shape `data_collector.fetch_symbol_history()` would need to
    match for the research pipeline to treat a forex pair like any
    other instrument - returns `base.unavailable()` until a real
    vendor is wired up. Never raises, never fabricates a bar."""
    if not is_valid_pair(symbol):
        return base.provider_error(SOURCE_FOREX, f"{symbol!r} is not a recognized forex pair (expected e.g. 'EUR/USD')")
    if not forex_configured():
        return base.unavailable(SOURCE_FOREX, "FOREX_DATA_API_KEY not configured - no forex data vendor is wired up yet (interface only)")
    # No real vendor call exists yet - reaching here would require a future,
    # reviewed change that actually implements one. Until then this is
    # unreachable in practice because forex_configured() is always False.
    logger.warning("ForexProvider.fetch_ohlcv(%s) called with a configured key but no real vendor implementation exists yet.", symbol)
    return base.unavailable(SOURCE_FOREX, "No forex data vendor implementation exists yet")
