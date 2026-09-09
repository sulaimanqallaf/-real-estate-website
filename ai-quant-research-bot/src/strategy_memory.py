"""Strategy performance memory (Phase 6 Parts Q/R): unconditioned and
regime/mode/ticker/sector-conditioned historical edge, computed purely from
CLOSED paper trades (`data/journal/paper_trades.csv`).

Reuses `performance_tracker.py`'s read path and basic-stats computation
rather than re-deriving win rate/profit factor/expectancy logic - this
module only adds the GROUPING (by strategy, and optionally also by the
market regime active when the trade was approved - see
`paper_trades.PAPER_TRADE_COLUMNS`'s `regime_at_entry` column, a Phase 6
addition) on top of what `performance_tracker.py` already computes per
group.

**Never claims an edge below a minimum sample size** (Part Q: "Do not claim
edge when sample size is too small") - `StrategyEdge.has_sufficient_sample`
is `False` and every derived stat is `None` (not a fabricated number) until
enough trades exist for that specific group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import paper_trades, performance_tracker

# Stricter than performance_tracker.MIN_TRADES_FOR_ADVANCED_STATS (5) - this
# module feeds a decision-support signal (Quant Agent), not just a displayed
# stat, so it holds itself to a higher bar before claiming a strategy "has an
# edge" one way or the other.
MIN_SAMPLE_FOR_EDGE = 10
MIN_SAMPLE_FOR_CONDITIONAL_EDGE = 8


@dataclass(frozen=True)
class StrategyEdge:
    group_key: str
    sample_size: int
    has_sufficient_sample: bool
    win_rate_pct: float | None
    profit_factor: float | None
    expectancy_dollars: float | None
    avg_trade_pct: float | None

    @property
    def edge_direction(self) -> str:
        """"POSITIVE" / "NEGATIVE" / "INSUFFICIENT_SAMPLE" - never a
        fabricated direction when the sample is too small to trust."""
        if not self.has_sufficient_sample:
            return "INSUFFICIENT_SAMPLE"
        if self.expectancy_dollars is None:
            return "INSUFFICIENT_SAMPLE"
        return "POSITIVE" if self.expectancy_dollars > 0 else "NEGATIVE"


def _to_edge(key: str, stats: dict[str, Any], min_sample: int) -> StrategyEdge:
    n = stats.get("sample_size", 0)
    sufficient = n >= min_sample
    return StrategyEdge(
        group_key=key,
        sample_size=n,
        has_sufficient_sample=sufficient,
        win_rate_pct=stats.get("win_rate_pct") if sufficient else None,
        profit_factor=stats.get("profit_factor") if sufficient else None,
        expectancy_dollars=stats.get("expectancy_per_trade_dollars") if sufficient else None,
        avg_trade_pct=stats.get("avg_trade_pct") if sufficient else None,
    )


def compute_strategy_edge(config: dict[str, Any], min_sample: int = MIN_SAMPLE_FOR_EDGE) -> dict[str, StrategyEdge]:
    """Unconditioned, per-strategy-family historical edge (Part Q) - reuses
    `performance_tracker.compute_strategy_breakdown()`'s `by_strategy` group
    directly rather than re-deriving it."""
    breakdown = performance_tracker.compute_strategy_breakdown(config)
    by_strategy = breakdown.get("by_strategy", {})
    return {name: _to_edge(name, stats, min_sample) for name, stats in by_strategy.items()}


def compute_grouped_edge(
    config: dict[str, Any], group_by: list[str], min_sample: int = MIN_SAMPLE_FOR_CONDITIONAL_EDGE
) -> dict[str, StrategyEdge]:
    """General conditional grouping (Part R) - `group_by` any subset of
    `["strategy", "regime_at_entry", "mode", "ticker", "sector"]`. Rows
    missing any requested grouping column's value (most commonly
    `regime_at_entry` on trades recorded before that column existed) are
    excluded from that specific breakdown - never guessed."""
    df = paper_trades.load_paper_trades_df(config)
    if df.empty:
        return {}

    closed = df[df["status"] != "OPEN"].copy()
    if closed.empty:
        return {}

    if "sector" in group_by:
        closed["sector"] = closed["ticker"].map(lambda t: config.get("sector_map", {}).get(t, t))

    for col in group_by:
        if col not in closed.columns:
            return {}
        closed = closed[closed[col].notna() & (closed[col] != "")]
    if closed.empty:
        return {}

    for col in ("pnl_dollars", "pnl_pct", "holding_days", "entry_price", "position_size"):
        closed[col] = pd.to_numeric(closed[col], errors="coerce")

    results: dict[str, StrategyEdge] = {}
    for group_values, group_df in closed.groupby(group_by):
        if isinstance(group_values, tuple):
            key = "|".join(str(v) for v in group_values)
        else:
            key = str(group_values)
        stats = performance_tracker._basic_stats(group_df)
        results[key] = _to_edge(key, stats, min_sample)
    return results


def compute_regime_conditioned_strategy_edge(config: dict[str, Any], min_sample: int = MIN_SAMPLE_FOR_CONDITIONAL_EDGE) -> dict[str, StrategyEdge]:
    """Strategy x market-regime edge (Part R's headline example: "Momentum
    Breakout x BULL_TREND"). Only ever reflects trades recorded with
    `regime_at_entry` populated - i.e. approved after this column existed."""
    return compute_grouped_edge(config, ["strategy", "regime_at_entry"], min_sample)


def edge_for_strategy_in_regime(config: dict[str, Any], strategy: str, regime: str, min_sample: int = MIN_SAMPLE_FOR_CONDITIONAL_EDGE) -> StrategyEdge:
    """Convenience lookup for `quant_agent.py`: the specific edge for one
    strategy under one regime, or an explicit zero-sample `StrategyEdge`
    (never `None`, never a fabricated stat) if that combination has no
    closed-trade history yet."""
    grouped = compute_regime_conditioned_strategy_edge(config, min_sample)
    key = f"{strategy}|{regime}"
    if key in grouped:
        return grouped[key]
    return StrategyEdge(key, 0, False, None, None, None, None)
