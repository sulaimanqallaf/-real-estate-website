"""Paper trade lifecycle: tracks OPEN paper positions through to TARGET_HIT /
STOPPED / TIME_EXIT, using only daily OHLCV bars. Still zero broker execution -
this only ever reads price history and rewrites data/journal/paper_trades.csv.

No lookahead: for each OPEN trade, only bars strictly AFTER `opened_at` are ever
examined, walked in chronological order, and each bar's outcome is decided using
only that bar's own high/low/close - never anything from a later bar.

Same-bar stop-vs-target conflict: if a single day's low <= stop_loss AND high >=
target_price, this module assumes the stop was hit first. This is not a new
rule invented for this module - it's the same conservative assumption
backtester.py's `_run_strategy_backtest` already uses (see that module's
docstring: "Exits check stop before target if both would trigger intraday on the
same bar (conservative assumption)"). Reusing it here keeps the codebase's answer
to "which one wins" consistent everywhere it's asked, rather than having two
different answers depending on whether a trade was simulated or is live.

Idempotency: check_open_trades() only ever evaluates rows whose status is still
"OPEN". The moment a trade closes, its status becomes a terminal value
(TARGET_HIT / STOPPED / TIME_EXIT) and it is never evaluated again by any later
call, in this run or a future one - so running the daily job twice on the same
data, or on the same calendar day, produces the same closed set both times with
no duplicate P&L, no duplicate journal rows, and no duplicate exit notification
(main.py only sends one notification per trade actually returned by this call).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from . import paper_trades

_EXIT_ICONS = {"TARGET_HIT": "🎯", "STOPPED": "🛑", "TIME_EXIT": "⏱"}
_EXIT_TITLES = {
    "TARGET_HIT": "Paper Target Hit",
    "STOPPED": "Paper Stop Hit",
    "TIME_EXIT": "Paper Time Exit",
}


def _pnl(entry_price: float, exit_price: float, position_size: float) -> tuple[float, float]:
    pnl_dollars = (exit_price - entry_price) * position_size
    pnl_pct = (exit_price - entry_price) / entry_price * 100.0 if entry_price else 0.0
    return round(pnl_dollars, 2), round(pnl_pct, 2)


def _close_trade(trade: dict[str, Any], exit_price: float, exit_date, exit_reason: str, status: str) -> dict[str, Any]:
    opened_at = datetime.strptime(str(trade["opened_at"]), "%Y-%m-%d").date()
    exit_date_only = exit_date.date() if hasattr(exit_date, "date") else exit_date
    holding_days = (exit_date_only - opened_at).days

    pnl_dollars, pnl_pct = _pnl(float(trade["entry_price"]), exit_price, float(trade["position_size"]))

    closed = dict(trade)
    closed.update(
        {
            "status": status,
            "exit_price": round(exit_price, 2),
            "exited_at": exit_date_only.isoformat(),
            "exit_reason": exit_reason,
            "pnl_dollars": pnl_dollars,
            "pnl_pct": pnl_pct,
            "holding_days": holding_days,
        }
    )
    return closed


def evaluate_open_trade(trade: dict[str, Any], price_df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any] | None:
    """Walk every daily bar strictly after `opened_at`, oldest first, and return
    an updated (closed) trade dict at the first exit condition met - or None if
    the trade is still open as of the latest available bar."""
    max_holding_days = config["paper_trading"]["max_holding_days"]
    opened_at = datetime.strptime(str(trade["opened_at"]), "%Y-%m-%d").date()
    stop_loss = float(trade["stop_loss"])
    target_price = float(trade["target_price"])

    subsequent_bars = price_df[price_df.index.date > opened_at].sort_index()

    for bar_date, bar in subsequent_bars.iterrows():
        days_held = (bar_date.date() - opened_at).days

        if bar["low"] <= stop_loss:
            return _close_trade(trade, stop_loss, bar_date, "Stop loss triggered", "STOPPED")
        if bar["high"] >= target_price:
            return _close_trade(trade, target_price, bar_date, "Target price reached", "TARGET_HIT")
        if days_held >= max_holding_days:
            return _close_trade(
                trade, float(bar["close"]), bar_date,
                f"Max holding period ({max_holding_days} days) reached", "TIME_EXIT",
            )

    return None


def check_open_trades(
    price_data: dict[str, pd.DataFrame], config: dict[str, Any], logger: logging.Logger
) -> list[dict[str, Any]]:
    """Evaluate every OPEN paper trade against freshly fetched daily bars.
    Returns only the trades that CLOSED during this call - never previously
    closed ones - so callers (main.py's exit-notification sender) can never
    double-notify. Rewrites paper_trades.csv once, only if something changed.
    """
    df = paper_trades.load_paper_trades_df(config)
    if df.empty:
        return []

    newly_closed: list[dict[str, Any]] = []
    updated_rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        trade = row.to_dict()

        if trade["status"] != "OPEN":
            updated_rows.append(trade)
            continue

        ticker = trade["ticker"]
        price_df = price_data.get(ticker)
        if price_df is None:
            logger.warning(
                "No fresh price data for %s - leaving paper trade %s OPEN this run.", ticker, trade["trade_id"]
            )
            updated_rows.append(trade)
            continue

        closed = evaluate_open_trade(trade, price_df, config)
        if closed is None:
            updated_rows.append(trade)
        else:
            updated_rows.append(closed)
            newly_closed.append(closed)
            logger.info(
                "Paper trade closed: %s %s status=%s pnl_pct=%s",
                closed["ticker"], closed["trade_id"], closed["status"], closed["pnl_pct"],
            )

    if newly_closed:
        paper_trades.save_paper_trades_df(pd.DataFrame(updated_rows, columns=df.columns), config)

    return newly_closed


def format_exit_notification(trade: dict[str, Any]) -> str:
    status = trade["status"]
    icon = _EXIT_ICONS.get(status, "ℹ️")
    title = _EXIT_TITLES.get(status, "Paper Trade Closed")
    pnl_dollars = float(trade["pnl_dollars"])
    pnl_pct = float(trade["pnl_pct"])
    sign = "+" if pnl_dollars >= 0 else ""

    return (
        f"{icon} {title}\n\n"
        f"{trade['ticker']}\n"
        f"Strategy: {trade['strategy']} ({trade['mode']})\n"
        f"Entry: {trade['entry_price']}\n"
        f"Exit: {trade['exit_price']}\n"
        f"P&L: {sign}{pnl_pct:.2f}% ({sign}${pnl_dollars:.2f})\n"
        f"Holding days: {trade['holding_days']}\n"
        f"Exit reason: {trade['exit_reason']}\n\n"
        f"(Paper trade only - no real order was placed or closed.)"
    )
