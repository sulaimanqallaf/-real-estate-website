"""Evaluates the multi-agent research layer against the EXISTING AI Quant
strategy stack (`quant_agent.py`) - the "go/no-go comparison report"
GitHub Issue #1's integration-plan deliverable asks for. Purely a
read-only report over `memory.py` + `ml/decision_ledger.py`; it has no
write path back into either and cannot influence anything.
"""

from __future__ import annotations

from typing import Any

from ..ml import decision_ledger
from . import memory
from .reflection import _realized_direction
from .schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL

_QUANT_DECISION_TO_ACTION = {
    "ELIGIBLE": ACTION_BUY,
    "WEAK": ACTION_HOLD,
    "NOT_ELIGIBLE": ACTION_SELL,
}


def compare_against_quant_agent(config: dict[str, Any], since: str | None = None, until: str | None = None) -> dict[str, Any]:
    memory_db = memory.resolve_db_path(config)
    ledger_db = decision_ledger.resolve_db_path(config)
    rows = memory.query_assessments(memory_db, since=since, until=until)

    compared = [r for r in rows if r.get("quant_agent_decision") in _QUANT_DECISION_TO_ACTION]
    agreement_count = sum(1 for r in compared if _QUANT_DECISION_TO_ACTION[r["quant_agent_decision"]] == r["action"])

    agent_correct = 0
    quant_correct = 0
    resolved = 0
    for row in rows:
        try:
            ledger_rows = decision_ledger.query_decisions(ledger_db, ticker=row["ticker"], only_with_outcome=True)
        except Exception:  # noqa: BLE001 - a report must never crash on a bad row
            continue
        matching = [r for r in ledger_rows if r.get("report_date") == row["report_date"]]
        if not matching:
            continue
        direction = _realized_direction(matching[0].get("pnl_pct"))
        if direction is None or row["action"] == ACTION_HOLD:
            continue
        resolved += 1
        if row["action"] == direction:
            agent_correct += 1
        quant_action = _QUANT_DECISION_TO_ACTION.get(row.get("quant_agent_decision"))
        if quant_action is not None and quant_action != ACTION_HOLD and quant_action == direction:
            quant_correct += 1

    return {
        "total_assessments": len(rows),
        "compared_with_quant_agent": len(compared),
        "agreement_count": agreement_count,
        "agreement_pct": round(100.0 * agreement_count / len(compared), 1) if compared else None,
        "resolved_directional_trades": resolved,
        "agent_correct": agent_correct,
        "agent_accuracy_pct": round(100.0 * agent_correct / resolved, 1) if resolved else None,
        "quant_agent_correct": quant_correct,
        "quant_agent_accuracy_pct": round(100.0 * quant_correct / resolved, 1) if resolved else None,
    }


def format_comparison_report(summary: dict[str, Any]) -> str:
    lines = ["🔬 Multi-Agent Research Layer vs. AI Quant Strategies (SHADOW MODE)", ""]
    lines.append(f"Assessments recorded: {summary['total_assessments']}")
    if summary["compared_with_quant_agent"]:
        lines.append(
            f"Agreement with quant_agent decision: {summary['agreement_count']}/{summary['compared_with_quant_agent']} "
            f"({summary['agreement_pct']}%)"
        )
    else:
        lines.append("Agreement with quant_agent decision: insufficient sample.")

    if summary["resolved_directional_trades"]:
        lines.append(
            f"\nOn {summary['resolved_directional_trades']} resolved directional trade(s):\n"
            f"  - Research layer agreed with the realized outcome {summary['agent_correct']} times "
            f"({summary['agent_accuracy_pct']}%)\n"
            f"  - Existing quant_agent agreed with the realized outcome {summary['quant_agent_correct']} times "
            f"({summary['quant_agent_accuracy_pct']}%)"
        )
    else:
        lines.append("\nNo resolved directional trades to score accuracy against yet.")

    lines.append(
        "\nShadow mode: these recommendations have NOT influenced any PAPER trading decision. "
        "No veto/reduce-only gate is enabled yet."
    )
    return "\n".join(lines)
