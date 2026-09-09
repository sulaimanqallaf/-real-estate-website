"""`OrderIntent` (Phase 7 Part E) and the order lifecycle state machine
(Part G). An `OrderIntent` is immutable and MUST pass `validate_intent()`
before it is ever handed to a `Broker` - `order_manager.py` is the only
caller of `submit_order`, and it always validates first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"  # only ever used for a protective/exit child order in this phase - never a new short entry

ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_STOP = "STOP"

ALLOWED_ORDER_TYPES = (ORDER_TYPE_LIMIT, ORDER_TYPE_STOP)

# Lifecycle states (Part G).
STATE_CREATED = "CREATED"
STATE_SUBMITTED = "SUBMITTED"
STATE_ACKNOWLEDGED = "ACKNOWLEDGED"
STATE_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STATE_FILLED = "FILLED"
STATE_CANCEL_PENDING = "CANCEL_PENDING"
STATE_CANCELLED = "CANCELLED"
STATE_REJECTED = "REJECTED"
STATE_EXIT_PENDING = "EXIT_PENDING"
STATE_CLOSED = "CLOSED"
STATE_ERROR = "ERROR"

ALL_STATES = (
    STATE_CREATED, STATE_SUBMITTED, STATE_ACKNOWLEDGED, STATE_PARTIALLY_FILLED, STATE_FILLED,
    STATE_CANCEL_PENDING, STATE_CANCELLED, STATE_REJECTED, STATE_EXIT_PENDING, STATE_CLOSED, STATE_ERROR,
)

# Every ticker this system is ever allowed to trade must be a plain US
# equity/ETF symbol - no options root symbols, no futures, no forex pairs.
# This is a coarse syntactic guard (Part E: "no options contract"), not a
# substitute for the universe already enforced by config.tickers/
# strategy_universe - it exists so an OrderIntent can never even be
# CONSTRUCTED for something that looks like a derivative.
_MAX_TICKER_LEN = 6


class OrderIntentValidationError(ValueError):
    pass


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    ticker: str
    side: str
    quantity: int
    order_type: str
    entry_price: float
    stop_loss: float
    target_price: float
    strategy: str
    signal_score: int
    quant_score: float | None
    risk_amount: float
    created_at: datetime
    trade_id: str | None = None  # links back to the paper_trades.csv / execution journal row, once known
    regime: str | None = None
    account_mode_at_creation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def new_intent_id() -> str:
    return f"intent_{uuid.uuid4().hex[:16]}"


def build_order_intent(
    ticker: str,
    position: dict[str, Any],
    strategy: str,
    signal_score: int,
    quant_score: float | None,
    regime: str | None = None,
    account_mode_at_creation: str | None = None,
    trade_id: str | None = None,
) -> OrderIntent:
    """Build an `OrderIntent` from an already fully-sized position dict
    (the SAME shape `risk_manager`/`portfolio_risk`/`quant_agent` already
    produce - `entry`/`stop_loss`/`target`/`shares`/`dollar_risk`) - this
    function does not compute or adjust sizing itself, it only packages an
    already-decided size into the immutable, broker-facing shape."""
    return OrderIntent(
        intent_id=new_intent_id(),
        ticker=ticker,
        side=SIDE_BUY,
        quantity=int(position["shares"]),
        order_type=ORDER_TYPE_LIMIT,
        entry_price=float(position["entry"]),
        stop_loss=float(position["stop_loss"]),
        target_price=float(position["target"]),
        strategy=strategy,
        signal_score=signal_score,
        quant_score=quant_score,
        risk_amount=float(position["dollar_risk"]),
        created_at=datetime.now(timezone.utc),
        trade_id=trade_id,
        regime=regime,
        account_mode_at_creation=account_mode_at_creation,
    )


def validate_intent(intent: OrderIntent, config: dict[str, Any], allowed_tickers: set[str] | None = None) -> list[str]:
    """Returns a list of validation failure reasons - empty means valid.
    Never raises; `order_manager.py` decides what to do with a non-empty
    list (always: refuse submission)."""
    errors: list[str] = []

    if intent.quantity <= 0:
        errors.append("quantity must be > 0")

    if intent.side != SIDE_BUY:
        errors.append(f"long-only: side must be '{SIDE_BUY}', got '{intent.side}' - no short entries are ever constructed")

    if not intent.ticker or len(intent.ticker) > _MAX_TICKER_LEN or not intent.ticker.isalnum():
        errors.append(f"ticker '{intent.ticker}' does not look like a plain equity/ETF symbol")

    if allowed_tickers is not None and intent.ticker not in allowed_tickers:
        errors.append(f"ticker '{intent.ticker}' is not in the configured tradeable universe")

    if intent.order_type not in ALLOWED_ORDER_TYPES:
        errors.append(f"order_type '{intent.order_type}' is not one of the allowed types {ALLOWED_ORDER_TYPES}")

    if not (intent.stop_loss < intent.entry_price < intent.target_price):
        errors.append(
            f"entry/stop/target are not internally consistent for a long position "
            f"(stop {intent.stop_loss} < entry {intent.entry_price} < target {intent.target_price} required)"
        )

    max_risk_dollars = config.get("execution_risk", {}).get("max_risk_per_trade_pct")
    account_equity = config.get("risk", {}).get("account_equity")
    if max_risk_dollars is not None and account_equity:
        limit_dollars = max_risk_dollars * account_equity
        if intent.risk_amount > limit_dollars + 1e-6:
            errors.append(f"risk_amount {intent.risk_amount:.2f} exceeds max_risk_per_trade_pct limit ({limit_dollars:.2f})")

    if intent.account_mode_at_creation is not None and intent.account_mode_at_creation != "PAPER":
        errors.append(f"account_mode_at_creation is '{intent.account_mode_at_creation}', not PAPER - refusing to validate a non-paper intent")

    return errors
