"""Three-way comparison: upstream TradingAgents vs. this project's own
deterministic multi-agent engine vs. the existing `quant_agent.py`
strategies, against REAL resolved outcomes in `ml/decision_ledger.py`.
GitHub Issue #1 requirements 10/11: "store its predictions separately and
compare performance... evaluate forward performance, costs, latency and
decision quality before recommending whether to use its signals."

Kept as its own module rather than extending `intelligence/evaluation.py`
(the deterministic engine's own comparison report) - per requirement 1,
that module is left completely unmodified."""

from __future__ import annotations

from typing import Any

from ..ml import decision_ledger
from . import memory, tradingagents_adapter
from .reflection import _realized_direction
from .schemas import ACTION_HOLD

_QUANT_DECISION_TO_ACTION = {"ELIGIBLE": "BUY", "WEAK": "HOLD", "NOT_ELIGIBLE": "SELL"}


def _accuracy_against_resolved(rows: list[dict[str, Any]], ledger_db) -> dict[str, Any]:
    correct = 0
    resolved = 0
    for row in rows:
        try:
            ledger_rows = decision_ledger.query_decisions(ledger_db, ticker=row["ticker"], only_with_outcome=True)
        except Exception:  # noqa: BLE001 - a comparison report must never crash on a bad row
            continue
        matching = [r for r in ledger_rows if r.get("report_date") == row["report_date"]]
        if not matching or row["action"] == ACTION_HOLD:
            continue
        direction = _realized_direction(matching[0].get("pnl_pct"))
        if direction is None:
            continue
        resolved += 1
        if row["action"] == direction:
            correct += 1

    return {"resolved": resolved, "correct": correct, "accuracy_pct": round(100.0 * correct / resolved, 1) if resolved else None}


def compare_three_way(config: dict[str, Any], since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Returns a structured comparison across all three sources. Any
    source with zero recorded assessments reports `None` for its stats
    rather than a misleading zero - never fabricated."""
    ledger_db = decision_ledger.resolve_db_path(config)

    deterministic_rows = memory.query_assessments(memory.resolve_db_path(config), since=since, until=until)
    upstream_rows = memory.query_assessments(tradingagents_adapter.resolve_memory_db_path(config), since=since, until=until)

    deterministic_stats = _accuracy_against_resolved(deterministic_rows, ledger_db)
    upstream_stats = _accuracy_against_resolved(upstream_rows, ledger_db)

    upstream_durations = [r["duration_ms"] for r in upstream_rows if r.get("duration_ms") is not None]
    deterministic_durations = [r["duration_ms"] for r in deterministic_rows if r.get("duration_ms") is not None]

    agreement = 0
    compared = 0
    by_key_deterministic = {(r["ticker"], r["report_date"]): r["action"] for r in deterministic_rows}
    for row in upstream_rows:
        key = (row["ticker"], row["report_date"])
        if key in by_key_deterministic:
            compared += 1
            if by_key_deterministic[key] == row["action"]:
                agreement += 1

    quant_compared = [r for r in upstream_rows if r.get("quant_agent_decision") in _QUANT_DECISION_TO_ACTION]
    quant_agreement = sum(1 for r in quant_compared if _QUANT_DECISION_TO_ACTION[r["quant_agent_decision"]] == r["action"])

    return {
        "upstream_vs_quant_agent": {
            "compared": len(quant_compared), "agreement_count": quant_agreement,
            "agreement_pct": round(100.0 * quant_agreement / len(quant_compared), 1) if quant_compared else None,
        },
        "deterministic_engine": {
            "total_assessments": len(deterministic_rows), **deterministic_stats,
            "avg_duration_ms": round(sum(deterministic_durations) / len(deterministic_durations), 2) if deterministic_durations else None,
        },
        "upstream_tradingagents": {
            "total_assessments": len(upstream_rows), **upstream_stats,
            "avg_duration_ms": round(sum(upstream_durations) / len(upstream_durations), 2) if upstream_durations else None,
            "note": "Real per-call dollar cost is not tracked (no token-accounting callback wired to any provider) - only latency and call count.",
        },
        "agreement_between_deterministic_and_upstream": {
            "compared": compared, "agreement_count": agreement,
            "agreement_pct": round(100.0 * agreement / compared, 1) if compared else None,
        },
    }


def format_three_way_report(summary: dict[str, Any]) -> str:
    d = summary["deterministic_engine"]
    u = summary["upstream_tradingagents"]
    a = summary["agreement_between_deterministic_and_upstream"]

    lines = ["🧪 Three-way research comparison (SHADOW MODE - no influence on trading)", ""]
    lines.append(f"Deterministic engine: {d['total_assessments']} assessment(s), accuracy {d['accuracy_pct']}% on {d['resolved']} resolved trade(s), avg latency {d['avg_duration_ms']}ms.")
    lines.append(f"Upstream TradingAgents: {u['total_assessments']} assessment(s), accuracy {u['accuracy_pct']}% on {u['resolved']} resolved trade(s), avg latency {u['avg_duration_ms']}ms.")
    lines.append(f"({u['note']})")
    if a["compared"]:
        lines.append(f"\nAgreement between the two research layers: {a['agreement_count']}/{a['compared']} ({a['agreement_pct']}%).")
    else:
        lines.append("\nNo overlapping ticker/date pairs between the two layers yet to compare agreement.")

    q = summary["upstream_vs_quant_agent"]
    if q["compared"]:
        lines.append(f"Upstream TradingAgents vs. existing quant_agent: {q['agreement_count']}/{q['compared']} ({q['agreement_pct']}%) agreement.")
    lines.append(
        "\nNeither layer has influenced any PAPER trading decision. This report is informational only - "
        "see GitHub Issue #1 for the forward-shadow validation this is building evidence toward."
    )
    return "\n".join(lines)
