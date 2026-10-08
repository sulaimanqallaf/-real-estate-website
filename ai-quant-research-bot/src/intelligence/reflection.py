"""Reflection on past agent assessments (TradingAgents-inspired
`reflection.py`), generated AFTER the fact once a real outcome exists -
never at assessment time, never used to retroactively edit the original
`AgentResearchAssessment` row (see `memory.record_reflection()`: it only
ever sets the one `reflection_note` field).

Joins `memory.py`'s assessments against `ml/decision_ledger.py`'s REAL
broker-paper/simulated outcomes, by `(ticker, report_date)` - a read-only
join across two intentionally separate databases (GitHub Issue #1
integration-plan point 7: never mix hypothetical-recommendation alpha
with realized trade P&L in one data model). An assessment with no
matching resolved outcome yet is left alone; it will be revisited next
run."""

from __future__ import annotations

import logging
from typing import Any

from ..ml import decision_ledger
from . import memory
from .schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL


def _realized_direction(pnl_pct: float | None) -> str | None:
    if pnl_pct is None:
        return None
    if pnl_pct > 0:
        return ACTION_BUY  # the realized (always-long) trade made money - a BUY stance would have agreed
    if pnl_pct < 0:
        return ACTION_SELL  # it lost money - a SELL/bearish stance would have agreed
    return None  # exact breakeven - not informative either way


def generate_reflections(config: dict[str, Any], logger: logging.Logger, since: str | None = None) -> list[dict[str, Any]]:
    """Returns the reflections written this call - `[]` if nothing new
    resolved. Never raises; every per-assessment step is isolated so one
    bad row can't stop the rest."""
    memory_db = memory.resolve_db_path(config)
    ledger_db = decision_ledger.resolve_db_path(config)

    assessments = memory.query_assessments(memory_db, since=since)
    written: list[dict[str, Any]] = []

    for row in assessments:
        if row.get("reflection_note"):
            continue  # already reflected on once - never overwritten (hindsight must not keep rewriting itself)
        if row["action"] == ACTION_HOLD:
            continue  # nothing directional to judge a HOLD against

        try:
            ledger_rows = decision_ledger.query_decisions(ledger_db, ticker=row["ticker"], only_with_outcome=True)
        except Exception as exc:  # noqa: BLE001 - a ledger read failure must never crash the reflection pass
            logger.warning("Reflection: could not read decision ledger for %s: %s", row["ticker"], exc)
            continue

        matching = [r for r in ledger_rows if r.get("report_date") == row["report_date"]]
        if not matching:
            continue  # not resolved yet - revisit on a later run

        outcome = matching[0]
        direction = _realized_direction(outcome.get("pnl_pct"))
        if direction is None:
            continue

        agreed = direction == row["action"]
        if agreed:
            note = (
                f"Correct: predicted {row['action']}; the realized {outcome.get('strategy', 'trade')} outcome "
                f"({outcome.get('outcome_status')}, pnl_pct={outcome.get('pnl_pct'):+.2%}) agreed."
            )
        else:
            note = (
                f"Missed: predicted {row['action']}, but the realized {outcome.get('strategy', 'trade')} outcome "
                f"({outcome.get('outcome_status')}, pnl_pct={outcome.get('pnl_pct'):+.2%}) went the other way. "
                f"Original thesis: {row['thesis']}"
            )

        try:
            memory.record_reflection(memory_db, row["assessment_id"], note)
            written.append({"ticker": row["ticker"], "report_date": row["report_date"], "agreed": agreed, "note": note})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reflection: could not record reflection for %s/%s: %s", row["ticker"], row["report_date"], exc)

    return written
