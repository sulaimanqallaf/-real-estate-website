"""DRY_RUN order review (Phase 7 continuation). Builds and validates an
`OrderIntent` for an eligible candidate and reports exactly what WOULD be
submitted if `execution.mode` were `IBKR_PAPER` - without ever placing
it. **This module never calls, imports, or references `Broker.submit_
order()` - there is no broker parameter anywhere in this file, only an
`OrderManager` used strictly for its `is_duplicate()` check (which never
touches `self.broker` either).** `submit_order()` stays
`NotImplementedError` in `ibkr_client.py`; this module exists entirely so
a candidate's would-be order can be inspected before that is ever wired.

Runs the exact same checks the real execution paths (`approval_bridge.
execute_approved_trade`, the AUTO_EXECUTE path in `main.py`) already use:
`order_state.validate_intent()` (quantity, long-only, ticker shape,
order type, entry/stop/target consistency, risk-per-trade limit, PAPER
account only), `pretrade_checks.check_trading_hours()`, `pretrade_checks.
check_slippage()`, and `OrderManager.is_duplicate()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import order_manager, order_state, pretrade_checks


@dataclass(frozen=True)
class OrderReview:
    ticker: str
    would_submit: bool
    intent: order_state.OrderIntent | None
    reasons: list[str] = field(default_factory=list)


def _now_ny() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return datetime.now()


def review_order_intent(
    record: dict[str, Any],
    config: dict[str, Any],
    manager: order_manager.OrderManager | None,
    current_market_price: float | None,
    now: datetime | None = None,
    trade_id: str | None = None,
) -> OrderReview:
    """`record` is the same shape `report_writer.final_position()`/the
    AUTO_EXECUTE path's `pending_record` already use: `symbol`,
    `strategy`, `score`, `entry`, `stop_loss`, `target`, `shares`,
    `dollar_risk`, optional `regime_at_entry`/`quant_score`.

    `manager` is optional - pass `None` to skip the duplicate check
    entirely (e.g. a one-off manual review with no journal to compare
    against); when given, only its `is_duplicate()` method is ever
    called, never anything that would touch a broker."""
    intent = order_state.build_order_intent(
        ticker=record["symbol"],
        position={
            "entry": record["entry"], "stop_loss": record["stop_loss"], "target": record["target"],
            "shares": record["shares"], "dollar_risk": record["dollar_risk"],
        },
        strategy=record["strategy"],
        signal_score=record["score"],
        quant_score=record.get("quant_score"),
        regime=record.get("regime_at_entry"),
        account_mode_at_creation="PAPER",
        trade_id=trade_id,
    )

    reasons: list[str] = list(order_state.validate_intent(intent, config))

    hours_reason = pretrade_checks.check_trading_hours(now or _now_ny(), config)
    if hours_reason:
        reasons.append(hours_reason)

    slippage_reason = pretrade_checks.check_slippage(intent.entry_price, current_market_price, config)
    if slippage_reason:
        reasons.append(slippage_reason)

    if manager is not None and manager.is_duplicate(intent):
        reasons.append("DUPLICATE_INTENT")

    deduped = list(dict.fromkeys(reasons))
    return OrderReview(ticker=record["symbol"], would_submit=not deduped, intent=intent, reasons=deduped)


def format_order_review(review: OrderReview) -> str:
    """Multi-line, log/print-safe description of exactly what order WOULD
    be submitted, or why it would not be. Never fabricates a broker order
    id or any field the system doesn't actually know - there is nothing
    to fabricate since no broker was ever contacted."""
    if review.intent is None:
        return f"{review.ticker}: could not construct an order intent."

    intent = review.intent
    lines = [
        f"DRY_RUN ORDER REVIEW - {review.ticker}",
        f"  Would submit: {'YES' if review.would_submit else 'NO'}",
        f"  Side: {intent.side}",
        f"  Quantity: {intent.quantity}",
        f"  Order type: {intent.order_type}",
        f"  Entry (limit): {intent.entry_price}",
        f"  Stop loss: {intent.stop_loss}",
        f"  Target: {intent.target_price}",
        f"  Strategy: {intent.strategy}",
        f"  Risk amount: ${intent.risk_amount:.2f}",
        f"  Account mode at creation: {intent.account_mode_at_creation}",
    ]
    if review.reasons:
        lines.append(f"  Blocked reasons: {', '.join(review.reasons)}")
    lines.append("  NOTE: DRY_RUN review only - no broker was contacted, no order was placed.")
    return "\n".join(lines)


def main() -> int:
    """`python -m src.execution.order_review` - an ad-hoc, standalone
    review of ONE hypothetical order, for manual testing without running
    the full daily research pipeline. Never contacts a broker; never
    places an order; does not read or care about `execution.mode`."""
    import argparse

    from ..utils import load_config, load_env

    parser = argparse.ArgumentParser(description="Review (never submit) a hypothetical DRY_RUN order.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--strategy", default="Manual Review")
    parser.add_argument("--score", type=int, default=90)
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--stop-loss", type=float, required=True)
    parser.add_argument("--target", type=float, required=True)
    parser.add_argument("--shares", type=int, required=True)
    parser.add_argument("--dollar-risk", type=float, required=True)
    parser.add_argument("--current-price", type=float, default=None, help="Defaults to --entry if omitted (no slippage to evaluate).")
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    record = {
        "symbol": args.ticker, "strategy": args.strategy, "score": args.score,
        "entry": args.entry, "stop_loss": args.stop_loss, "target": args.target,
        "shares": args.shares, "dollar_risk": args.dollar_risk,
    }
    review = review_order_intent(record, config, manager=None, current_market_price=args.current_price if args.current_price is not None else args.entry)
    print(format_order_review(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
