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


def handle_manual_approval(
    action: str,
    symbol: str,
    report_date: str,
    config: dict[str, Any],
    logger: logging.Logger,
    broker: Broker | None = None,
) -> tuple[bool, str]:
    """Routes one Telegram button press through the correct path for the
    CURRENT `execution.mode` - the manual-approval continuation of Part R.

    - `action != "approve"`, or `execution.mode != "IBKR_PAPER"`:
      delegates straight to `paper_trades.process_decision()` - UNCHANGED
      Phase 3 behavior, and (hard rule) never touches a broker of any
      kind. This covers DRY_RUN entirely, and Reject/Watch Only always.
    - `action == "approve"` AND `execution.mode == "IBKR_PAPER"`: peeks
      at the pending record (`paper_trades.peek_pending_decision()` -
      read-only, nothing marked decided yet), then re-runs the SAME
      execution-time re-check chain `execute_approved_trade()` already
      uses for the AUTO_EXECUTE path - account mode, circuit breakers,
      trading hours, slippage against a freshly fetched quote, sizing,
      duplicate - BEFORE the pending record is ever marked APPROVED. A
      blocked re-check therefore leaves the record PENDING and
      retryable, never stuck "approved" with nothing actually submitted.
      Only once a broker order is confirmed submitted does this call
      `paper_trades.process_decision()` to mark it decided and write the
      SAME trade_id to `paper_trades.csv`.

    `execution.mode: IBKR_PAPER` with `autonomous_paper.enabled: false`
    (the default) IS this codebase's "manual paper execution" mode -
    there is no separate `IBKR_PAPER_MANUAL` config value; manual vs.
    autonomous is entirely decided by `autonomous_paper.enabled`/
    `auto_execute.enabled`, which this function never reads or changes.
    """
    from .. import data_collector, paper_trades
    from . import order_manager

    execution_mode = config.get("execution", {}).get("mode", "DRY_RUN")
    if action != "approve" or execution_mode != "IBKR_PAPER":
        return paper_trades.process_decision(action, symbol, report_date, config, logger)

    record, error = paper_trades.peek_pending_decision(action, symbol, report_date, config)
    if record is None:
        return False, error

    owns_broker = broker is None
    if owns_broker:
        from .ibkr_client import IBKRClient

        broker = IBKRClient()
        try:
            broker.connect()
        except Exception as exc:  # noqa: BLE001
            logger.error("Manual approval: could not connect to IBKR Paper for %s (%s).", symbol, exc)
            return False, f"{symbol}: could not connect to IBKR Paper - NOT submitted. The pending approval is unaffected; try again."

    try:
        journal_path = config.get("execution", {}).get("journal_path", "data/journal/executions.jsonl")
        journal = order_manager.ExecutionJournal(journal_path)
        manager = order_manager.OrderManager(broker, config, journal)
        manager.restore_from_journal_rows(journal.read_all())

        trade_id = paper_trades.generate_trade_id(symbol, report_date)
        current_price = data_collector.fetch_current_price(symbol, logger)
        result = execute_approved_trade(record, config, broker, manager, current_market_price=current_price, logger=logger, trade_id=trade_id)

        if not result.get("executed"):
            reasons = "; ".join(result.get("reasons", []))
            logger.warning("Manual approval blocked for %s: %s", symbol, reasons)
            return False, f"{symbol}: NOT submitted - {reasons}. The pending approval is unaffected; try again."

        success, message = paper_trades.process_decision("approve", symbol, report_date, config, logger, trade_id=trade_id)
        if not success:
            # Extremely unlikely (the exact same record just passed peek_
            # pending_decision()'s read-only checks moments ago) - but if
            # the pending state somehow changed in between (a concurrent
            # click), surface it rather than silently claiming success.
            logger.error(
                "Manual approval for %s: broker order %s was submitted but process_decision failed: %s",
                symbol, result["managed"].entry_broker_order_id, message,
            )
            return False, f"{symbol}: PAPER order was submitted to IBKR (order id {result['managed'].entry_broker_order_id}) but recording it failed - check logs: {message}"

        return True, f"{symbol}: PAPER order submitted to IBKR (order id {result['managed'].entry_broker_order_id})."
    finally:
        if owns_broker:
            from ..utils import safe_run

            safe_run(logger, "broker disconnect", broker.disconnect)


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
