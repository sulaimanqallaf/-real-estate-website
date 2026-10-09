"""Performance broken down by the market regime active when each trade
was decided (AI Quant Trading Platform sprint, Phase 4: "add... market-
regime breakdowns").

Uses the `regime` column `ml/decision_ledger.py` already records on
every decision (populated at decision time by whatever called
`record_decision(..., regime=...)` - see `src/market_regime.py` for
how that label itself is computed) - no new data source, no
re-classification after the fact. A trade whose decision row has no
regime recorded (`regime IS NULL`) is grouped under the honest label
`"UNKNOWN"` rather than dropped or guessed at.
"""

from __future__ import annotations

import logging
from typing import Any

from .trade_history import closed_trade_rows

UNKNOWN_REGIME_LABEL = "UNKNOWN"
MIN_TRADES_FOR_REGIME_ROW = 3


def build_regime_breakdown(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any] | None:
    """One row per regime label that has at least `MIN_TRADES_FOR_REGIME_ROW`
    closed trades - win rate, average/total P&L, and trade count, each
    computed directly from `decision_ledger` rows. Returns `None` (never
    a fabricated breakdown) if there are no closed trades at all."""
    closed = closed_trade_rows(config)
    if not closed:
        logger.info("Regime breakdown unavailable: no closed trades with a recorded outcome yet.")
        return None

    by_regime: dict[str, list[dict[str, Any]]] = {}
    for row in closed:
        regime = row.get("regime") or UNKNOWN_REGIME_LABEL
        by_regime.setdefault(regime, []).append(row)

    rows = []
    for regime, trades in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        if len(trades) < MIN_TRADES_FOR_REGIME_ROW:
            continue
        pnl_pcts = [float(t["pnl_pct"]) for t in trades]
        pnl_dollars = [float(t["pnl_dollars"]) for t in trades if t.get("pnl_dollars") is not None]
        wins = [p for p in pnl_pcts if p > 0]
        rows.append({
            "regime": regime,
            "trade_count": len(trades),
            "win_rate_pct": round(len(wins) / len(pnl_pcts) * 100.0, 2),
            "avg_pnl_pct": round(sum(pnl_pcts) / len(pnl_pcts), 4),
            "total_pnl_dollars": round(sum(pnl_dollars), 2) if pnl_dollars else None,
        })

    excluded = len(closed) - sum(r["trade_count"] for r in rows)
    return {
        "rows": rows,
        "total_closed_trades": len(closed),
        "excluded_insufficient_sample": excluded,
        "min_trades_per_regime_row": MIN_TRADES_FOR_REGIME_ROW,
    }


def format_regime_breakdown(breakdown: dict[str, Any] | None) -> str:
    if breakdown is None:
        return "Regime breakdown unavailable (no closed trades with a recorded outcome yet)."
    if not breakdown["rows"]:
        return f"Regime breakdown unavailable: no regime has >= {breakdown['min_trades_per_regime_row']} closed trades yet ({breakdown['total_closed_trades']} total closed trade(s))."

    lines = [f"Performance by market regime ({breakdown['total_closed_trades']} closed trades total):"]
    for row in breakdown["rows"]:
        lines.append(
            f"  {row['regime']}: {row['trade_count']} trade(s), {row['win_rate_pct']:.1f}% win rate, "
            f"avg {row['avg_pnl_pct']:+.2f}% / trade"
        )
    if breakdown["excluded_insufficient_sample"]:
        lines.append(f"  ({breakdown['excluded_insufficient_sample']} trade(s) in regimes with too small a sample to report)")
    return "\n".join(lines)
