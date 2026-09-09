"""Portfolio performance analytics computed purely from CLOSED paper trades in
data/journal/paper_trades.csv. Never fabricates a percentage from an empty or
tiny sample - every returned block carries `has_data` and every group carries
its own `sample_size` so callers (report_writer.py) can decide what's worth
showing rather than trusting a number blindly.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import paper_trades
from .strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE

# Secondary/derived metrics (expectancy) are only computed once a group has at
# least this many closed trades - below that, a single trade's outcome would be
# presented as if it were a stable per-trade expectation, which it isn't.
MIN_TRADES_FOR_ADVANCED_STATS = 5


def _strategy_family(strategy_name: str) -> str:
    if strategy_name in (STRATEGY_NAME_SAFE, STRATEGY_NAME_AGGRESSIVE):
        return "Mean Reversion"
    return strategy_name


def _max_consecutive(is_win_sequence: list[bool]) -> tuple[int, int]:
    max_win_streak = max_loss_streak = current_win = current_loss = 0
    for win in is_win_sequence:
        if win:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        max_win_streak = max(max_win_streak, current_win)
        max_loss_streak = max(max_loss_streak, current_loss)
    return max_win_streak, max_loss_streak


def _split_open_closed(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = paper_trades.load_paper_trades_df(config)
    if df.empty:
        return df, df
    closed = df[df["status"] != "OPEN"].copy()
    for col in ("pnl_dollars", "pnl_pct", "holding_days", "entry_price", "position_size"):
        closed[col] = pd.to_numeric(closed[col], errors="coerce")
    return df, closed


def _basic_stats(closed: pd.DataFrame) -> dict[str, Any]:
    """Metrics from section 7, computed from an already-closed, non-empty group.
    Sort by exited_at first so consecutive win/loss streaks are chronological."""
    closed = closed.sort_values("exited_at")
    pnl_dollars = closed["pnl_dollars"]
    pnl_pct = closed["pnl_pct"]

    wins = closed[pnl_dollars > 0]
    losses = closed[pnl_dollars <= 0]

    win_sum = wins["pnl_dollars"].sum()
    loss_sum = losses["pnl_dollars"].sum()
    total_cost_basis = (closed["entry_price"] * closed["position_size"]).sum()

    max_win_streak, max_loss_streak = _max_consecutive(list(pnl_dollars > 0))

    return {
        "sample_size": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100.0, 2),
        "avg_win_pct": round(wins["pnl_pct"].mean(), 2) if len(wins) else None,
        "avg_loss_pct": round(losses["pnl_pct"].mean(), 2) if len(losses) else None,
        "profit_factor": round(win_sum / abs(loss_sum), 2) if loss_sum != 0 else None,
        "total_pnl_dollars": round(pnl_dollars.sum(), 2),
        "total_pnl_pct": round(pnl_dollars.sum() / total_cost_basis * 100.0, 2) if total_cost_basis else None,
        "avg_trade_pct": round(pnl_pct.mean(), 2),
        "best_trade_pct": round(pnl_pct.max(), 2),
        "worst_trade_pct": round(pnl_pct.min(), 2),
        "avg_holding_days": round(closed["holding_days"].mean(), 1),
        "max_consecutive_wins": max_win_streak,
        "max_consecutive_losses": max_loss_streak,
        "expectancy_per_trade_dollars": (
            round(pnl_dollars.mean(), 2) if len(closed) >= MIN_TRADES_FOR_ADVANCED_STATS else None
        ),
    }


def compute_portfolio_performance(config: dict[str, Any]) -> dict[str, Any]:
    """Whole-portfolio stats across every closed paper trade, regardless of
    strategy/mode/ticker. `has_data` is False whenever there are zero closed
    trades - callers must check it before trusting any other key."""
    df, closed = _split_open_closed(config)

    total_trades = len(df)
    open_trades = int((df["status"] == "OPEN").sum()) if not df.empty else 0

    if closed.empty:
        return {"has_data": False, "total_trades": total_trades, "open_trades": open_trades, "closed_trades": 0}

    return {
        "has_data": True,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": len(closed),
        **_basic_stats(closed),
    }


def compute_strategy_breakdown(config: dict[str, Any]) -> dict[str, Any]:
    """Grouped performance: by strategy family (Mean Reversion / Momentum
    Breakout / Trend Following), by Mean Reversion mode (Safe / Aggressive), and
    by ticker. A group only appears here if it has at least one closed trade -
    there is no fabricated zero-trade entry for a strategy that's never closed
    anything, so later code deciding "which strategies don't prove an edge yet"
    can tell "no data" apart from "proven bad" by checking membership, not a value.
    """
    _, closed = _split_open_closed(config)

    if closed.empty:
        return {"has_data": False, "by_strategy": {}, "by_mean_reversion_mode": {}, "by_ticker": {}}

    closed = closed.copy()
    closed["strategy_family"] = closed["strategy"].apply(_strategy_family)

    by_strategy = {family: _basic_stats(group) for family, group in closed.groupby("strategy_family")}

    mean_reversion_rows = closed[closed["strategy_family"] == "Mean Reversion"]
    by_mean_reversion_mode = (
        {mode: _basic_stats(group) for mode, group in mean_reversion_rows.groupby("mode")}
        if not mean_reversion_rows.empty
        else {}
    )

    by_ticker = {ticker: _basic_stats(group) for ticker, group in closed.groupby("ticker")}

    return {
        "has_data": True,
        "by_strategy": by_strategy,
        "by_mean_reversion_mode": by_mean_reversion_mode,
        "by_ticker": by_ticker,
    }
