"""LIVE mode: real backend events ONLY. Polls the real decision ledger
for rows newer than the last one already emitted and plays each new
row's real event sequence (`decision_events.events_for_decision_row()`)
with a short stagger between events so the frontend can animate
movement - never fabricates a row, never invents one when nothing new
has happened. Also emits a periodic `"health"` snapshot (real `run_
health.build_health_report()`) so the health/spend/staleness panels stay
current even on a quiet day with no new decisions.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .. import readonly
from ..events import AgentEvent, make_event
from .decision_events import events_for_decision_row

POLL_INTERVAL_SECONDS = 5.0
HEALTH_SNAPSHOT_INTERVAL_SECONDS = 30.0
STEP_DELAY_SECONDS = 0.6


async def live_events() -> AsyncIterator[AgentEvent]:
    last_as_of: str | None = None
    ticks_since_health = 0
    health_ticks = max(1, int(HEALTH_SNAPSHOT_INTERVAL_SECONDS / POLL_INTERVAL_SECONDS))

    while True:
        rows = readonly.recent_decisions(limit=200)
        new_rows = [r for r in rows if last_as_of is None or (r.get("as_of") or "") > last_as_of]
        for row in new_rows:
            for event in events_for_decision_row(row, mode="live"):
                yield event
                await asyncio.sleep(STEP_DELAY_SECONDS)
            last_as_of = row.get("as_of") or last_as_of

        ticks_since_health += 1
        if ticks_since_health >= health_ticks:
            ticks_since_health = 0
            report = readonly.health_report()
            yield make_event(
                "live", "health",
                f"Health snapshot: last run ok={report['status']['last_run_ok']}",
                data={"health": report},
            )

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
