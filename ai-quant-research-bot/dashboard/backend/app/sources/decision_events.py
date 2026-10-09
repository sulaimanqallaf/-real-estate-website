"""Shared mapping: one real `decision_ledger` row -> the ordered
sequence of `AgentEvent`s that visualizes it (used identically by LIVE
and REPLAY - the whole point is that replaying history looks exactly
like watching it happen live). Pure function of its input row - no I/O,
no randomness, so the same row always produces the same event sequence
(this is also what makes REPLAY mode deterministic and testable).

Read-only: this only ever interprets data a row already contains
(decision/strategy/outcome_status/etc, written by `src.main`'s existing
pipeline) - it never infers, fabricates, or upgrades a row's actual
content.
"""

from __future__ import annotations

from typing import Any

from ..events import AgentEvent, Mode, make_event


def events_for_decision_row(row: dict[str, Any], mode: Mode) -> list[AgentEvent]:
    ticker = row.get("ticker")
    strategy = row.get("strategy") or "unknown strategy"
    decision = row.get("decision") or "UNKNOWN"
    regime = row.get("regime")
    score = row.get("signal_score")
    outcome_status = row.get("outcome_status")

    events: list[AgentEvent] = []

    events.append(
        make_event(
            mode, "scan", f"Market Scout evaluated {ticker} ({strategy}, score={score})",
            agent="market_scout", zone="scout_desk", ticker=ticker,
            data={"strategy": strategy, "signal_score": score},
        )
    )

    # A real debate only happened if the deterministic/upstream research
    # layer actually produced provenance for this candidate - `provenance`
    # is whatever main.py recorded; absence means no debate content
    # exists for this row, so no debate event is fabricated for it.
    if row.get("provenance"):
        events.append(
            make_event(
                mode, "debate", f"Bull/Bear research reviewed {ticker}",
                agent="bull_analyst", zone="debate_room", ticker=ticker,
                data={"provenance": row.get("provenance")},
            )
        )
        events.append(
            make_event(
                mode, "debate", f"Bear case reviewed for {ticker}",
                agent="bear_analyst", zone="debate_room", ticker=ticker,
            )
        )

    events.append(
        make_event(
            mode, "risk_check", f"Risk/regime check for {ticker} (regime={regime})",
            agent="risk_officer", zone="risk_desk", ticker=ticker,
            data={"regime": regime, "dollar_risk": row.get("dollar_risk")},
        )
    )

    events.append(
        make_event(
            mode, "decision", f"Chief AI Manager classified {ticker}: {decision}",
            agent="chief_manager", zone="chief_office", ticker=ticker,
            data={"decision": decision, "decision_reasons": row.get("decision_reasons")},
        )
    )

    events.append(
        make_event(
            mode, "info", f"Strategy Scientist noted {strategy} for {ticker}",
            agent="strategy_scientist", zone="strategy_desk", ticker=ticker,
        )
    )

    if row.get("trade_id"):
        events.append(
            make_event(
                mode, "execution", f"Paper trade recorded for {ticker} (trade_id={row['trade_id']})",
                agent="execution_agent", zone="execution_desk", ticker=ticker,
                data={"trade_id": row["trade_id"]},
            )
        )

    if outcome_status:
        events.append(
            make_event(
                mode, "learning", f"Outcome recorded for {ticker}: {outcome_status}",
                agent="learning_agent", zone="learning_desk", ticker=ticker,
                data={"outcome_status": outcome_status, "pnl_pct": row.get("pnl_pct")},
            )
        )

    return events
