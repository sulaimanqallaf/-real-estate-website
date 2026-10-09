"""REPLAY mode: replays HISTORICAL events from the real decision ledger
- same event mapping as LIVE (`decision_events.events_for_decision_row`),
just paced out from an already-complete, bounded set of rows instead of
polling for new ones. Never invents a row; stops (emits one final
`"info"` event) once every historical row has been replayed."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .. import readonly
from ..events import AgentEvent, make_event
from .decision_events import events_for_decision_row

DEFAULT_STEP_DELAY_SECONDS = 0.6


async def replay_events(
    since: str | None = None, until: str | None = None, step_delay_seconds: float = DEFAULT_STEP_DELAY_SECONDS
) -> AsyncIterator[AgentEvent]:
    from src.ml import decision_ledger  # local import - keeps readonly.py the single integration seam for everything else

    config = readonly.get_config()
    db_path = decision_ledger.resolve_db_path(config)
    rows = decision_ledger.query_decisions(db_path, since=since, until=until)

    if not rows:
        yield make_event("replay", "info", "No historical decisions found for this range.")
        return

    for row in rows:
        for event in events_for_decision_row(row, mode="replay"):
            yield event
            await asyncio.sleep(step_delay_seconds)

    yield make_event("replay", "info", f"Replay finished - {len(rows)} historical decisions replayed.")
