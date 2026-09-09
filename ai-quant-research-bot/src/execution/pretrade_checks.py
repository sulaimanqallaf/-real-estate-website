"""Real-time, immediately-before-submission checks (Phase 7 Parts N/O/P):
trading hours, event-risk, and price/slippage. Each returns `None` (pass)
or a short reason string the caller treats as a hard block for a NEW entry
- none of these ever loosen risk to "catch" a trade; they only ever skip
one.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Any

REASON_OUTSIDE_TRADING_HOURS = "OUTSIDE_TRADING_HOURS"
REASON_EVENT_RISK_BLOCKED = "EVENT_RISK_BLOCKED"
REASON_PRICE_MOVED_TOO_FAR = "PRICE_MOVED_TOO_FAR"


def _parse_hhmm(value: str) -> dt_time:
    hour, minute = value.split(":")
    return dt_time(int(hour), int(minute))


def is_within_trading_hours(now: datetime, config: dict[str, Any]) -> bool:
    """Regular session only in V1 (Part N) - no pre-market/after-hours new
    entries. `now` MUST already be timezone-aware in
    `config.execution.trading_hours_timezone` (America/New_York by
    default) - this function does not itself perform timezone conversion,
    so DST is handled correctly by whatever produced `now` (Python's
    `zoneinfo`-aware datetimes handle DST transitions automatically)."""
    cfg = config.get("execution", {})
    start = _parse_hhmm(cfg.get("trading_hours_start", "09:30"))
    end = _parse_hhmm(cfg.get("trading_hours_end", "16:00"))
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return start <= now.time() <= end


def check_trading_hours(now: datetime, config: dict[str, Any]) -> str | None:
    return None if is_within_trading_hours(now, config) else REASON_OUTSIDE_TRADING_HOURS


def check_event_risk(ticker: str, event_flags: dict[str, Any] | None, config: dict[str, Any]) -> str | None:
    """Architecture for a configurable entry block around known high-risk
    events (Part O) - earnings day, a major macro release, extreme
    volatility. `event_flags` is caller-supplied (e.g.
    `{"earnings_today": True}`); with no data source wired up yet, this
    NEVER invents an event status - `event_flags=None` or an empty dict
    always passes, exactly like every other "Data Unavailable, never
    fabricated" provider in this codebase. Kept deliberately modular
    (Part O: "do not make this a huge new data project") - a future phase
    can supply real `event_flags` without changing this function's
    contract."""
    if not config.get("execution", {}).get("event_risk", {}).get("enabled", False):
        return None
    if not event_flags:
        return None
    if event_flags.get("earnings_today") or event_flags.get("major_macro_release_today") or event_flags.get("extreme_volatility"):
        return REASON_EVENT_RISK_BLOCKED
    return None


def check_slippage(intent_entry_price: float, current_market_price: float | None, config: dict[str, Any]) -> str | None:
    """Compares the intended (signal-time) entry price against the CURRENT
    market price immediately before submission (Part P). `current_market_
    price=None` (data unavailable) is treated as a block, not a pass -
    submitting blind is never safer than skipping."""
    if current_market_price is None or current_market_price <= 0:
        return REASON_PRICE_MOVED_TOO_FAR
    max_slippage_pct = config.get("execution", {}).get("max_entry_slippage_pct", 0.003)
    moved_pct = abs(current_market_price - intent_entry_price) / intent_entry_price
    return REASON_PRICE_MOVED_TOO_FAR if moved_pct > max_slippage_pct else None
