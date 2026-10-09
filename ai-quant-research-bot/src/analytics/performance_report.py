"""Real paper-trading performance analytics, powered by QuantStats
(AI Quant Trading Platform sprint, Phase 4: "Sharpe, Sortino, max
drawdown, profit factor, expectancy, win rate, exposure and
transaction-cost attribution").

**Built from real `decision_ledger` rows only - never synthetic.**
Returns `None` (never a fabricated report) when there are fewer than
`MIN_TRADES_FOR_REPORT` outcome-recorded trades, or when the isolated
QuantStats venv hasn't been set up yet
(`scripts/setup_oss_quant_env.sh`) - both states are reported honestly
to the caller, not silently hidden behind a zeroed-out report.

Sources from `ml/decision_ledger.py` rather than `paper_trades.csv`
because the ledger is where real commission/slippage actually land -
`execution/learning_feedback.py`'s `check_exit_fills()` writes the
broker-reported commission there via `record_outcome(commission=...)`,
and `slippage_pct` is computed there too (actual vs signal entry
price) - `paper_trades.csv` has neither column.

**Per-trade returns, not daily equity-curve returns - labeled as such.**
QuantStats' stats functions are generic over any return-like series;
this module feeds them one return per CLOSED trade (indexed by its
exit date), which gives a per-trade risk/return profile - a different,
equally valid, but DISTINCT statistic from a daily-NAV Sharpe ratio.
Every report this produces says so explicitly (`"basis":
"per_trade_pnl_pct"`), so it is never mistaken for - or compared
apples-to-oranges against - a continuously-compounded daily return
series.
"""

from __future__ import annotations

import logging
from typing import Any

from . import oss_quant_adapter

MIN_TRADES_FOR_REPORT = 5


def _closed_trade_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    from ..ml import decision_ledger

    db_path = decision_ledger.resolve_db_path(config)
    rows = decision_ledger.query_decisions(db_path, only_with_outcome=True)
    return [
        r for r in rows
        if r.get("outcome_status") not in (None, decision_ledger.OUTCOME_NOT_TRADED)
        and r.get("pnl_pct") is not None
        and r.get("exited_at") is not None
    ]


def build_performance_report(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any] | None:
    closed = _closed_trade_rows(config)
    if len(closed) < MIN_TRADES_FOR_REPORT:
        logger.info(
            "Performance report unavailable: %d closed trade(s) with a recorded outcome, need at least %d.",
            len(closed), MIN_TRADES_FOR_REPORT,
        )
        return None

    if not oss_quant_adapter.is_available(config):
        logger.info("Performance report unavailable: OSS quant tooling not set up (run scripts/setup_oss_quant_env.sh).")
        return None

    # One return per closed trade, indexed by exit date - see module
    # docstring for why this is "per_trade_pnl_pct," never conflated
    # with a daily-equity-curve return series.
    returns_by_exit: dict[str, float] = {}
    for row in closed:
        exit_date = str(row["exited_at"])[:10]
        pnl_fraction = float(row["pnl_pct"]) / 100.0
        # Multiple trades can close the same day - compound them, the
        # same way QuantStats itself expects a single daily return.
        existing = returns_by_exit.get(exit_date)
        returns_by_exit[exit_date] = ((1 + existing) * (1 + pnl_fraction) - 1) if existing is not None else pnl_fraction

    result = oss_quant_adapter.run_task("performance_report", {"returns": returns_by_exit}, config, logger)
    if result is None or not result.get("ok"):
        logger.warning("Performance report task failed: %s", result.get("error") if result else "no response")
        return None

    metrics = result["metrics"]
    metrics["basis"] = "per_trade_pnl_pct"
    metrics["trade_count"] = len(closed)

    # Transaction-cost attribution: real commission/slippage, summed
    # directly from decision_ledger rows - never re-derived from a model.
    commissions = [r["commission"] for r in closed if r.get("commission") is not None]
    slippages = [r["slippage_pct"] for r in closed if r.get("slippage_pct") is not None]
    metrics["total_commission_usd"] = round(sum(commissions), 2) if commissions else None
    metrics["avg_slippage_pct"] = round(sum(slippages) / len(slippages), 4) if slippages else None

    return metrics


def format_performance_report(metrics: dict[str, Any] | None) -> str:
    if metrics is None:
        return "Performance report unavailable (not enough closed trades with a recorded outcome yet, or OSS quant tooling not set up)."

    lines = [
        f"Performance report (basis: {metrics['basis']}, {metrics['trade_count']} closed trades)",
        f"  Sharpe (per-trade):   {metrics['sharpe']:.3f}" if metrics.get("sharpe") is not None else "  Sharpe: unavailable",
        f"  Sortino (per-trade):  {metrics['sortino']:.3f}" if metrics.get("sortino") is not None else "  Sortino: unavailable",
        f"  Max drawdown:         {metrics['max_drawdown']:.2%}" if metrics.get("max_drawdown") is not None else "  Max drawdown: unavailable",
        f"  Profit factor:        {metrics['profit_factor']:.3f}" if metrics.get("profit_factor") is not None else "  Profit factor: unavailable",
        f"  Win rate:             {metrics['win_rate']:.2%}" if metrics.get("win_rate") is not None else "  Win rate: unavailable",
        f"  Exposure:             {metrics['exposure']:.2%}" if metrics.get("exposure") is not None else "  Exposure: unavailable",
    ]
    if metrics.get("total_commission_usd") is not None:
        lines.append(f"  Total commission:     ${metrics['total_commission_usd']:.2f}")
    if metrics.get("avg_slippage_pct") is not None:
        lines.append(f"  Avg slippage:         {metrics['avg_slippage_pct']:.4%}")
    return "\n".join(lines)
