"""Telegram command center (Phase 7 Part T): `/status`, `/positions`,
`/orders`, `/performance`, `/halt`, `/resume`.

These are plain TEXT messages (not the existing inline-keyboard button
presses `approval_listener.py` already handles) - `handle_text_command()`
is called from `approval_listener.handle_update()` for any `message` update
whose text matches one of the commands below, gated by the exact same
`chat_id` authorization check `approval_listener.py` already applies to
button presses. No secret (bot token, IBKR credentials, account id) is ever
included in a formatted reply.

**`/resume` never overrides a hard block.** `circuit_breaker.resume()`
only ever deletes the manual-halt file - it has no access to, and cannot
clear, a live-account block, an unverified account mode, a reconciliation
failure, or any other hard risk breaker (those are independently
re-evaluated from live state - broker connection, broker positions, P&L -
at the next real execution attempt, never from a flag this command could
flip). `format_resume_reply()` says so explicitly every time, rather than
implying "resumed" means "cleared to trade."
"""

from __future__ import annotations

import logging
from typing import Any

from . import circuit_breaker, order_manager

COMMANDS = ("/status", "/positions", "/orders", "/performance", "/halt", "/resume")

_TERMINAL_STATES = ("CANCELLED", "REJECTED", "CLOSED", "ERROR")


def parse_command(text: str) -> str | None:
    if not text:
        return None
    first_token = text.strip().split()[0].lower()
    return first_token if first_token in COMMANDS else None


def _latest_state_per_intent(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event") == "state" and row.get("intent_id"):
            latest[row["intent_id"]] = row
    return latest


def format_status_reply(config: dict[str, Any]) -> str:
    halted, reason = circuit_breaker.is_halted(config)
    execution_cfg = config.get("execution", {})
    autonomous_cfg = config.get("autonomous_paper", {})
    lines = [
        "📊 STATUS",
        f"Mode: {execution_cfg.get('mode', 'DRY_RUN')}",
        f"Autonomous paper trading: {'ENABLED' if autonomous_cfg.get('enabled', False) else 'disabled'}",
        f"Auto-execute: {'ENABLED' if autonomous_cfg.get('auto_execute', {}).get('enabled', False) else 'disabled'}",
        f"Manual halt: {'YES - ' + (reason or 'no reason given') if halted else 'no'}",
    ]
    return "\n".join(lines)


def format_positions_reply(config: dict[str, Any]) -> str:
    from .. import paper_trades

    df = paper_trades.load_paper_trades_df(config)
    open_rows = df[df["status"] == "OPEN"] if not df.empty else df
    if open_rows.empty:
        return "📈 POSITIONS\n\nNo open positions."
    lines = ["📈 POSITIONS"]
    for _, row in open_rows.iterrows():
        lines.append(f"{row['ticker']}: {row['position_size']} sh @ {row['entry_price']} (stop {row['stop_loss']}, target {row['target_price']})")
    return "\n".join(lines)


def format_orders_reply(config: dict[str, Any]) -> str:
    journal_path = config.get("execution", {}).get("journal_path", "data/journal/executions.jsonl")
    rows = order_manager.ExecutionJournal(journal_path).read_all()
    latest = _latest_state_per_intent(rows)
    active = [r for r in latest.values() if r.get("state") not in _TERMINAL_STATES]
    if not active:
        return "📋 ORDERS\n\nNo active orders."
    lines = ["📋 ORDERS"]
    for row in active:
        lines.append(f"{row['ticker']}: {row['state']} (filled {row.get('filled_quantity', 0)}, entry order {row.get('entry_broker_order_id')})")
    return "\n".join(lines)


def format_performance_reply(config: dict[str, Any]) -> str:
    from .. import performance_tracker

    perf = performance_tracker.compute_portfolio_performance(config)
    if not perf.get("has_data"):
        return f"📉 PERFORMANCE\n\nNo closed trades yet ({perf.get('open_trades', 0)} open)."
    lines = [
        "📉 PERFORMANCE",
        f"Closed trades: {perf['closed_trades']} (open: {perf['open_trades']})",
        f"Win rate: {perf.get('win_rate_pct', 'N/A')}%",
        f"Total P&L: ${perf.get('total_pnl_dollars', 'N/A')}",
        f"Expectancy/trade: ${perf.get('expectancy_per_trade_dollars', 'N/A')}",
    ]
    return "\n".join(lines)


def format_halt_reply(config: dict[str, Any], reason: str) -> str:
    circuit_breaker.halt(config, reason=reason)
    return "🛑 HALTED\n\nNew entries are blocked. Existing protected exits are unaffected. Send /resume to clear the manual halt."


def format_resume_reply(config: dict[str, Any]) -> str:
    circuit_breaker.resume(config)
    return (
        "▶️ Manual halt cleared.\n\n"
        "This does NOT override a live-account block, an unverified account "
        "mode, a reconciliation failure, or any other hard risk breaker - "
        "those are independently re-checked from live state on the next "
        "real execution attempt and cannot be cleared by this command."
    )


def handle_text_command(text: str, config: dict[str, Any], logger: logging.Logger, requested_by: str = "Telegram") -> str | None:
    """Returns the reply text for a recognized command, or None if `text`
    is not one of `COMMANDS` - callers should send nothing to Telegram in
    that case (it may be an ordinary, unrelated message)."""
    command = parse_command(text)
    if command is None:
        return None

    if command == "/status":
        return format_status_reply(config)
    if command == "/positions":
        return format_positions_reply(config)
    if command == "/orders":
        return format_orders_reply(config)
    if command == "/performance":
        return format_performance_reply(config)
    if command == "/halt":
        logger.warning("Manual halt requested via %s.", requested_by)
        return format_halt_reply(config, reason=f"Manual halt via {requested_by}")
    if command == "/resume":
        logger.warning("Manual resume requested via %s.", requested_by)
        return format_resume_reply(config)
    return None
