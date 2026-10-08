"""Telegram-ready text for `OrderManager` lifecycle events (Phase 7
continuation - "Telegram should become primarily a monitoring/control
interface rather than a required approval step"). Pure formatting only -
no Telegram import here, no broker access - so it's trivially unit
testable and reusable by both the AUTO_EXECUTE path (`main.py`) and the
continuous monitor (`position_monitor.py`), which wire these strings to
`telegram_bot.send_telegram_message` themselves.

`format_lifecycle_notice()` returns `None` for an event that already has
its own, better-tailored notice elsewhere (e.g. "acknowledged" - the
AUTO_EXECUTE notice and the manual-approval Telegram reply both already
cover that moment with more context than a generic one-liner could) -
callers must skip sending anything when it returns `None`.
"""

from __future__ import annotations

from typing import Any


def format_lifecycle_notice(event: str, managed: Any, extra: dict[str, Any]) -> str | None:
    intent = managed.intent
    ticker = intent.ticker

    if event == "rejected":
        return (
            f"❌ {ticker} PAPER order REJECTED by broker\n"
            f"Reason: {managed.rejection_reason}\n"
            "No position was opened."
        )

    if event == "submission_error":
        return (
            f"⚠️ {ticker} PAPER order submission ERROR (requires attention)\n"
            f"Reason: {managed.rejection_reason}\n"
            "No position was opened - this is not a broker rejection, something else went wrong "
            "(connection, timeout, or an internal error). Check approval_listener.log / position_monitor.log."
        )

    if event == "fill":
        newly_filled = extra.get("newly_filled", 0.0)
        is_full = managed.filled_quantity >= intent.quantity
        label = "FULL FILL" if is_full else "PARTIAL FILL"
        return (
            f"✅ {ticker} {label}\n"
            f"Filled: {managed.filled_quantity}/{intent.quantity} shares (this fill: {newly_filled}) @ avg {managed.avg_fill_price}\n"
            f"Broker order: {managed.entry_broker_order_id}"
        )

    if event == "protection_synced":
        protected_qty = extra.get("protected_quantity", managed.filled_quantity)
        action = "created" if extra.get("_protection_just_created") else "resized"
        return (
            f"🛡️ {ticker} protective stop/target {action}\n"
            f"Protected quantity: {protected_qty}\n"
            f"Stop order: {managed.stop_broker_order_id} @ {intent.stop_loss}\n"
            f"Target order: {managed.target_broker_order_id} @ {intent.target_price}"
        )

    return None  # "submitting"/"acknowledged"/"cancelled"/"closed"/"validation_failed"/"duplicate_blocked" already have their own, more specific notices (or need none) elsewhere


def format_circuit_breaker_notice(newly_tripped: list[str]) -> str:
    return (
        "🚨 CIRCUIT BREAKER ACTIVATED - new entries blocked\n"
        f"Tripped: {', '.join(newly_tripped)}\n"
        "Existing protected positions continue to be managed. Use /status for current state; /resume clears "
        "ONLY the manual kill switch, never a reconciliation failure or hard breaker."
    )


def format_reconciliation_failure_notice(summary: str) -> str:
    return f"🚨 RECONCILIATION FAILURE (requires attention) - new entries blocked\n{summary}"
