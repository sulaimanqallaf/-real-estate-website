"""Options/institutional order-flow provider interface.

No paid vendor is wired up yet - this module defines the normalized event
schema (`FlowEvent`) and a provider Protocol so a real feed (ORATS, Tradier,
Polygon, Unusual Whales, QuantData, ...) can be dropped in later without
changing anything downstream. `MockOptionsFlowProvider` (default, always
available, requires no credentials) always returns `unavailable()` - it never
fabricates a sweep/block/dark-pool print. `big_money.py` and the daily report
are written to treat "no flow provider configured" as a clean, honest gap in
data quality, never as "no unusual activity detected."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from . import base

SOURCE_OPTIONS_FLOW = "options_flow"

EVENT_CALL_SWEEP = "call_sweep"
EVENT_PUT_SWEEP = "put_sweep"
EVENT_BLOCK_TRADE = "block_trade"
EVENT_DARK_POOL_PRINT = "dark_pool_print"
EVENT_LARGE_PREMIUM_TRADE = "large_premium_trade"
EVENT_OPEN_INTEREST_CHANGE = "open_interest_change"

SIDE_ASK = "ask"
SIDE_BID = "bid"
SIDE_MID = "mid"
SIDE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FlowEvent:
    timestamp: datetime
    ticker: str
    event_type: str
    side: str
    premium: float | None
    strike: float | None
    expiry: str | None
    call_put: str | None  # "call" | "put" | None (e.g. for a dark pool print)
    price: float | None
    size: float | None
    source: str
    available_at: datetime


class OptionsFlowProvider(Protocol):
    """Every concrete provider (mock or a future paid vendor) implements this
    shape. `fetch_events` never raises - a real implementation is expected to
    wrap its own network/parse errors into a `base.provider_error()` result,
    exactly like the SEC/FRED providers."""

    name: str

    def is_configured(self) -> bool: ...

    def fetch_events(self, ticker: str, config: dict[str, Any]) -> base.ProviderResult[list[FlowEvent]]: ...


class MockOptionsFlowProvider:
    """The default provider when no real options-flow vendor is configured.
    Always reports `unavailable` - deliberately never returns synthetic
    events dressed up as real ones, so a report/feature built on top of this
    can't mistake "no vendor" for "no unusual activity." Tests exercise the
    normalization path (`FlowEvent` construction, schema) via
    `normalize_raw_event()` directly with hand-built raw payloads instead."""

    name = "options_flow_mock"

    def is_configured(self) -> bool:
        return False

    def fetch_events(self, ticker: str, config: dict[str, Any]) -> base.ProviderResult[list[FlowEvent]]:
        return base.unavailable(SOURCE_OPTIONS_FLOW, "No options/institutional order-flow provider configured.")


def normalize_raw_event(raw: dict[str, Any], source: str, available_at: datetime | None = None) -> FlowEvent:
    """Build a normalized `FlowEvent` from a raw vendor payload shaped like
    the fields documented in this module's docstring. A future real provider
    should map its own response into this shape once, here, rather than
    leaking vendor-specific field names into `big_money.py` or the report."""
    timestamp = raw["timestamp"]
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    return FlowEvent(
        timestamp=timestamp,
        ticker=raw["ticker"],
        event_type=raw["event_type"],
        side=raw.get("side", SIDE_UNKNOWN),
        premium=raw.get("premium"),
        strike=raw.get("strike"),
        expiry=raw.get("expiry"),
        call_put=raw.get("call_put"),
        price=raw.get("price"),
        size=raw.get("size"),
        source=source,
        available_at=available_at or timestamp,
    )


def get_default_provider(config: dict[str, Any]) -> OptionsFlowProvider:
    """Config-driven provider selection point. Only the mock provider exists
    today; a future real vendor would be selected here based on
    `config["providers"]["options_flow"]` without changing any caller."""
    providers_cfg = config.get("providers", {}).get("options_flow", {})
    if not providers_cfg.get("enabled", False):
        return MockOptionsFlowProvider()
    # No real vendor wired up yet - falls back to the mock even if a caller
    # enables this in config, rather than pretending a vendor exists.
    return MockOptionsFlowProvider()
