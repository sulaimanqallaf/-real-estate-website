"""Routes an approved Telegram candidate into the execution layer (Phase 7
Part R), and the AUTO_EXECUTE path (Part S) that skips approval entirely
when both `autonomous_paper.enabled` and `auto_execute.enabled` are true.

**Never trusts stale approval state.** By the time a human taps "Approve
Paper Trade," the record in `pending_approvals.json` may be hours old.
`execute_approved_trade()` re-checks everything that can legitimately have
changed since then - account mode, circuit breakers, current market price
vs. the signal price, portfolio exposure, duplicate/conflicting orders,
market hours, and data freshness - before ever calling
`OrderManager.submit_entry()`. It does NOT re-run the full regime/ML
pipeline (that's still the same decision the record already captured); see
module docstring below for exactly which gates are re-verified and why
those are the ones that can actually go stale.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import circuit_breaker, execution_policy, order_manager, order_state, pretrade_checks
from .broker import ACCOUNT_MODE_PAPER, Broker


def _now_ny() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return datetime.now()


def execute_approved_trade(
    record: dict[str, Any],
    config: dict[str, Any],
    broker: Broker,
    manager: order_manager.OrderManager,
    current_market_price: float | None,
    logger: logging.Logger,
    trade_id: str | None = None,
) -> dict[str, Any]:
    """`record` is a `pending_approvals.json` entry (see `paper_trades.
    pending_record_from_entry`) after `paper_trades.process_decision()`
    already recorded the APPROVED decision to `paper_trades.csv` - this
    function is strictly ADDITIONAL: it never changes that CSV record, it
    only decides whether to also place a broker order for it. A refusal
    here still leaves the trade correctly recorded as an internal
    simulated paper position (unchanged Phase 3 behavior) - it just never
    reaches IBKR."""
    reasons: list[str] = []

    account = None
    try:
        account = broker.account_summary()
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"ACCOUNT_MODE_UNVERIFIED: {exc}")
    if account is not None and account.account_mode != ACCOUNT_MODE_PAPER:
        reasons.append("LIVE_ACCOUNT_BLOCKED")

    if broker.connection_state() != "CONNECTED":
        reasons.append("BROKER_DISCONNECTED")

    breaker_result = circuit_breaker.check_all(config, account=account, connection_state=broker.connection_state())
    reasons.extend(breaker_result.tripped)

    hours_reason = pretrade_checks.check_trading_hours(_now_ny(), config)
    if hours_reason:
        reasons.append(hours_reason)

    slippage_reason = pretrade_checks.check_slippage(record["entry"], current_market_price, config)
    if slippage_reason:
        reasons.append(slippage_reason)

    quantity, size_reason = execution_policy.compute_broker_constrained_quantity(
        record["shares"], record["entry"], account.available_funds if account else None, config
    )
    if size_reason:
        reasons.append(size_reason)

    if reasons:
        deduped = list(dict.fromkeys(reasons))  # multiple independent checks can legitimately agree - report each reason once
        logger.warning("Execution-time re-check blocked %s: %s", record["symbol"], "; ".join(deduped))
        return {"executed": False, "reasons": deduped}

    intent = order_state.build_order_intent(
        ticker=record["symbol"],
        position={"entry": record["entry"], "stop_loss": record["stop_loss"], "target": record["target"], "shares": quantity, "dollar_risk": record["dollar_risk"]},
        strategy=record["strategy"],
        signal_score=record["score"],
        quant_score=None,
        regime=record.get("regime_at_entry"),
        account_mode_at_creation=account.account_mode if account else None,
        trade_id=trade_id,
    )

    if manager.is_duplicate(intent):
        return {"executed": False, "reasons": ["DUPLICATE_INTENT"]}

    managed = manager.submit_entry(intent)
    executed = managed.state not in (order_state.STATE_ERROR, order_state.STATE_REJECTED)
    if not executed:
        return {"executed": False, "reasons": [managed.rejection_reason or "ORDER_MANAGER_ERROR"], "managed": managed}
    return {"executed": True, "managed": managed, "intent": intent}


def format_auto_execution_notice(record: dict[str, Any], managed: order_manager.ManagedOrder, account_risk_pct: float | None) -> str:
    """Part S: auto execution must never be silent - this is the standing
    Telegram notice sent every time an AUTO_EXECUTE candidate skips
    approval and goes straight to the broker."""
    risk_line = f"{account_risk_pct * 100:.2f}%" if account_risk_pct is not None else "N/A"
    return (
        "🤖 AUTO PAPER TRADE EXECUTED\n\n"
        f"{record['symbol']}\n"
        f"Strategy: {record['strategy']}\n"
        f"Reason: met every configured AUTO_EXECUTE criterion\n"
        f"Score: {record['score']}/100\n"
        f"Entry: {record['entry']}\n"
        f"Stop loss: {record['stop_loss']}\n"
        f"Target: {record['target']}\n"
        f"Quantity: {managed.intent.quantity}\n"
        f"Account risk: {risk_line}\n"
        f"Broker order ID: {managed.entry_broker_order_id}\n"
        f"Timestamp: {managed.intent.created_at.isoformat()}\n\n"
        "This is a PAPER trade only - no real money is at risk."
    )
