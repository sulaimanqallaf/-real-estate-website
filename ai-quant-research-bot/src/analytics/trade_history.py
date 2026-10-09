"""Shared "real, closed trades with a known outcome" accessor - the one
place `performance_report.py`, `monte_carlo.py`, and `regime_breakdown.py`
all source from, so "which rows count as a real closed trade" (outcome
recorded, NOT_TRADED excluded, pnl_pct and exited_at both present) is
defined exactly once rather than copy-pasted three times.

Sources from `ml/decision_ledger.py` rather than `paper_trades.csv`
because the ledger is where real commission/slippage/regime actually
land - see `performance_report.py`'s module docstring for the full
reasoning (unchanged by this extraction).
"""

from __future__ import annotations

from typing import Any


def closed_trade_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    from ..ml import decision_ledger

    db_path = decision_ledger.resolve_db_path(config)
    rows = decision_ledger.query_decisions(db_path, only_with_outcome=True)
    return [
        r for r in rows
        if r.get("outcome_status") not in (None, decision_ledger.OUTCOME_NOT_TRADED)
        and r.get("pnl_pct") is not None
        and r.get("exited_at") is not None
    ]
