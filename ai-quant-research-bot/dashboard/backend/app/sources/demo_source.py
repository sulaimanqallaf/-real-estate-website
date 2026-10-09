"""DEMO mode: mock events, allowed ONLY here (never in LIVE/REPLAY - see
`main.py`'s mode gate, which additionally requires `DASHBOARD_ALLOW_
DEMO=1` before this source can even be selected, so a production
deployment never serves synthetic data by accident). Every event this
emits carries `mode="demo"` so the frontend can render the mandatory
"DEMO - SYNTHETIC DATA" banner - see `dashboard/README.md`."""

from __future__ import annotations

import asyncio
import itertools
import random
from typing import AsyncIterator

from ..events import AgentEvent, make_event

_DEMO_TICKERS = ["DEMO1", "DEMO2", "DEMO3"]

_SCRIPT: list[tuple[str, str, str, str]] = [
    # (event_type, agent, zone, summary_template)
    ("scan", "market_scout", "scout_desk", "Market Scout scanning {ticker} (synthetic)"),
    ("debate", "bull_analyst", "debate_room", "Bull case for {ticker} (synthetic)"),
    ("debate", "bear_analyst", "debate_room", "Bear case for {ticker} (synthetic)"),
    ("risk_check", "risk_officer", "risk_desk", "Risk check for {ticker} (synthetic)"),
    ("decision", "chief_manager", "chief_office", "Decision for {ticker} (synthetic)"),
    ("info", "strategy_scientist", "strategy_desk", "Strategy note for {ticker} (synthetic)"),
    ("execution", "execution_agent", "execution_desk", "Simulated paper fill for {ticker} (synthetic, NOT a real trade)"),
    ("learning", "learning_agent", "learning_desk", "Reflection logged for {ticker} (synthetic)"),
]


async def demo_events(step_delay_seconds: float = 0.6) -> AsyncIterator[AgentEvent]:
    yield make_event(
        "demo", "info",
        "DEMO MODE: all events below are synthetic - no real decisions, trades, or P&L.",
    )
    tickers = itertools.cycle(_DEMO_TICKERS)
    while True:
        ticker = next(tickers)
        for event_type, agent, zone, template in _SCRIPT:
            yield make_event(
                "demo", event_type, template.format(ticker=ticker),  # type: ignore[arg-type]
                agent=agent, zone=zone, ticker=ticker,  # type: ignore[arg-type]
                data={"synthetic_score": random.randint(1, 100)},
            )
            await asyncio.sleep(step_delay_seconds)
