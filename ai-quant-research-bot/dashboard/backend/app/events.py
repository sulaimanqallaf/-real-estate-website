"""The AI Agents Dashboard's event schema - the one contract the
frontend's PixiJS scene and the three backend modes (LIVE/REPLAY/DEMO)
all agree on. See `dashboard/README.md` for the documented schema and
field-by-field meaning; this module is the schema's single source of
truth (a `pydantic` model, so every event emitted anywhere is validated
against it before it ever reaches a WebSocket client).

**Read-only, no side effects.** This module defines data shapes only -
no network, no file I/O, no import of anything execution/broker/LLM-
facing. See `tests/test_readonly_safety.py`'s grep-based guardrail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# The 8 Phase 1 MVP agents - fixed set, matches the frontend's
# `agentConfig.ts` exactly (kept in sync by hand; `tests/test_events_
# schema.py` pins this exact list so a drift between the two is caught
# immediately rather than silently breaking the animation).
AgentId = Literal[
    "market_scout",
    "bull_analyst",
    "bear_analyst",
    "chief_manager",
    "risk_officer",
    "strategy_scientist",
    "execution_agent",
    "learning_agent",
]

ALL_AGENT_IDS: tuple[AgentId, ...] = (
    "market_scout",
    "bull_analyst",
    "bear_analyst",
    "chief_manager",
    "risk_officer",
    "strategy_scientist",
    "execution_agent",
    "learning_agent",
)

# One "zone" (desk/room) per agent's home position, plus a shared
# "debate_room" that bull_analyst/bear_analyst both move into for a
# debate event. The frontend's PixiJS scene places a desk/room sprite at
# each of these and tweens an agent's position to it on a matching event.
ZoneId = Literal[
    "scout_desk",
    "debate_room",
    "chief_office",
    "risk_desk",
    "strategy_desk",
    "execution_desk",
    "learning_desk",
]

EventType = Literal[
    "scan",       # Market Scout found/evaluated a candidate
    "debate",     # Bull/Bear (deterministic or real TradingAgents) debate content available
    "risk_check", # Portfolio/regime risk evaluation ran
    "decision",   # Chief AI Manager's classification (AUTO_EXECUTE/REQUIRE_APPROVAL/WATCH_ONLY/REJECT)
    "execution",  # A paper trade was actually recorded (never a real/live order - see dashboard/README.md)
    "learning",   # An outcome/reflection was recorded for a previously-made decision
    "health",     # A periodic health/status snapshot (run status, spend, staleness)
    "info",       # Anything else worth showing in the timeline, no agent movement implied
]

Mode = Literal["live", "replay", "demo"]


class AgentEvent(BaseModel):
    """One event on the WebSocket stream. `agent`/`zone` are `None` for
    an event with no single-agent visual (e.g. a general `"health"`
    snapshot) - the frontend must not move anything when both are
    `None`, never guess a default agent/zone."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mode: Mode
    event_type: EventType
    agent: AgentId | None = None
    zone: ZoneId | None = None
    ticker: str | None = None
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


def make_event(
    mode: Mode,
    event_type: EventType,
    summary: str,
    agent: AgentId | None = None,
    zone: ZoneId | None = None,
    ticker: str | None = None,
    data: dict[str, Any] | None = None,
) -> AgentEvent:
    return AgentEvent(
        mode=mode, event_type=event_type, agent=agent, zone=zone, ticker=ticker,
        summary=summary, data=data or {},
    )
